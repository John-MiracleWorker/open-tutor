"""Learner events: validated interactions, assessments, and review signals.

The runtime boundary between the grounded engine (P1.1/P1.2) and the mastery
layer (P2). What this module defines *now*, deterministically and non-LLM:

- **Versioned event shapes** — pinned to ``SCHEMA_VERSION``:
    * ``attempt``         one tutor interaction on a resolved node
    * ``misconception``   a detected trigger, mapped to a *validated*
                          node misconception id (T1) — the reference is the
                          contract, free text is only the evidence data
    * ``mastery_delta``   a legacy zero-valued audit shape; non-zero deltas are
                          rejected because tutor groundedness is not mastery
    * ``assessment``      a deterministic T4 verdict, the only mastery source
    * ``review``          an FSRS grade and caller-supplied review timestamp
- **Deterministic validation** — every event is checked against the
  ``CurriculumSpec`` before it may touch state: unknown node / unknown
  misconception id / unknown schema version / missing or extra fields are
  first-class rejections, never silent drops.
- **Idempotent application** — an event's identity is the SHA-256 of its
  canonical payload (no timestamps, no randomness), so replaying the same
  event is a no-op: recorded once, never double-counted.
- **Honesty invariant** — a tutor answer, even when grounded, never produces a
  mastery or review signal.  Only a verified assessment outcome can do so.

The durable BKT projection and FSRS replay live in ``learner_state.py``;
``EventLog`` remains a pure in-memory validation/idempotency boundary.

Design invariants (inherited from the PoC, non-negotiable):
- **Deterministic, non-LLM.** Every verdict is computed from the event payload
  and the spec. A free-form LLM self-assessment cannot mutate state without a
  validated node/misconception reference — the log rejects it.
- **No silent failures.** Rejection reasons are named and returned; a failed
  event is reported, never swallowed.
- **Local-first, no cloud.** Pure Python + the spec; no I/O at all.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from .spec import CurriculumSpec

# ---------- schema versioning -------------------------------------------------
SCHEMA_VERSION = "1"    # the only known version; unknown versions are rejected

# ---------- event kinds (closed vocabulary) -----------------------------------
KIND_ATTEMPT = "attempt"
KIND_MISCONCEPTION = "misconception"
KIND_MASTERY_DELTA = "mastery_delta"
KIND_ASSESSMENT = "assessment"
# P2.2 — a verified review interaction on a node: the scheduler's input.
# It is an event like the others (validated, idempotent, durable) because
# the schedule must be replayable from the state log after a restart.
KIND_REVIEW = "review"
EVENT_KINDS = (KIND_ATTEMPT, KIND_MISCONCEPTION, KIND_MASTERY_DELTA,
               KIND_ASSESSMENT, KIND_REVIEW)

# The exact field set per kind (``event_id`` and ``subject`` are handled by
# the log itself: ``event_id`` is derived, ``subject`` is optional and filled
# from the bound spec). Any other key in the payload is a shape violation.
_CORE_FIELDS: dict[str, frozenset] = {
    KIND_ATTEMPT: frozenset({
        "v", "kind", "node_id", "question", "answer_status", "grounded",
        "quantitative", "citations_used", "oracle_status",
    }),
    KIND_MISCONCEPTION: frozenset({
        "v", "kind", "node_id", "misconception_id", "evidence", "detected_by",
    }),
    KIND_MASTERY_DELTA: frozenset({
        "v", "kind", "node_id", "delta", "reason", "attempts_before",
    }),
    KIND_ASSESSMENT: frozenset({
        "v", "kind", "node_id", "item_id", "verdict", "score",
        "response_fingerprint",
    }),
    KIND_REVIEW: frozenset({
        "v", "kind", "node_id", "grade", "review_time",
    }),
}

# Deterministic detection method tags — named so a later tracer can tell
# which evidence produced a misconception event. Only deterministic methods
# exist in P1.3; an LLM-labelled event is rejected (``detected_by`` must be
# one of these, never a model name).
DETECTOR_KEYWORD_MAP = "keyword-map"
DETECTOR_MANUAL = "manual"
VALID_DETECTORS = (DETECTOR_KEYWORD_MAP, DETECTOR_MANUAL)
ASSESSMENT_VERDICTS = ("correct", "partial", "incorrect", "flagged",
                       "degraded", "not-scored")

# ---------- deterministic mastery-delta rules (P2's tracer consumes these) ----
# Named, bounded, and summed — a delta is always the sum of the rules that
# fire for one interaction, so it is reproducible and auditable.
RULE_GROUNDED_ATTEMPT = "grounded-attempt"
RULE_MISCONCEPTION_TRIGGERED = "misconception-triggered"
RULE_UNVERIFIED_ATTEMPT = "unverified-attempt"
RULE_ASSESSMENT_CORRECT = "assessment-correct"
RULE_ASSESSMENT_INCORRECT = "assessment-incorrect"
RULES = (RULE_GROUNDED_ATTEMPT, RULE_MISCONCEPTION_TRIGGERED,
         RULE_UNVERIFIED_ATTEMPT, RULE_ASSESSMENT_CORRECT,
         RULE_ASSESSMENT_INCORRECT)

RULE_DELTAS: dict[str, float] = {
    RULE_GROUNDED_ATTEMPT: 0.10,       # the gate certified the answer
    RULE_MISCONCEPTION_TRIGGERED: -0.05,  # a validated misconception fired
    RULE_UNVERIFIED_ATTEMPT: 0.0,     # honest: no positive change, ever
    # These legacy rule values are retained for audit/report compatibility;
    # assessment mastery is traced from the assessment event itself, not a
    # caller-supplied float.
    RULE_ASSESSMENT_CORRECT: 0.0,
    RULE_ASSESSMENT_INCORRECT: 0.0,
}


def grade_for_verdict(grounded: bool, misconceptions_triggered: bool = False) -> int:
    """Map an answer's gate verdict to an FSRS review grade (1..4).

    3 — correct, verified, no scaffold needed
    2 — correct, verified, but a scaffold/misconception was triggered
    1 — unverified (the gate did not certify the answer) — a lapse

    ``grounded`` is the verifier's verdict (not the learner's self-assessment).
    ``misconceptions_triggered`` indicates whether a scaffold was needed.

    See ``fsrs.py`` for the full grade → scheduling semantics.
    """
    if not grounded:
        return 1  # again: the gate says the learner did not demonstrate it
    if misconceptions_triggered:
        return 2  # hard: recalled with help
    return 3  # good: clean verified recall


# ---------- detection tuning (deterministic keyword overlap) ------------------
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "is", "are", "be",
    "been", "was", "were", "it", "its", "with", "for", "as", "at", "by", "how",
    "what", "which", "who", "when", "where", "why", "can", "could", "will",
    "would", "should", "may", "might", "do", "does", "did", "not", "no", "yes",
    "just", "only", "also", "than", "then", "there", "here", "this", "that",
    "these", "those", "into", "about", "after", "me", "we", "our", "us",
}
_DETECT_MIN_TOKEN_LEN = 4          # short tokens are too weak to map on
_DETECT_MIN_OVERLAP = 3            # at least 3 distinctive tokens must match
_DETECT_MIN_SCORE = 0.5            # |overlap| / |distinctive tokens| >= 0.5


def _tokens(text: str) -> set:
    out = set()
    for t in _TOKEN.findall((text or "").lower()):
        if len(t) >= _DETECT_MIN_TOKEN_LEN and t not in _STOP:
            out.add(t)
    return out


# ---------- event identity (deterministic, replay-stable) ---------------------
def canonical_payload(event: dict[str, Any]) -> dict[str, Any]:
    """The event minus derived fields — the input to validation and identity.

    ``event_id`` is derived (never trusted from the caller) and ``subject``
    is filled from the bound spec when absent, so the canonical form is the
    payload as the log stores it, pre-hash.
    """
    e = {k: v for k, v in dict(event).items() if k != "event_id"}
    return e


def event_id(canonical: dict[str, Any]) -> str:
    """SHA-256 over the canonical payload — the idempotency key.

    No timestamps, no randomness: the same logical event always has the same
    id, so a replayed event collides with its original and is a no-op.
    """
    blob = json.dumps(canonical, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------- validation (deterministic, spec-checked, no silent passes) --------
def validate_event(event: dict[str, Any], spec: CurriculumSpec) -> tuple[bool, list[str]]:
    """Validate one event against the spec. Returns ``(ok, issues)``.

    Issues are named, first-class codes (the same vocabulary a report would
    carry): ``version-unknown``, ``kind-unknown``, ``node-unknown``,
    ``misconception-unknown``, ``field-missing:<f>``, ``field-extra:<f>``,
    ``field-type:<f>``, ``subject-mismatch``, ``detector-unknown``,
    ``delta-not-finite``, ``evidence-empty``. An event is valid iff it
    carries a validated node reference (and, for misconceptions, a
    validated misconception id on that node) — nothing free-form may touch
    state without one.
    """
    issues: list[str] = []
    if not isinstance(event, dict):
        return False, ["event-not-mapping: event must be a mapping"]

    e = canonical_payload(event)

    # V0 — schema version (versioned contract: unknown versions are rejected,
    # never guessed at; a P2 reader sees v and knows what it is reading).
    v = e.get("v")
    if v != SCHEMA_VERSION:
        issues.append(f"version-unknown: v={v!r} (known: {SCHEMA_VERSION!r})")

    kind = e.get("kind")
    if kind not in EVENT_KINDS:
        issues.append(f"kind-unknown: kind={kind!r} (known: {list(EVENT_KINDS)})")
        return False, issues

    # V1 — strict shape: exactly the declared fields, no more, no less.
    # ``event_id`` is derived (never required from the caller) and ``subject``
    # is optional (filled from the bound spec); both are allowed, neither
    # required.
    allowed = _CORE_FIELDS[kind] | {"event_id", "subject"}
    extra = sorted(set(e) - allowed)
    missing = sorted(allowed - set(e) - {"subject", "event_id"})
    for f in extra:
        issues.append(f"field-extra:{f}")
    for f in missing:
        issues.append(f"field-missing:{f}")
    if extra or missing:
        return False, issues

    # V2 — subject binding (optional in the payload; when present it must be
    # this spec's subject, else the event belongs to another learner state).
    subject = e.get("subject")
    if subject is not None and subject != spec.subject:
        issues.append(f"subject-mismatch: {subject!r} != {spec.subject!r}")

    # V3 — the node reference: mandatory and must exist in the spec. This is
    # the anchor of the whole contract — no validated node id, no state.
    node_id = e.get("node_id")
    if not isinstance(node_id, str) or not node_id:
        issues.append("field-type:node_id (must be a non-empty string)")
        return False, issues
    node = spec.node(node_id)
    if node is None:
        issues.append(f"node-unknown: node_id={node_id!r} is not in this spec")
        return False, issues

    # V4 — kind-specific rules.
    if kind == KIND_MISCONCEPTION:
        mid = e.get("misconception_id")
        if not isinstance(mid, str) or not mid:
            issues.append("field-type:misconception_id (must be a non-empty string)")
        else:
            known = {m.id for m in node.misconceptions}
            if mid not in known:
                issues.append(
                    f"misconception-unknown: {mid!r} is not a misconception of "
                    f"node {node_id!r} (known: {sorted(known) or 'none'})")
        evidence = e.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            issues.append("evidence-empty: the triggering utterance is the "
                          "evidence data; it must be present")
        det = e.get("detected_by")
        if det not in VALID_DETECTORS:
            issues.append(
                f"detector-unknown: detected_by={det!r} (known: "
                f"{list(VALID_DETECTORS)}) — an LLM self-assessment may not "
                f"label state changes")
    elif kind == KIND_ATTEMPT:
        question = e.get("question")
        if not isinstance(question, str) or not question.strip():
            issues.append("field-type:question (must be a non-empty string)")
        status = e.get("answer_status")
        if not isinstance(status, str) or not status:
            issues.append("field-type:answer_status (must be a non-empty string)")
        if not isinstance(e.get("grounded"), bool):
            issues.append("field-type:grounded (must be a bool)")
        if not isinstance(e.get("quantitative"), bool):
            issues.append("field-type:quantitative (must be a bool)")
        cits = e.get("citations_used")
        if not isinstance(cits, list) or any(not isinstance(c, int) for c in cits):
            issues.append("field-type:citations_used (must be a list of ints)")
        if e.get("oracle_status") is not None and not isinstance(e["oracle_status"], str):
            issues.append("field-type:oracle_status (must be a str or null)")
    elif kind == KIND_MASTERY_DELTA:
        delta = e.get("delta")
        if isinstance(delta, bool) or not isinstance(delta, (int, float)):
            issues.append("field-type:delta (must be a finite number)")
        else:
            try:
                finite = math.isfinite(float(delta))
            except OverflowError:
                finite = False
            if not finite:
                issues.append("delta-not-finite: delta must be a finite number")
            elif not -1.0 <= delta <= 1.0:
                issues.append("delta-out-of-range: delta must be in [-1, 1]")
            elif delta != 0.0:
                issues.append("mastery-source-required: non-zero mastery deltas must "
                              "come from a verified assessment event")
        reason = e.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            issues.append("field-type:reason (must be a non-empty string; the "
                          "named rule that produced the delta)")
        if not isinstance(e.get("attempts_before"), int) or e["attempts_before"] < 0:
            issues.append("field-type:attempts_before (must be a non-negative int)")
    elif kind == KIND_REVIEW:
        # P2.2: the grade is the FSRS rating (1..4); review_time is the
        # caller-supplied clock (ISO-8601 UTC) — both must be honest, named
        # values, never free-form.
        grade = e.get("grade")
        if isinstance(grade, bool) or not isinstance(grade, int) or grade not in (1, 2, 3, 4):
            issues.append("grade-unknown: grade must be an int in {1, 2, 3, 4} "
                          "(FSRS grades: 1=again, 2=hard, 3=good, 4=easy)")
        rt = e.get("review_time")
        if not isinstance(rt, str) or not rt.strip():
            issues.append("review-time-missing: review_time must be a non-empty "
                          "ISO-8601 UTC timestamp (caller-supplied clock)")
    elif kind == KIND_ASSESSMENT:
        item_id = e.get("item_id")
        if not isinstance(item_id, str) or not item_id.strip():
            issues.append("field-type:item_id (must be a non-empty string)")
        verdict = e.get("verdict")
        if verdict not in ASSESSMENT_VERDICTS:
            issues.append(f"verdict-unknown: {verdict!r} (known: "
                          f"{list(ASSESSMENT_VERDICTS)})")
        score = e.get("score")
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                issues.append("field-type:score (must be null or a finite number)")
            else:
                try:
                    finite = math.isfinite(float(score))
                except OverflowError:
                    finite = False
                if not finite:
                    issues.append("score-not-finite: score must be finite")
                elif not 0.0 <= score <= 1.0:
                    issues.append("score-out-of-range: score must be in [0, 1]")
        fingerprint = e.get("response_fingerprint")
        if (not isinstance(fingerprint, str)
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
            issues.append("field-type:response_fingerprint (must be a SHA-256 hex string)")
    return (not issues), issues


def normalize_event(event: dict[str, Any], spec: CurriculumSpec) -> dict[str, Any]:
    """The canonical, stored form of a valid event (subject filled, id added).

    Only call after ``validate_event`` returned ok — normalization is a
    projection, not a repair: it does not fill missing fields.
    """
    e = canonical_payload(event)
    e["subject"] = e.get("subject") or spec.subject
    e["event_id"] = event_id(e)
    return e


# ---------- misconception detection (deterministic keyword map) ---------------
def misconception_candidates(text: str, spec: CurriculumSpec,
                             node_id: str | None = None) -> list[dict[str, Any]]:
    """Map a learner utterance to the spec's misconception ids.

    Deterministic keyword-overlap scoring (no LLM): a misconception is a
    candidate when at least ``_DETECT_MIN_OVERLAP`` of its *distinctive*
    tokens (>=4 chars, stop words dropped) appear in the utterance AND the
    overlap covers at least ``_DETECT_MIN_SCORE`` of them. Results are the
    validated ``(node_id, misconception_id)`` references plus their score and
    the matched tokens — sorted by (-score, node_id, misconception_id), so the
    mapping is fully reproducible.

    ``node_id`` restricts the search to one node (the engine's resolved node);
    when omitted the whole spec is searched (e.g. for a free teach-back).
    """
    utterance = _tokens(text)
    rows: list[dict[str, Any]] = []
    for node in spec.nodes:
        if node_id is not None and node.id != node_id:
            continue
        for m in node.misconceptions:
            distinct = _tokens(m.text)
            if not distinct:
                continue
            overlap = sorted(utterance & distinct)
            if len(overlap) < _DETECT_MIN_OVERLAP:
                continue
            score = len(overlap) / len(distinct)
            if score < _DETECT_MIN_SCORE:
                continue
            rows.append({
                "node_id": node.id,
                "misconception_id": m.id,
                "score": round(score, 4),
                "matched": overlap,
            })
    rows.sort(key=lambda r: (-r["score"], r["node_id"], r["misconception_id"]))
    return rows


def make_misconception_event(candidate: dict[str, Any], spec: CurriculumSpec,
                             evidence: str) -> dict[str, Any]:
    """A misconception event from a validated candidate (data + reference).

    ``evidence`` is the learner's utterance — data, never instructions. The
    event carries the validated node/misconception ids, so it is exactly the
    shape the log accepts (see ``validate_event``).
    """
    return {
        "v": SCHEMA_VERSION,
        "kind": KIND_MISCONCEPTION,
        "subject": spec.subject,
        "node_id": candidate["node_id"],
        "misconception_id": candidate["misconception_id"],
        "evidence": evidence,
        "detected_by": DETECTOR_KEYWORD_MAP,
    }


# ---------- mastery-delta rules (deterministic, named, bounded) ---------------
def mastery_delta_for_attempt(grounded: bool,
                              misconceptions_triggered: int = 0) -> dict[str, Any]:
    """The delta rules for one interaction, as a fired-rule accounting.

    Honesty rule (the card's core invariant): an unverified answer can never
    award mastery. ``grounded=False`` fires ``unverified-attempt`` (delta 0.0)
    and *suppresses* the grounded rule entirely; only the negative
    misconception rule may still fire. ``grounded=True`` fires
    ``grounded-attempt`` and, per validated trigger, the misconception rule.
    The result is the sum — deterministic, bounded, fully named.
    """
    fired: list[str] = []
    if grounded:
        fired.append(RULE_GROUNDED_ATTEMPT)
        fired.extend([RULE_MISCONCEPTION_TRIGGERED] * misconceptions_triggered)
    else:
        fired.append(RULE_UNVERIFIED_ATTEMPT)
        if misconceptions_triggered:
            fired.extend([RULE_MISCONCEPTION_TRIGGERED] * misconceptions_triggered)
    delta = sum(RULE_DELTAS[r] for r in fired)
    return {
        "rules": fired,
        "delta": delta,
        "reason": "; ".join(f"{r}" for r in fired) or "no-rule-fired",
    }


def make_attempt_event(node_id: str, question: str, answer_status: str,
                       grounded: bool, quantitative: bool,
                       citations_used: list[int],
                       oracle_status: str | None,
                       spec: CurriculumSpec) -> dict[str, Any]:
    """The canonical attempt event for a resolved node (P1.3 contract shape)."""
    return {
        "v": SCHEMA_VERSION,
        "kind": KIND_ATTEMPT,
        "subject": spec.subject,
        "node_id": node_id,
        "question": question,
        "answer_status": answer_status,
        "grounded": bool(grounded),
        "quantitative": bool(quantitative),
        "citations_used": [int(c) for c in citations_used],
        "oracle_status": oracle_status,
    }


def _response_fingerprint(response: Any) -> str:
    """Stable identity for one learner response, without trusting its score."""
    if response is None:
        payload: Any = None
    elif hasattr(response, "to_dict"):
        payload = response.to_dict()
    elif isinstance(response, dict):
        payload = response
    else:
        payload = {
            "text": getattr(response, "text", ""),
            "grounded": bool(getattr(response, "grounded", False)),
            "citations": getattr(response, "citations", []),
        }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def grade_for_assessment(verdict: str, score: float | None,
                         misconceptions_triggered: bool = False) -> int | None:
    """Map a deterministic assessment verdict to an FSRS review grade."""
    if score is None or verdict not in ("correct", "partial", "incorrect"):
        return None
    if verdict == "incorrect":
        return 1
    if verdict == "partial" or misconceptions_triggered:
        return 2
    return 3


def make_assessment_event(result: dict[str, Any], response: Any,
                          spec: CurriculumSpec) -> dict[str, Any]:
    """Build an event from the output of deterministic ``assessment.grade``."""
    return {
        "v": SCHEMA_VERSION,
        "kind": KIND_ASSESSMENT,
        "subject": spec.subject,
        "node_id": result.get("node_id"),
        "item_id": result.get("item_id"),
        "verdict": result.get("verdict"),
        "score": result.get("score"),
        "response_fingerprint": _response_fingerprint(response),
    }


def events_from_assessment(item: dict[str, Any], response: Any,
                           spec: CurriculumSpec, now: str = "") -> list[dict[str, Any]]:
    """Create events from a verified T4 assessment result.

    The item is validated and graded inside this function.  A caller cannot
    pass a self-declared score as mastery evidence.  Only a scored assessment
    produces an FSRS review event; flagged/degraded/not-scored results produce
    no learning signal.
    """
    from . import assessment

    valid, issues = assessment.validate_item(item, spec)
    if not valid:
        raise ValueError("assessment-invalid: " + "; ".join(issues))
    normalized = response
    if not isinstance(normalized, assessment.LearnerResponse):
        normalized = assessment.response_from_answer(response)
    result = assessment.grade(item, normalized, spec)
    response_grounded = bool(normalized and normalized.grounded)
    if not response_grounded and result.get("verdict") in (
            "correct", "partial", "incorrect"):
        # A quiz grader can call an ungrounded answer "incorrect", but that is
        # not a verified assessment outcome: BKT's learning transition would
        # otherwise turn even an initial incorrect response into positive
        # knowledge.  Teach-back grading is similarly rubric-based and cannot
        # infer that the response passed the answer gate.  Demote every scored
        # ungrounded result to a no-signal outcome.
        result = dict(result)
        result["verdict"] = "flagged"
        result["score"] = None
        result["issues"] = list(result.get("issues") or [])
        result["issues"].append("unverified-attempt: assessment response was not gate-certified")
    node_id = result.get("node_id")
    if not isinstance(node_id, str) or spec.node(node_id) is None:
        return []

    grounded = response_grounded
    citations = normalized.citations if normalized else []
    attempt = make_attempt_event(
        node_id=node_id, question=str(item.get("prompt", "")),
        answer_status=f"assessment-{result.get('verdict')}", grounded=grounded,
        quantitative=bool(item.get("quantitative")),
        citations_used=list(range(1, len(citations) + 1)), oracle_status=None, spec=spec)
    events: list[dict[str, Any]] = [attempt]

    misconception_rows = result.get("misconceptions") or []
    evidence = normalized.text if normalized else ""
    for candidate in misconception_rows:
        events.append(make_misconception_event(candidate, spec, evidence))

    events.append(make_assessment_event(result, normalized, spec))
    review_grade = grade_for_assessment(
        str(result.get("verdict")), result.get("score"), bool(misconception_rows))
    if review_grade is not None and now and now.strip():
        events.append({
            "v": SCHEMA_VERSION, "kind": KIND_REVIEW, "subject": spec.subject,
            "node_id": node_id, "grade": review_grade, "review_time": now,
        })
    return events


def events_from_answer(answer, spec: CurriculumSpec,
                       learner_utterance: str | None = None,
                       now: str = "") -> list[dict[str, Any]]:
    """Build the P1.3 event stream for one ``engine.tutor`` answer.

    Deterministic from ``(answer, spec, utterance, now)``:
      1. the ``attempt`` event (the engine's honest grounded flag — the gate
         verdict, never a self-assessment),
      2. one ``misconception`` event per validated candidate in the utterance
         (only when the answer resolved a node),
      3. no mastery or review event. Tutor groundedness is evidence for
         generating an answer, not a verified assessment outcome.

    Unresolvable answers (``status == 'no-node'``) emit no events: there is
    no validated node reference, so there is nothing the state may change
    on. That is the contract working as designed, not a gap.
    """
    if answer is None or not getattr(answer, "resolution", None):
        return []
    node_id = getattr(answer.resolution, "node_id", None)
    if not node_id or spec.node(node_id) is None:
        return []  # no validated node reference -> no state may be touched

    grounded = bool(getattr(answer, "grounded", False))
    verification = getattr(answer, "verification", None)
    citations_used = (list(verification.citations_used) if verification else [])
    oracle = getattr(answer, "oracle", None)
    oracle_status = getattr(oracle, "status", None)

    events: list[dict[str, Any]] = [
        make_attempt_event(
            node_id=node_id,
            question=getattr(answer, "question", ""),
            answer_status=str(getattr(answer, "status", "unknown")),
            grounded=grounded,
            quantitative=bool(getattr(answer, "quantitative", False)),
            citations_used=citations_used,
            oracle_status=oracle_status,
            spec=spec,
        )
    ]

    if learner_utterance and learner_utterance.strip():
        for cand in misconception_candidates(learner_utterance, spec, node_id):
            events.append(make_misconception_event(cand, spec, learner_utterance))

    return events


# ---------- the event log (idempotent; persistence is P2) ----------------------
@dataclass
class RecordResult:
    """The outcome of one ``EventLog.record`` — honest, never silent.

    ``status``: ``recorded`` | ``duplicate`` | ``rejected``. A duplicate is
    the idempotent no-op (same canonical payload, same id); a rejection
    carries the named validation issues."""
    status: str
    event_id: str | None = None
    issues: list[str] = field(default_factory=list)


class EventLog:
    """The in-memory learner-state update boundary (P1.3).

    - **Validates** every event against the bound spec (node/misconception
      references must be real; unknown schema versions are rejected).
    - **Dedupes** on the deterministic event id: replaying an event returns
      ``duplicate`` and changes nothing — no double mastery, no double
      misconception count.
    - **Accumulates** the honest, auditable state P2 will persist: attempts
      per node and triggered misconception ids. Mastery is traced by P2 from
      verified assessment events; the legacy mastery-delta shape is audit-only.

    No I/O, no clock, no randomness: two logs fed the same event sequence in
    the same order are identical.
    """

    def __init__(self, spec: CurriculumSpec):
        self.spec = spec
        self.entries: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self.attempts: dict[str, int] = {}
        self.misconceptions: dict[str, list[str]] = {}
        self.mastery: dict[str, float] = {}
        self.rejections: list[dict[str, Any]] = []

    # ---- recording ---------------------------------------------------------
    def record(self, event: dict[str, Any]) -> RecordResult:
        """Validate + apply one event. Never raises on a bad event."""
        ok, issues = validate_event(event, self.spec)
        if not ok:
            self.rejections.append({"event": dict(event), "issues": issues})
            return RecordResult(status="rejected", issues=issues)

        canonical = normalize_event(event, self.spec)
        eid = canonical["event_id"]
        if eid in self._by_id:
            return RecordResult(status="duplicate", event_id=eid)

        self._by_id[eid] = canonical
        self.entries.append(canonical)

        node_id = canonical["node_id"]
        kind = canonical["kind"]
        if kind == KIND_ATTEMPT:
            self.attempts[node_id] = self.attempts.get(node_id, 0) + 1
        elif kind == KIND_MISCONCEPTION:
            self.misconceptions.setdefault(node_id, [])
            if canonical["misconception_id"] not in self.misconceptions[node_id]:
                self.misconceptions[node_id].append(canonical["misconception_id"])
        # ``mastery_delta`` is retained as a zero-valued audit shape for old
        # callers, but non-zero deltas are rejected above.  Mastery is traced
        # from ``assessment`` events by learner_state.py.
        return RecordResult(status="recorded", event_id=eid)

    def record_events(self, events: list[dict[str, Any]]) -> list[RecordResult]:
        """Apply a stream (e.g. ``events_from_answer``); order preserved."""
        return [self.record(e) for e in events]

    # ---- state (the honest, auditable projection P2 persists) ---------------
    def mastery_of(self, node_id: str) -> float:
        return self.mastery.get(node_id, 0.0)

    def misconceptions_of(self, node_id: str) -> list[str]:
        return list(self.misconceptions.get(node_id, []))

    def summary(self) -> dict[str, Any]:
        """The full P1.3 state — deterministic and diffable."""
        return {
            "subject": self.spec.subject,
            "schema_version": SCHEMA_VERSION,
            "entries": [dict(e) for e in self.entries],
            "attempts": dict(self.attempts),
            "misconceptions_triggered": {k: list(v) for k, v in self.misconceptions.items()},
            "mastery": {k: round(v, 6) for k, v in self.mastery.items()},
            "rejections": [dict(r) for r in self.rejections],
        }


# ---------- module-level convenience (the boundary API) ------------------------
def process_answer(answer, spec: CurriculumSpec,
                   log: EventLog,
                   learner_utterance: str | None = None) -> dict[str, Any]:
    """The one-call P1.3 boundary: answer + utterance -> validated state update.

    Returns ``{"events": [...], "results": [...], "mastery": float}`` where
    ``results[i].status`` is recorded/duplicate/rejected per event. Rejections
    are surfaced, never swallowed; unverified answers contribute a zero delta
    by construction (see ``mastery_delta_for_attempt``).
    """
    events = events_from_answer(answer, spec, learner_utterance)
    results = log.record_events(events)
    node_id = getattr(getattr(answer, "resolution", None), "node_id", None)
    return {
        "events": events,
        "results": results,
        "mastery": log.mastery_of(node_id) if node_id else 0.0,
    }
