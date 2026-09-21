"""P2.1/P2.2 — durable learner state, BKT mastery, and FSRS scheduling.

This is the persistence and tracing layer the P1.3 card (learner_events.py)
left as "P2": the in-memory ``EventLog`` state is now

- **persistent** — a JSON document per subject next to the spec
  (``out/curriculum/<subject>.learnerstate.json`` by convention), written
  **atomically** (temp file in the same directory + ``os.replace``), so a
  crash mid-write can never leave a torn or empty state file;
- **traced** — mastery per node is a deterministic BKT fold over verified T4
  ``assessment`` outcomes, never a raw sum or a tutor groundedness flag;
- **auditable** — the full event log (the durable interaction/event records)
  is retained in the state document and is the source of truth;
- **safe** — a process-wide lock + a cross-process file lock make concurrent
  writers correct (last-writer-wins per event, never lost), and subject/spec
  compatibility is enforced on load and on every write.

## Why BKT (documented choice, per the card)

The card asks to choose and document a defensible tracer, keeping FSRS
scheduling separate for P2.2. We choose **BKT (Bayesian Knowledge Tracing,
Corbett & Anderson, 1994)** — the canonical model the card names first —
because it is the only one of the candidates that satisfies *all* of the
PoC invariants at once:

- **Deterministic, non-LLM, no learning step.** With the four parameters
  fixed (the paper's standard starting values), the update is a pure,
  closed-form function of ``(p_t, observation)``. No sampling, no clock, no
  gradient, no model to load. Two states fed the same event sequence are
  bit-identical.
- **Bounded in ``[0, 1]``** by construction, so "mastery" is a comparable,
  testable quantity across subjects and restarts.
- **Monotone in evidence**: a verified (grounded) response raises mastery;
  an unverified response is *not a learning signal at all* (below).

Why **not EKT**: the published EKT (Settle et al., AIED 2023) predicts the
per-response strengths ``a_t, b_t`` and the exponent ``β`` with a neural
network. That is a *learning* step and a model dependency — it conflicts
directly with the deterministic/non-LLM invariant this repo is built on.
BKT with fixed parameters is the closed-form, dependency-free member of the
same family, and is what the card lists first.

### Signal mapping (documented, deterministic)

Assessment events carry the deterministic T4 verdict. The tracer maps it to an
observation:

- ``verdict == correct`` -> a correct observation -> BKT learning step.
- ``verdict == incorrect`` -> an incorrect observation -> BKT update.
- ``partial``, ``flagged``, ``degraded``, and ``not-scored`` -> no signal.
  A partial or unsupported response is not silently promoted to mastery.

Misconceptions remain **first-class state** (``misconceptions_triggered``)
and feed the tutor's scaffolded hints (P2.2); their P1.3 delta accounting
(negative) is preserved verbatim in the event log for audit. The mastery
*scalar* is the BKT trace over verified learning opportunities.

## Subject/spec version compatibility

The state document is bound to the **subject** it was created for and to the
**schema version** it was written in. On load:

- a state file for a **different subject** is rejected (``subject-mismatch``)
  — the state is per-subject and must never be applied to another spec;
- a **future** ``schema_version`` (lexically higher than this reader knows)
  is rejected (``version-too-new``) — never silently misread;
- a **past** version (lower) is **migrated** by the registered migrators
  (v0 -> v1); an unknown past version is rejected (``version-unknown``).

Spec compatibility is enforced on every write: every node id present in the
state must exist in the bound spec (``node-unknown``), and the state's
subject must equal the spec's subject. This is the same anchor the P1.3
contract uses (no free-form id may touch state).

## Atomic writes, restart safety, concurrency

- ``LearnerStateStore.save()`` serialises to a temp file in the same
  directory and ``os.replace``'s it over the target. Readers either see the
  old complete file or the new complete file — never a partial write.
- ``load()`` of a corrupt/empty file raises a named ``ValueError`` (a
  first-class result the caller reports — never a silent crash, never a
  silent drop).
- ``record()`` / ``record_events()`` run load-modify-save under a
  process-wide ``threading.Lock`` keyed by path **and** an exclusive
  ``fcntl.flock`` on a sidecar ``<path>.lock`` file, so a second *process*
  cannot interleave.
- Replays of an already-recorded event are a no-op (P1.3 idempotency) and do
  not rewrite the file, so concurrent duplicate writers converge to the same
  state.

## What this module deliberately does NOT do

- no LLM in the loop: every state change is a validated, deterministic
  function of the event stream and the spec;
- no automatic wall clock: all review and persistence timestamps are injected
  by the caller.

Design invariants (inherited from the PoC, non-negotiable):
- **Deterministic, non-LLM.** Two states fed the same event sequence are
  identical (timestamps are the only time-derived fields and are
  caller-supplied, never read from the clock inside the state).
- **No silent failures.** Rejections carry named issues; a corrupt file is a
  first-class load error, not an exception the caller can miss.
- **Local-first, no cloud.** Pure stdlib (``json``, ``os``, ``fcntl``,
  ``threading``); no new dependencies.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import math
import os
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Callable

from . import fsrs as fsrs_mod
from . import learner_events as le
from .spec import CurriculumSpec

# ---------- state-document schema versioning ----------------------------------
STATE_SCHEMA_VERSION = "1"
_KNOWN_STATE_VERSIONS = ("0", "1")   # "0" = P1.3 dump, migratable; "1" = this


# ---------- BKT tracer (deterministic, closed-form, no learning) --------------
# BKT (Corbett & Anderson 1994), four fixed parameters (the paper's standard
# starting values). With these fixed there is no learning step: the update is
# a pure function of (p_t, observation). Bounded in [0, 1] by construction.
BKT_P_INIT = 0.0    # P(L_0): no mastery before any evidence
BKT_P_T = 0.2       # P(T): probability of learning per opportunity
BKT_P_S = 0.1       # P(S): probability of slipping (wrong despite knowing)
BKT_P_G = 0.2       # P(G): probability of guessing (right despite not knowing)

# ---------- P2.2: prerequisite gating (deterministic threshold) ---------------
# A prerequisite node counts as "mastered enough to unlock its dependents"
# once its BKT-traced mastery clears this threshold. 0.7 is the standard
# "competent, not just familiar" bar; it is a documented, fixed constant
# (not a learned parameter) so gating is a pure function of the trace.
GATE_THRESHOLD = 0.7


def _probability(value: Any, name: str = "mastery") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name}-invalid: value must be finite in [0, 1]")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name}-invalid: value must be finite in [0, 1]") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name}-invalid: value must be finite in [0, 1]")
    return result


def bkt_update(p: float, correct: bool) -> float:
    """One BKT step: posterior over skill, then the learning transition.

    Deterministic and bounded in ``[0, 1]``. A correct observation raises
    mastery; an incorrect one lowers the posterior (the learning transition
    then applies, the standard BKT "each response is a learning opportunity"
    model).
    """
    p = _probability(p)
    if correct:
        # P(L | correct) with P(correct|L)=1-P(S), P(correct|¬L)=P(G)
        num = (1.0 - BKT_P_S) * p
        den = (1.0 - BKT_P_S) * p + BKT_P_G * (1.0 - p)
    else:
        # P(L | incorrect) with P(incorrect|L)=P(S), P(incorrect|¬L)=1-P(G)
        num = BKT_P_S * p
        den = BKT_P_S * p + (1.0 - BKT_P_G) * (1.0 - p)
    p_l = num / den
    # learning transition: P(L_{t+1}) = P(L_t|obs) + (1 - P(L_t|obs)) * P(T)
    p_next = p_l + (1.0 - p_l) * BKT_P_T
    return max(0.0, min(p_next, 1.0))


def classify_reason(reason: str) -> str:
    """Map a P1.3 rule-reason string to a BKT observation.

    - ``grounded-attempt`` present   -> ``correct``    (gate-certified)
    - ``unverified-attempt`` present -> ``no-signal``  (honesty: never credits)
    - only ``misconception-triggered`` -> ``incorrect`` (defensive branch)
    - anything else                  -> ``no-signal``  (unknown rules are
                                                         first-class, not guessed)
    """
    if le.RULE_GROUNDED_ATTEMPT in reason:
        return "correct"
    if le.RULE_UNVERIFIED_ATTEMPT in reason:
        return "no-signal"
    if le.RULE_MISCONCEPTION_TRIGGERED in reason:
        return "incorrect"
    return "no-signal"


def trace_mastery(events: list[dict[str, Any]], initial_p: float = BKT_P_INIT) -> float:
    """Fold a P1.3 event stream into a mastery probability via BKT.

    Only verified ``assessment`` events carry the signal. Deterministic: the
    same stream in the same order -> the same mastery, exactly (no clock, no
    randomness). Tutor interactions and unverified assessments contribute
    nothing.
    """
    p = _probability(initial_p)
    for e in events:
        if e.get("kind") != le.KIND_ASSESSMENT:
            continue
        verdict = e.get("verdict")
        # Only a scored T4 result is a knowledge signal.  Partial, flagged,
        # degraded, and not-scored outcomes are intentionally not converted
        # into a full mastery claim.
        if verdict == "correct":
            p = bkt_update(p, True)
        elif verdict == "incorrect":
            p = bkt_update(p, False)
        # Everything else is no-signal.  In particular, a grounded tutor
        # answer is an explanation, not an assessment outcome.
    return _probability(p)


# ---------- the durable state document (v1) -----------------------------------
@dataclass
class LearnerState:
    """The per-subject durable learner state (P2.1).

    Fields mirror ``docs/DATA-MODEL.md`` §2 and extend it with the event log
    (the durable interaction records), the tracer provenance, and the P1.3
    rejection audit trail. ``next_review`` is present for the data model but
    stays ``None`` — FSRS scheduling is P2.2 and is not smuggled in here.
    """
    schema_version: str = STATE_SCHEMA_VERSION
    tracer: str = "bkt"
    subject: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    # The full durable event records (P1.3 canonical payloads, in recorded
    # order). This is the source of truth; the per-node projections are
    # derived from it.
    events: list[dict[str, Any]] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    misconceptions_triggered: dict[str, list[str]] = field(default_factory=dict)
    mastery: dict[str, float] = field(default_factory=dict)
    # (P2.2) Per-node FSRS memory state — the durable review-scheduling
    # projection. Keyed by node id; value is the ``MemoryState.to_dict()``
    # (stability, difficulty, reviews, last_review, next_review). Derived
    # from the ``review`` events in ``self.events`` (the source of truth);
    # absent / empty for nodes that have never been reviewed.
    schedules: dict[str, dict[str, Any]] = field(default_factory=dict)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    # The only time-derived fields; caller-supplied at save() time (the state
    # never reads the clock itself — determinism), ISO-8601 UTC.
    created_at: str | None = None
    last_updated: str | None = None

    # ---- construction from a P1.3 EventLog ----------------------------------
    @classmethod
    def from_log(cls, log: le.EventLog, now: str) -> LearnerState:
        """Build a v1 state from a (possibly replayed) P1.3 ``EventLog``.

        Deterministic given the same log + same ``now``: the event order is
        the log's order, the mastery is the BKT fold over that order, and the
        per-node projections are pure functions of the events.
        """
        state = cls(
            schema_version=STATE_SCHEMA_VERSION,
            tracer="bkt",
            subject=log.spec.subject,
            events=[dict(e) for e in log.entries],
            attempts=dict(log.attempts),
            misconceptions_triggered={k: list(v)
                                      for k, v in log.misconceptions.items()},
            rejections=[dict(r) for r in log.rejections],
            created_at=now,
            last_updated=now,
        )
        state._rebuild_from_events()
        return state

    def _rebuild_from_events(self) -> None:
        """Rebuild every derived projection from the durable event list."""
        attempts: dict[str, int] = {}
        misconceptions: dict[str, list[str]] = {}
        for event in self.events:
            node_id = event.get("node_id")
            if event.get("kind") == le.KIND_ATTEMPT:
                attempts[node_id] = attempts.get(node_id, 0) + 1
            elif event.get("kind") == le.KIND_MISCONCEPTION:
                misconception_id = event.get("misconception_id")
                ids = misconceptions.setdefault(node_id, [])
                if misconception_id not in ids:
                    ids.append(misconception_id)
        self.attempts = attempts
        self.misconceptions_triggered = misconceptions
        self._recompute_mastery()
        self._rebuild_schedules()
        self._rebuild_nodes()

    def _recompute_mastery(self) -> None:
        """Mastery = BKT fold over the event stream, per node. Pure."""
        self.mastery = {}
        for node_id in self._node_ids():
            stream = [e for e in self.events if e.get("node_id") == node_id]
            self.mastery[node_id] = round(trace_mastery(stream), 6)

    def _rebuild_schedules(self) -> None:
        """(P2.2) Derive the per-node FSRS memory state from the ``review``
        events (the source of truth). Pure: same events -> same schedules,
        byte for byte. Nodes with no ``review`` events are absent from the
        projection (the honest "never scheduled").
        """
        self.schedules = {}
        for node_id in self._node_ids():
            stream = [
                {"grade": e.get("grade"), "review_time": e.get("review_time")}
                for e in self.events
                if e.get("kind") == "review" and e.get("node_id") == node_id
            ]
            if not stream:
                continue
            final = fsrs_mod.replay_reviews(stream)
            if final is not None:
                self.schedules[node_id] = final.to_dict()

    def _rebuild_nodes(self) -> None:
        """The per-node projection list (the data-model ``nodes`` section)."""
        self.nodes = [{
            "id": node_id,
            "mastery": self.mastery.get(node_id, 0.0),
            "attempts": self.attempts.get(node_id, 0),
            "misconceptions_triggered": list(
                self.misconceptions_triggered.get(node_id, [])),
            # (P2.2) the FSRS due time, when the node has been reviewed;
            # honest ``None`` (never scheduled) when it has not.
            "next_review": (
                self.schedules.get(node_id, {}).get("next_review")
                if node_id in self.schedules else None),
        } for node_id in self._node_ids()]

    def _node_ids(self) -> list[str]:
        # The event list is the source of truth.  Projection-only ids are
        # deliberately excluded so tampered attempts/mastery maps cannot
        # become durable learner state.
        ids = {e.get("node_id") for e in self.events if e.get("node_id")}
        return sorted(ids)

    # ---- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tracer": self.tracer,
            "subject": self.subject,
            "nodes": copy.deepcopy(self.nodes),
            "events": copy.deepcopy(self.events),
            "attempts": dict(self.attempts),
            "misconceptions_triggered": {k: list(v)
                                         for k, v in self.misconceptions_triggered.items()},
            "mastery": dict(self.mastery),
            "schedules": copy.deepcopy(self.schedules),
            "rejections": copy.deepcopy(self.rejections),
            "created_at": self.created_at,
            "last_updated": self.last_updated,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False,
                          ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LearnerState:
        d = dict(d)
        for key, default in (
            ("schema_version", "1"), ("tracer", "bkt"), ("subject", ""),
            ("nodes", []), ("events", []), ("attempts", {}),
            ("misconceptions_triggered", {}), ("mastery", {}),
            ("schedules", {}),
            ("rejections", []), ("created_at", None), ("last_updated", None),
        ):
            if key not in d:
                d[key] = copy.deepcopy(default)
        return cls(**d)

    # ---- compatibility (subject + schema version + spec) ---------------------
    def check_compatible(self, spec: CurriculumSpec) -> list[str]:
        """Compatibility issues (empty list = compatible).

        - ``subject-mismatch``: the state belongs to another subject.
        - ``node-unknown``: a node in the state is not in the bound spec.
        The event payloads were already spec-validated on record (P1.3);
        this is the document-level gate on load/save.
        """
        issues: list[str] = []
        if self.schema_version != STATE_SCHEMA_VERSION:
            issues.append(f"version-unknown: {self.schema_version!r}")
        if self.tracer != "bkt":
            issues.append(f"tracer-unknown: {self.tracer!r}")
        if self.subject != spec.subject:
            issues.append(f"subject-mismatch: state subject {self.subject!r} != "
                          f"spec subject {spec.subject!r}")
        for name in ("created_at", "last_updated"):
            value = getattr(self, name)
            if value is not None:
                try:
                    fsrs_mod._parse_timestamp(value, name)
                except (TypeError, ValueError, fsrs_mod.FSRSValueError) as exc:
                    issues.append(f"timestamp-invalid: {name}: {exc}")
        known = {n.id for n in spec.nodes}
        node_ids: set[str] = set()
        if not isinstance(self.events, list):
            issues.append("events-invalid: events must be a list")
            return issues
        if not isinstance(self.attempts, dict):
            issues.append("attempts-invalid: attempts must be a mapping")
        if not isinstance(self.mastery, dict):
            issues.append("mastery-invalid: mastery must be a mapping")
        if not isinstance(self.misconceptions_triggered, dict):
            issues.append("misconceptions-invalid: misconceptions must be a mapping")
        if not isinstance(self.schedules, dict):
            issues.append("schedules-invalid: schedules must be a mapping")
            return issues
        if not isinstance(self.nodes, list):
            issues.append("nodes-invalid: nodes must be a list")
        for n in self.nodes if isinstance(self.nodes, list) else []:
            if isinstance(n, dict) and isinstance(n.get("id"), str):
                node_ids.add(n["id"])
        for e in self.events:
            if isinstance(e, dict) and isinstance(e.get("node_id"), str):
                node_ids.add(e["node_id"])
            elif not isinstance(e, dict):
                issues.append("event-invalid: durable event is not a mapping")
        if isinstance(self.attempts, dict):
            node_ids |= {k for k in self.attempts if isinstance(k, str)}
        if isinstance(self.mastery, dict):
            node_ids |= {k for k in self.mastery if isinstance(k, str)}
        if isinstance(self.misconceptions_triggered, dict):
            node_ids |= {k for k in self.misconceptions_triggered if isinstance(k, str)}
        node_ids |= {k for k in self.schedules if isinstance(k, str)}
        for nid in sorted(node_ids):
            if nid not in known:
                issues.append(f"node-unknown: {nid!r} is not in this spec")
        for e in self.events:
            if not isinstance(e, dict):
                continue
            ok, event_issues = le.validate_event(e, spec)
            if not ok:
                issues.extend(f"event-invalid: {issue}" for issue in event_issues)
            elif e.get("event_id") != le.event_id(le.canonical_payload(e)):
                issues.append("event-invalid: event_id does not match payload")
        event_ids = [e.get("event_id") for e in self.events
                     if isinstance(e, dict) and isinstance(e.get("event_id"), str)]
        if len(event_ids) != len(set(event_ids)):
            issues.append("event-invalid: duplicate event_id in durable events")
        for node_id, schedule in self.schedules.items():
            if not isinstance(schedule, dict):
                issues.append(f"schedule-invalid: {node_id!r} is not a mapping")
                continue
            try:
                fsrs_mod.MemoryState.from_dict(schedule)
            except (TypeError, ValueError, fsrs_mod.FSRSValueError) as exc:
                issues.append(f"schedule-invalid: {node_id!r}: {exc}")
        return issues


# ---------- P2.2: prerequisite gating (deterministic, pure) --------------------
def prereq_met(node, mastery: dict[str, float],
               threshold: float = GATE_THRESHOLD) -> bool:
    """Whether ``node``'s prerequisites are all mastered enough.

    A node with **no** prerequisites is met by definition (nothing to
    satisfy). Otherwise **every** prerequisite must have BKT-traced mastery
    ``>= threshold``. A prerequisite missing from ``mastery`` counts as 0.0
    (no evidence) — never as met. Pure function of (node, mastery, threshold).
    """
    threshold = _validate_threshold(threshold)
    for value in mastery.values():
        _probability(value)
    prereqs = list(node.prereqs)
    if not prereqs:
        return True
    for p in prereqs:
        if mastery.get(p, 0.0) < threshold:
            return False
    return True


def gated_nodes(spec: CurriculumSpec, mastery: dict[str, float],
                threshold: float = GATE_THRESHOLD) -> list[str]:
    """The node ids that are currently **locked** — at least one prerequisite
    below ``threshold``. Sorted for determinism. A node is gated only by its
    **direct** prerequisites (transitive gating falls out naturally: an
    ancestor below bar keeps its dependent gated)."""
    return sorted(n.id for n in spec.nodes if not prereq_met(n, mastery, threshold))


def unlocked_nodes(spec: CurriculumSpec, mastery: dict[str, float],
                   threshold: float = GATE_THRESHOLD) -> list[str]:
    """The node ids that are currently **available** — all prerequisites met
    (or none). The complement of :func:`gated_nodes`, sorted."""
    gated = set(gated_nodes(spec, mastery, threshold))
    return sorted(n.id for n in spec.nodes if n.id not in gated)


def _mastery_mapping(state: LearnerState | dict[str, Any] | None) -> dict[str, float]:
    if state is None:
        return {}
    if isinstance(state, LearnerState):
        raw = dict(state.mastery)
        result = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                raise TypeError("mastery-invalid: keys must be strings")
            result[key] = _probability(value)
        return result
    if isinstance(state, dict):
        raw = state.get("mastery")
        if isinstance(raw, dict):
            result = {}
            for key, value in raw.items():
                if not isinstance(key, str):
                    raise TypeError("mastery-invalid: keys must be strings")
                result[key] = _probability(value)
            return result
        nodes = state.get("nodes")
        if isinstance(nodes, dict):
            result = {}
            for key, value in nodes.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    raise TypeError("mastery-invalid: node projection is malformed")
                result[key] = _probability(value.get("mastery", 0.0))
            return result
        if isinstance(nodes, list):
            result = {}
            for value in nodes:
                if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                    raise TypeError("mastery-invalid: node projection is malformed")
                result[value["id"]] = _probability(value.get("mastery", 0.0))
            return result
    return {}


def _validate_threshold(threshold: float) -> float:
    return _probability(threshold, "gate-threshold")


def gate_report(spec: CurriculumSpec,
                state: LearnerState | dict[str, Any] | None,
                threshold: float = GATE_THRESHOLD) -> dict[str, dict[str, Any]]:
    """Return the deterministic prerequisite gate report for every node.

    ``unlocked`` is true when all direct prerequisites have traced mastery at
    least ``threshold``.  Missing mastery is zero.  The report is suitable for
    the API worker and never treats an unknown node or a tutor self-assessment
    as mastery.
    """
    threshold = _validate_threshold(threshold)
    mastery = _mastery_mapping(state)
    report: dict[str, dict[str, Any]] = {}
    for node in spec.nodes:
        blocking = [p for p in node.prereqs if mastery.get(p, 0.0) < threshold]
        report[node.id] = {
            "unlocked": not blocking,
            "blocking_prereqs": blocking,
        }
    return report


def due_nodes(spec: CurriculumSpec,
              state: LearnerState | dict[str, Any] | None,
              now: str | None = None) -> list[str]:
    """Return nodes needing review, in deterministic due order.

    A scheduled node is due when its stored ``next_review`` is on or before
    the supplied timestamp.  A validated misconception event is an immediate
    review request and is included even when its normal interval is not due.
    ``now=None`` performs no clock read and returns all scheduled nodes plus
    misconception-triggered nodes; callers needing a time-filtered queue must
    inject ``now``.
    """
    if state is None:
        return []
    if isinstance(state, LearnerState):
        schedules = state.schedules
        misconceptions = set(state.misconceptions_triggered)
    elif isinstance(state, dict):
        schedules = state.get("schedules") or {}
        misconceptions = set((state.get("misconceptions_triggered") or {}).keys())
    else:
        return []
    if not isinstance(schedules, dict):
        raise ValueError("schedule-invalid: schedules must be a mapping")  # noqa: TRY004
    cutoff = None if now is None else fsrs_mod._parse_timestamp(now, "now")
    due: list[tuple[int, str, str]] = []
    known = {node.id for node in spec.nodes}
    for node_id, raw in schedules.items():
        if node_id not in known:
            raise ValueError(f"node-unknown: {node_id!r} is not in this spec")
        memory = fsrs_mod.MemoryState.from_dict(raw)
        due_time = fsrs_mod._parse_timestamp(memory.next_review, "next-review")
        if cutoff is None or due_time <= cutoff:
            due.append((0, memory.next_review, node_id))
    for node_id in misconceptions:
        if node_id in known and not any(item[2] == node_id for item in due):
            due.append((-1, "", node_id))
    due.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[2] for item in due]


# ---------- migrations (past versions -> current) -------------------------------
def _migrate_v0_to_v1(d: dict[str, Any]) -> dict[str, Any]:
    """P1.3 ``EventLog.summary()`` dump (v0) -> v1 state document.

    v0 has: subject, schema_version, entries, attempts, misconceptions_triggered
    (node -> [ids]), mastery (raw rule-delta sums), rejections.
    v1 adds: the version bump, the tracer provenance, the per-node projection
    list, created/last_updated (v0 had none -> null), and **recomputes
    mastery** with BKT. The v0 raw sums are *not* a probability and are
    replaced by the traced value; the raw accounting is still fully
    recoverable from ``events``.
    """
    d = dict(d)
    d["schema_version"] = "1"
    d.setdefault("tracer", "bkt")
    events = list(d.pop("entries", d.get("events", [])))
    # P1.3 mastery deltas had no assessment provenance.  Preserve their event
    # records as an audit trail, but neutralize any non-zero value before the
    # v1 reader can use it: historical tutor groundedness is not a verified T4
    # outcome.  Recompute the derived id after this explicit migration.
    for event in events:
        if isinstance(event, dict) and event.get("kind") == le.KIND_MASTERY_DELTA:
            if event.get("delta") != 0.0:
                event["delta"] = 0.0
                event["reason"] = "legacy-unverified"
            event.pop("event_id", None)
            event["event_id"] = le.event_id(le.canonical_payload(event))
    d["events"] = events
    d.setdefault("nodes", [])
    d.setdefault("attempts", {})
    d.setdefault("misconceptions_triggered", {})
    d.setdefault("rejections", [])
    d.setdefault("created_at", None)
    d.setdefault("last_updated", None)
    state = LearnerState.from_dict(d)
    state._rebuild_from_events()   # BKT replaces the raw sums
    return state.to_dict()


_MIGRATORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "0": _migrate_v0_to_v1,
}


def _version_int(v: Any) -> int | None:
    """A version tag as an int, or ``None`` when it is not a bare integer.

    Our scheme is integer-versioned (``STATE_SCHEMA_VERSION = "1"``). A tag
    that is not a plain integer (``"1.5"``, ``"abc"``, ``1.0``) is not a
    member of this scheme, so it is *unknown* to us — not a version we can
    order against ours.
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.lstrip("-").isdigit():
        return int(v)
    return None


def migrate_state(d: dict[str, Any]) -> dict[str, Any]:
    """Bring a parsed state document up to ``STATE_SCHEMA_VERSION``.

    Raises ``ValueError`` with a named code on failure:
    - ``version-unknown``: the tag is not a known version in our scheme —
      a missing field, a non-integer tag (``"1.5"``), or an integer we do
      not recognise as a past one;
    - ``version-too-new``: an integer version above this reader's — never
      silently misread a future format we can order against but not parse.

    Migrations run in ascending version order; each step is a pure
    dict -> dict function, so the whole chain is deterministic.
    """
    v = d.get("schema_version")
    if v is None:
        raise ValueError("version-unknown: schema_version is missing")
    if v not in _KNOWN_STATE_VERSIONS:
        v_int = _version_int(v)
        cur_int = int(STATE_SCHEMA_VERSION)
        if v_int is not None and v_int > cur_int:
            raise ValueError(f"version-too-new: {v!r} > {STATE_SCHEMA_VERSION!r}")
        raise ValueError(f"version-unknown: {v!r}")
    cur = v
    while cur != STATE_SCHEMA_VERSION:
        d = _MIGRATORS[cur](d)
        cur = d["schema_version"]
    return d


# ---------- locking (process + file) --------------------------------------------
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.Lock] = {}


def _path_lock(path: str) -> threading.Lock:
    """A process-wide lock keyed by resolved path (load-modify-save must be
    critical even when two threads in this process race)."""
    key = os.path.abspath(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _file_lock(path: str) -> Iterator[Any]:
    """An exclusive, blocking ``fcntl.flock`` on a sidecar file — the
    cross-process half of the concurrency guard. Released on exit."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield f
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: str, data: str) -> None:
    """Write ``data`` to ``path`` atomically: temp file in the same
    directory + ``os.replace``. A crash mid-write leaves either the old file
    or the new file — never a torn document."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".learnerstate-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _projection_matches(state: LearnerState) -> bool:
    """Whether all persisted projections agree with the durable event list."""
    saved = {
        "nodes": copy.deepcopy(state.nodes),
        "attempts": copy.deepcopy(state.attempts),
        "misconceptions_triggered": copy.deepcopy(state.misconceptions_triggered),
        "mastery": copy.deepcopy(state.mastery),
        "schedules": copy.deepcopy(state.schedules),
    }
    rebuilt = copy.deepcopy(state)
    rebuilt._rebuild_from_events()
    return saved == {
        "nodes": rebuilt.nodes,
        "attempts": rebuilt.attempts,
        "misconceptions_triggered": rebuilt.misconceptions_triggered,
        "mastery": rebuilt.mastery,
        "schedules": rebuilt.schedules,
    }


# ---------- the store (atomic writes, locking, restart safety) -----------------
class LearnerStateStore:
    """The durable, per-subject learner-state store (P2.1).

    One store = one state file + its bound spec. All mutations go through
    ``record`` / ``record_events`` (load-modify-save under the locks, with
    P1.3 validation and idempotent dedup before any write), so the file is
    only ever touched with a compatible, validated, de-duplicated state.
    """

    def __init__(self, path: str, spec: CurriculumSpec):
        self.path = path
        self.spec = spec
        self._state: LearnerState | None = None

    # ---- load (honest, never a silent drop) ---------------------------------
    def load(self) -> LearnerState | None:
        """Load + migrate + compatibility-check the state file.

        Returns the state, or ``None`` when the file does not exist (a
        first-class result: "no state yet"). Raises ``ValueError`` with a
        named code for a corrupt file, a future version, or a state that is
        not compatible with the bound spec — the caller reports it.
        """
        if not os.path.exists(self.path):
            self._state = None
            return None
        with open(self.path, "r", encoding="utf-8") as f:
            raw = f.read()
        if not raw.strip():
            raise ValueError("corrupt-state: the state file is empty "
                             "(a torn write? the events are the source of truth)")
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"corrupt-state: {e}") from e
        if not isinstance(d, dict):
            # TRY004 suggests TypeError, but this module's contract is that
            # *every* load failure is a named ValueError (documented + pinned by
            # test_corrupt_file_is_a_named_first_class_error); keep it ValueError.
            raise ValueError("corrupt-state: the state document is not a mapping")  # noqa: TRY004
        try:
            d = migrate_state(d)
            state = LearnerState.from_dict(d)
        except ValueError:
            raise
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"corrupt-state: invalid state shape: {exc}") from exc
        issues = state.check_compatible(self.spec)
        if issues:
            raise ValueError("; ".join(issues))
        # The event list is canonical.  Never trust a hand-edited projection.
        if not _projection_matches(state):
            raise ValueError("corrupt-state: derived projection does not match durable events")
        self._state = state
        return state

    # ---- save (atomic) --------------------------------------------------------
    def save(self, state: LearnerState, now: str) -> None:
        """Compatibility-check, stamp ``last_updated``, and write atomically.

        ``now`` is caller-supplied (ISO-8601 UTC) — the store never reads the
        clock, so two saves with the same inputs are byte-identical
        (determinism for tests and replay).
        """
        issues = state.check_compatible(self.spec)
        if issues:
            raise ValueError("; ".join(issues))
        if not _projection_matches(state):
            raise ValueError("corrupt-state: derived projection does not match durable events")
        fsrs_mod._parse_timestamp(now, "now")
        if state.created_at is None:
            state.created_at = now
        state.last_updated = now
        _atomic_write(self.path, state.to_json())
        self._state = state

    # ---- the mutation boundary (validate + dedupe + trace + persist) ----------
    def record(self, event: dict[str, Any], now: str) -> le.RecordResult:
        """Validate + apply one P1.3 event and persist the resulting state.

        Under the process + file locks: load (or start fresh) -> run the P1.3
        log semantics (validate, dedupe, project) -> re-trace mastery with
        BKT -> atomic save. A rejected event is persisted as a rejection
        audit record (honesty: it happened, it was refused, the reason is
        named); a duplicate is a no-op and does not rewrite the file.
        """
        with _path_lock(self.path), _file_lock(self.path):
            return self._record_locked(event, now)

    def _replay_into_log(self, log: le.EventLog) -> LearnerState | None:
        """Replay the durable event log into a fresh P1.3 log so the new
        event sees the real attempt counts and the dedupe table. Returns the
        prior state (or None when starting fresh)."""
        # Always read the atomically replaced file while holding the file lock.
        # A cached state can be stale when another process has committed since
        # this store instance last wrote; using it would lose that process's
        # events during load-modify-save.
        prior = self.load()
        if prior is None:
            return None
        for e in prior.events:
            r = log.record(e)
            assert r.status in ("recorded", "duplicate")
        # carry the audit trail forward (rejections are history, not state)
        log.rejections = [dict(r) for r in prior.rejections]
        return prior

    def _record_locked(self, event: dict[str, Any], now: str) -> le.RecordResult:
        log = le.EventLog(self.spec)
        prior = self._replay_into_log(log)
        res = log.record(event)
        if res.status == "duplicate":
            # no-op: do not rewrite the file, do not touch last_updated —
            # but cache the loaded state for read conveniences
            if self._state is None:
                self._state = self.load()
            return res
        state = LearnerState.from_log(log, now)
        if prior is not None and prior.created_at is not None:
            state.created_at = prior.created_at
        self.save(state, now)
        return res

    def record_events(self, events: list[dict[str, Any]], now: str) -> list[le.RecordResult]:
        """Apply a P1.3 stream (e.g. ``le.events_from_answer``) and persist.

        An all-duplicate stream is a no-op: the file is not rewritten and
        ``last_updated`` is not touched (idempotent replay).
        """
        with _path_lock(self.path), _file_lock(self.path):
            log = le.EventLog(self.spec)
            prior = self._replay_into_log(log)
            results = log.record_events(events)
            if all(r.status == "duplicate" for r in results):
                if self._state is None:
                    self._state = self.load()
                return results
            state = LearnerState.from_log(log, now)
            if prior is not None and prior.created_at is not None:
                state.created_at = prior.created_at
            self.save(state, now)
            return results

    # ---- read-only conveniences ------------------------------------------------
    def state(self) -> LearnerState | None:
        return self._state

    def mastery_of(self, node_id: str) -> float:
        st = self._state if self._state is not None else self.load()
        return st.mastery.get(node_id, 0.0) if st else 0.0


# ---------- module-level convenience -------------------------------------------
def default_state_path(subject: str, base: str = "out/curriculum") -> str:
    """The conventional per-subject state path (next to the spec)."""
    return os.path.join(base, f"{subject}.learnerstate.json")


def process_answer(answer, spec: CurriculumSpec, store: LearnerStateStore,
                   learner_utterance: str | None = None,
                   now: str = "") -> dict[str, Any]:
    """The one-call P2.1/P2.2 boundary: grounded answer -> durable state update.

    Builds the P1.3 event stream (the same contract P1.3 pinned), applies it
    through the store (validate + dedupe + BKT trace + atomic write), and
    returns the per-event results + the traced mastery. ``now`` is the
    caller's timestamp (ISO-8601 UTC) — pass a fixed value in tests for a
    deterministic file.

    (P2.2) When ``now`` is supplied and the answer is grounded, the stream
    also carries the FSRS ``review`` event; the store persists the derived
    ``schedules`` projection (stability/difficulty/next_review) alongside the
    BKT mastery. Ungrounded answers emit no review event.
    """
    events = le.events_from_answer(answer, spec, learner_utterance, now=now)
    node_id = getattr(getattr(answer, "resolution", None), "node_id", None)
    if not events:
        return {"events": [], "results": [], "node_id": node_id, "mastery": None}
    results = store.record_events(events, now=now)
    # (P2.2) surface the durable review schedule for the node, if any.
    next_review = None
    st = store.state()
    if st is not None and node_id is not None and node_id in st.schedules:
        next_review = st.schedules[node_id].get("next_review")
    return {
        "events": events,
        "results": results,
        "node_id": node_id,
        "mastery": store.mastery_of(node_id) if node_id else None,
        "next_review": next_review,
    }


def process_assessment(item: dict[str, Any], response: Any,
                       spec: CurriculumSpec, store: LearnerStateStore,
                       now: str = "") -> dict[str, Any]:
    """Grade one T4 assessment and durably apply its verified outcome.

    This is the mastery boundary for API workers: ``assessment.grade`` runs
    deterministically inside :func:`learner_events.events_from_assessment`, so
    a tutor draft or learner self-score cannot award mastery.  The returned
    shape mirrors :func:`process_answer` and includes the resulting schedule.
    """
    events = le.events_from_assessment(item, response, spec, now=now)
    if not events:
        return {"events": [], "results": [], "node_id": None, "mastery": None,
                "next_review": None}
    results = store.record_events(events, now=now)
    node_id = next((e.get("node_id") for e in events
                    if e.get("kind") == le.KIND_ASSESSMENT), None)
    state = store.state()
    return {
        "events": events,
        "results": results,
        "node_id": node_id,
        "mastery": store.mastery_of(node_id) if node_id else None,
        "next_review": (
            state.schedules[node_id]["next_review"]
            if state and node_id in state.schedules else None),
    }
