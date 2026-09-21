"""P1.4 — TIER-4 VERIFIED ASSESSMENT: items, answer keys, rubrics, teach-back.

The assessment tier (SPEC §2, T4 — "how you know you know") as a
deterministic, non-LLM runtime contract, in the same shape and discipline as
P1.3 (``learner_events``). It sits on the P1.1/P1.2 engine: every graded
attempt is *certified* by the deterministic gate (``verifier.verify_answer``);
this module only *decides what a given, certified attempt is worth*, and it
does so by rules — never by a model, and never from a draft's self-assessment.

What this module defines now, deterministically and non-LLM:

- **Versioned assessment items** — ``make_item`` / ``validate_item``, pinned to
  ``SCHEMA_VERSION``. Three kinds (``quiz`` | ``teachback`` | ``worked``), each
  bound to a T1 node. Strict per-kind shape validation: an unknown kind /
  version / node / oracle / corpus id, or a missing or extra field, is a
  first-class named rejection — never silent.
- **Verified answer keys** — ``build_answer_key`` resolves the key *from
  evidence*, never authored by the drafter:
    * a **quantitative** key is the *executed T3 oracle* (the code is the
      answer key): the numeric value is read from the oracle result at the
      item's ``oracle_field``. The oracle must have *run* (``run_oracle`` ok)
      for the item to be usable; a failed/absent oracle degrades honestly.
    * a **factual** key is the item's required T2 citation(s): at least one
      corpus id that exists in the spec, and the graded attempt must carry a
      citation covering one of them.
- **Deterministic grading boundaries** — a closed verdict vocabulary
  (``correct`` | ``partial`` | ``incorrect`` | ``flagged`` | ``degraded`` |
  ``not-scored``) and a score in ``[0, 1]`` computed only from rules.
  Unsupported or ambiguous grading is **flagged** (score ``None``), never
  silently scored; a no-T3 quantitative item is reported **degraded**
  ("worked example + citations"), never given a fabricated runnable key.
- **Teach-back I/O** — ``LearnerResponse`` is the *input* (the learner's own
  text + the gate verdict + its citations); the graded output is a rubric
  score over the learner's concept points plus the validated misconception
  references their explanation triggers. The misconception mapping is *exactly*
  P1.3's deterministic keyword map (``node_id`` / ``misconception_id``), so it
  is a validated T1 reference — never a free-text guess.

Honesty invariants (inherited, non-negotiable):
- **Assessment is separate from the LLM draft.** The drafter never chooses its
  own grade: the key is evidence-derived (oracle / citation) and the verdict is
  rule-derived over a ``LearnerResponse`` (text + the *deterministic* gate
  verdict + citations). No field of the draft is trusted as a self-score.
- **Unverified never awards.** An attempt the gate did not certify can never
  produce a positive grade (the P1.3 rule applies here too).
- **Unsupported / ambiguous grading is flagged, never scored.** No usable key,
  no answer to grade, or an unresolvable node -> a named verdict with a
  named reason, score ``None``.
- **No-T3 honest degradation.** A subject/node with no T3 (or a failed oracle)
  grades a quantitative item ``degraded`` — "citations only" — never a
  fabricated runnable key.
- **The human gate is preserved.** ``assess`` is *pure*: it returns a report
  and never compiles, persists, or mutates the spec. The curriculum-approval
  gate (P1.0) and the learner-state gate (P1.3) are the boundaries this module
  feeds — not replaces.

Local-first, no cloud, no I/O: pure Python over the spec, the executed oracle,
and the learner's certified response.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .spec import CurriculumSpec, Node
from .verifier import run_oracle

# ---------- schema versioning -------------------------------------------------
SCHEMA_VERSION = "1"    # the only known version; unknown versions are rejected

# ---------- item kinds (closed vocabulary) ------------------------------------
KIND_QUIZ = "quiz"            # a question the learner answers (graded against a key)
KIND_TEACHBACK = "teachback"  # the learner explains it back (rubric + misconception)
KIND_WORKED = "worked"        # a worked solution — a T4 answer key bound to T3
KINDS = (KIND_QUIZ, KIND_TEACHBACK, KIND_WORKED)

# ---------- grading verdicts (closed vocabulary) ------------------------------
CORRECT = "correct"          # fully satisfied the key + rubric
PARTIAL = "partial"          # partially satisfied
INCORRECT = "incorrect"      # scored, and wrong (unverified never awards)
FLAGGED = "flagged"          # unsupported/ambiguous — reported, never scored
DEGRADED = "degraded"        # no-T3 honest degradation (citations only)
NOT_SCORED = "not-scored"    # nothing to grade (no response)
VERDICTS = (CORRECT, PARTIAL, INCORRECT, FLAGGED, DEGRADED, NOT_SCORED)

# The exact field set per kind. ``id``, ``kind``, ``v``, ``node_id`` and
# ``prompt`` are common; the rest is kind-specific. Any other key in an item is
# a shape violation (field-extra), a missing one is field-missing — never silent.
_CORE_FIELDS: dict[str, frozenset] = {
    KIND_QUIZ: frozenset({
        "id", "kind", "v", "node_id", "prompt", "quantitative",
        "required_citations", "oracle", "oracle_field",
    }),
    KIND_TEACHBACK: frozenset({
        "id", "kind", "v", "node_id", "prompt", "required_citations",
        "rubric_points",
    }),
    KIND_WORKED: frozenset({
        "id", "kind", "v", "node_id", "prompt", "oracle", "oracle_field",
        "required_citations",
    }),
}

# Authored quiz extensions are persisted server-side but redacted by the API
# before an item reaches the browser.  They let a factual/choice quiz have a
# real deterministic key without exposing that key in GET /assessment.
_OPTIONAL_FIELDS: frozenset = frozenset({"answer_options", "trusted_answer"})

# ---------- rubric decision boundaries (deterministic) ------------------------
RUBRIC_CORRECT = 0.75    # rubric score at/above this -> correct
RUBRIC_PARTIAL = 0.40    # at/above this (and below correct) -> partial; else incorrect
MISSING_CITATION_FACTOR = 0.5   # required citations present but not covered
MISCONCEPTION_FACTOR = 0.5      # a validated misconception fired on the explanation


# ---------- the learner response (the graded input; separate from the draft) ---
@dataclass
class LearnerResponse:
    """What the assessment grades — the learner's own attempt, certified by the
    deterministic gate. This is the seam that keeps T4 separate from the LLM
    draft: the module reads only the learner's ``text``, the gate's ``grounded``
    verdict, and the learner's ``citations``. No field of a draft's
    self-assessment is trusted as a score.

    ``text``          the learner's answer (quiz) or explanation (teach-back).
    ``grounded``      the deterministic gate's verdict (verify_answer), not a
                      self-report.
    ``citations``     the T2 spans the learner's attempt cites; each must carry
                      a ``source_id`` (a corpus id) for coverage checks.
    ``verification``  the full gate report (transparency; not used to score).
    """
    text: str
    grounded: bool = False
    citations: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "grounded": self.grounded,
            "citations": [dict(c) for c in self.citations],
            "verification": (None if self.verification is None
                             else dict(self.verification)),
        }


def response_from_answer(answer: Any) -> LearnerResponse | None:
    """Adapt an ``engine.tutor`` ``Answer`` (or any object shaped like one) into
    a ``LearnerResponse``. Duck-typed so the assessment module does not hard-
    depend on the engine. ``None`` when the input is not a usable response.

    Only the learner-relevant surface is extracted: the raw text, the gate's
    ``grounded`` verdict, and the citation source ids. The draft's internal
    structure is never parsed here — that is the gate's job, not T4's.
    """
    if answer is None:
        return None
    if isinstance(answer, dict):
        d = answer
        text = d.get("draft")
        grounded = bool(d.get("grounded", False))
        verification = d.get("verification")
        raw_cits = d.get("citations") or []
    else:
        text = getattr(answer, "draft", None)
        grounded = bool(getattr(answer, "grounded", False))
        verification = getattr(answer, "verification", None)
        raw_cits = getattr(answer, "citations", None) or []
    if text is None:
        return None
    vdict = None
    if verification is not None:
        vdict = verification.to_dict() if hasattr(verification, "to_dict") \
            else dict(verification)
        if vdict and "grounded" in vdict:
            grounded = bool(vdict["grounded"])

    citations: list[dict[str, Any]] = []
    for c in raw_cits:
        d = c.to_dict() if hasattr(c, "to_dict") else dict(c)
        sid = d.get("source_id")
        if sid:
            citations.append(d)
    return LearnerResponse(text=str(text), grounded=grounded,
                           citations=citations, verification=vdict)


# ---------- deterministic answer-key resolution -------------------------------
def _oracle_result(oracle_name: str | None) -> dict[str, Any] | None:
    """The executed T3 result for ``oracle_name`` (None when absent / failed).

    The oracle is the answer key: its output is *computed*, never authored. A
    failed or unknown oracle yields None so the caller degrades honestly — it
    never fakes a runnable key (SPEC §8).
    """
    if not oracle_name:
        return None
    res = run_oracle(oracle_name)
    if not res.get("ok"):
        return None
    return res.get("result")


def _numeric_at(result: dict[str, Any] | None,
                oracle_field: str | None) -> float | None:
    """A numeric value from an executed oracle, at ``oracle_field`` or its first
    numeric scalar. Deterministic; None when no numeric value is available.
    Booleans are never treated as numbers."""
    if result is None:
        return None
    if oracle_field:
        v = result.get(oracle_field)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v)
    for v in result.values():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return float(v)
    return None


@dataclass
class AnswerKey:
    """The verified answer key for one item — evidence-derived, not authored.

    ``source``: ``oracle`` (T3 executed) | ``citation`` (T2 required corpus id)
    | ``none`` (no usable key -> the item degrades / flags). ``numeric_key`` is
    the numeric key when the item is quantitative and the oracle ran;
    ``citation_ids`` are the corpus ids a factual item must cite (validated
    against the spec). ``issues`` are named, first-class — never masked.
    """
    source: str
    numeric_key: float | None = None
    oracle: str | None = None
    oracle_field: str | None = None
    oracle_ran: bool = False
    citation_ids: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "numeric_key": self.numeric_key,
            "oracle": self.oracle,
            "oracle_field": self.oracle_field,
            "oracle_ran": self.oracle_ran,
            "citation_ids": list(self.citation_ids),
            "issues": list(self.issues),
        }


def build_answer_key(item: dict[str, Any], spec: CurriculumSpec) -> AnswerKey:
    """Resolve the item's verified answer key from the spec + the executed oracle.

    A **quantitative** item (``quantitative: true`` on a quiz, or any ``oracle``
    binding) requires a *run* T3 oracle to be usable — otherwise the key is
    ``none`` and the item degrades to "worked example + citations" (never a
    fabricated runnable key). A **factual** item requires at least one corpus id
    that exists in the spec.
    """
    node = spec.node(item.get("node_id") or "")
    issues: list[str] = []
    quantitative = bool(item.get("quantitative"))
    # A worked solution IS the runnable key (SPEC §2, T4): its completeness is
    # about the oracle output, so it resolves a numeric key the same way a
    # quantitative quiz does — even though its field set carries no flag.
    if item.get("kind") == KIND_WORKED:
        quantitative = True
    oracle = item.get("oracle")

    # The key must match the node's own oracle (no cross-binding of keys).
    if oracle and node is not None and node.oracle and oracle != node.oracle:
        issues.append(
            f"oracle-binding: item binds '{oracle}' but the node's oracle is "
            f"'{node.oracle}' — the key must match the node")

    if node is None:
        issues.append(f"node-unknown: node_id={item.get('node_id')!r} is not in this spec")

    binding_invalid = node is None or any(
        issue.startswith("oracle-binding") for issue in issues)
    result = None if binding_invalid else _oracle_result(oracle)
    numeric = _numeric_at(result, item.get("oracle_field"))

    cits = item.get("required_citations") or []
    if not isinstance(cits, list) or not all(isinstance(c, str) for c in cits):
        issues.append("citations-type: required_citations must be a list of ids")
        cits = []
    known_citations = {source.id for source in spec.corpus}
    unknown_citations = sorted(set(cits) - known_citations)
    for cid in unknown_citations:
        issues.append(f"citation-unknown:{cid} (not a corpus id in this spec)")
    cits = [cid for cid in cits if cid in known_citations]
    if node is not None:
        allowed_for_node = set(node.grounding_corpus)
        unbound = sorted(set(cits) - allowed_for_node)
        for cid in unbound:
            issues.append(f"citation-binding: {cid} is not cited by node {node.id}")
        cits = [cid for cid in cits if cid in allowed_for_node]
    unusable = []
    for cid in cits:
        source = next((source for source in spec.corpus if source.id == cid), None)
        if source is not None and source.status in {"dead", "needs-render"}:
            unusable.append(cid)
    if unusable:
        issues.append("citation-unusable: source(s) are not live: "
                      + ", ".join(unusable))
        cits = [cid for cid in cits if cid not in unusable]

    trusted_answer = item.get("trusted_answer")
    if trusted_answer is not None and quantitative:
        issues.append("trusted-answer-quantitative: trusted conceptual keys cannot be quantitative")
        trusted_answer = None

    # A quantitative item cannot silently fall back to a citation-only key: the
    # question's answer key must be the node-bound executable oracle.
    has_numeric = quantitative and numeric is not None and not binding_invalid
    has_factual = bool(cits)
    if trusted_answer is not None and not binding_invalid and not issues:
        source = "trusted-answer"
    elif has_numeric:
        source = "oracle"
    elif has_factual and not quantitative and not binding_invalid:
        source = "citation"
    else:
        source = "none"
        if quantitative and numeric is None:
            issues.append("no-t3-key: quantitative item with no usable T3 oracle "
                          "value — degrades to worked example + citations")

    return AnswerKey(
        source=source,
        numeric_key=numeric if has_numeric else None,
        oracle=oracle,
        oracle_field=item.get("oracle_field"),
        oracle_ran=result is not None,
        citation_ids=list(cits),
        issues=issues,
    )


# ---------- citation / rubric / misconception helpers -------------------------
def _citations_cover(citations: list[dict[str, Any]], corpus_ids: list[str]) -> bool:
    """True iff the learner's citations cover at least one required corpus id.
    Case-insensitive on the corpus id (a stable reference, not a claim)."""
    if not corpus_ids:
        return False
    wanted = {c.lower() for c in corpus_ids}
    for c in citations:
        sid = (c.get("source_id") or "").lower()
        if sid in wanted:
            return True
    return False


def _numeric_response(text: str) -> tuple[float | None, bool]:
    """Return one learner-supplied number and whether the text is ambiguous."""
    values = []
    for match in re.finditer(r"(?<![A-Za-z_|])[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text or ""):
        try:
            values.append(float(match.group(0)))
        except ValueError:
            continue
    if len(values) != 1:
        return None, bool(values)
    return values[0], False


def _rubric_score(text: str, points: list[str]) -> tuple[float, list[str]]:
    """Deterministic rubric: the fraction of required concept points present in
    the learner's text. Points are the item's ``rubric_points`` (spec-curated),
    so the rubric is deterministic, not an LLM judgment. Case-insensitive,
    substring-based (a point is 'covered' when it appears in the text)."""
    if not points:
        return 0.0, []
    tl = (text or "").lower()
    hits = [p for p in points if p and p.lower() in tl]
    return (len(hits) / len(points)), hits


def _ambiguous_teachback(text: str, hits: list[str]) -> bool:
    """Do not turn hedged or explicitly uncertain overlap into mastery evidence."""
    if not hits:
        return False
    lower = (text or "").casefold()
    return any(marker in lower for marker in (
        "i think", "i guess", "maybe", "not sure", "might be", "could be",
        "i'm unsure", "uncertain",
    ))


def _teachback_misconceptions(text: str, spec: CurriculumSpec,
                              node: Node | None) -> list[dict[str, Any]]:
    """Map the learner's teach-back to *validated* misconception references via
    P1.3's deterministic keyword map — the mapping is the contract; free text is
    only evidence. Scoped to the item's node (a validated T1 reference), never a
    whole-spec free-text guess."""
    from . import learner_events as le
    if not (text or "").strip() or node is None:
        return []
    candidates = le.misconception_candidates(text, spec, node.id)
    # Keyword overlap is only a candidate generator. Require a high overlap
    # and do not call a corrected negation a misconception trigger.
    if " not " in f" {(text or '').casefold()} ":
        return []
    return [candidate for candidate in candidates if candidate["score"] >= 0.75]


# ---------- the grading boundary (pure, deterministic, non-LLM) ---------------
def grade(item: dict[str, Any], response: LearnerResponse | None,
          spec: CurriculumSpec) -> dict[str, Any]:
    """Grade one assessment item against a learner response. Pure and
    deterministic — it inspects the response + the verified key and applies
    rules; it calls no model, parses no draft internals, and never silently
    scores an unsupported/ambiguous item.

    Returns a report dict: ``item_id`` / ``node_id`` / ``kind``, the
    ``answer_key``, the ``verdict``, the ``score`` (float in ``[0, 1]`` when a
    score applies, else ``None``), the named ``issues``, and (for teach-back)
    the ``rubric`` score and the ``misconceptions`` the learner triggered.
    """
    if not isinstance(item, dict):
        return _unscorable({}, spec, ["item-not-mapping: item must be a mapping"])
    shape_issues = _grade_shape_issues(item, spec)
    if shape_issues:
        return _unscorable(item, spec, shape_issues)
    kind = item.get("kind")
    if kind not in KINDS:
        return _unscorable(item, spec, [
            f"kind-unknown: {kind!r} (known: {list(KINDS)})"])

    node = spec.node(item.get("node_id") or "")
    key = build_answer_key(item, spec)
    issues: list[str] = list(key.issues)

    # No response at all -> nothing to grade (honest, not a silent pass/fail).
    if response is None:
        issues.append("no-response: no learner attempt to grade against")
        return _report(item, node, key, NOT_SCORED, None, issues)

    if node is None:
        # No validated node reference -> nothing may be scored on.
        issues.append(f"node-unknown: node_id={item.get('node_id')!r} is not in this spec")
        return _report(item, node, key, FLAGGED, None, issues)

    grounded = bool(response.grounded)

    if kind == KIND_WORKED:
        return _grade_worked(item, node, key, issues)

    if kind == KIND_QUIZ:
        return _grade_quiz(item, node, key, response, grounded, issues)

    # KIND_TEACHBACK — rubric + misconception mapping over the learner's text.
    return _grade_teachback(item, node, key, response, spec, issues)


def _grade_shape_issues(item: dict[str, Any], spec: CurriculumSpec) -> list[str]:
    """Safety validation used by grading, without requiring a live registry.

    ``validate_item`` additionally checks registry membership for worked items;
    grade must also remain usable with test/local oracle adapters, so this
    boundary validates shape and spec bindings while the executed run remains
    the authority on whether an oracle is usable.
    """
    issues: list[str] = []
    if item.get("v") != SCHEMA_VERSION:
        issues.append(f"version-unknown: v={item.get('v')!r}")
    kind = item.get("kind")
    if kind not in KINDS:
        return issues + [f"kind-unknown: {kind!r} (known: {list(KINDS)})"]
    allowed = _CORE_FIELDS[kind]
    allowed_with_optional = allowed | _OPTIONAL_FIELDS
    issues.extend(f"field-extra:{key}" for key in sorted(set(item) - allowed_with_optional))
    issues.extend(f"field-missing:{key}" for key in sorted(allowed - set(item)))
    if set(item) - allowed_with_optional or allowed - set(item):
        return issues
    node_id = item.get("node_id")
    if not isinstance(node_id, str) or not node_id:
        issues.append("field-type:node_id (must be a non-empty string)")
    elif spec.node(node_id) is None:
        issues.append(f"node-unknown: node_id={node_id!r} is not in this spec")
    if not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
        issues.append("prompt-empty: the prompt is the question/explanation request")
    cits = item.get("required_citations")
    if not isinstance(cits, list) or not all(isinstance(cid, str) for cid in cits):
        issues.append("field-type:required_citations (must be a list of ids)")
    else:
        known = {source.id for source in spec.corpus}
        issues.extend(f"citation-unknown:{cid} (not a corpus id in this spec)"
                      for cid in cits if cid not in known)
    if kind == KIND_QUIZ and not isinstance(item.get("quantitative"), bool):
        issues.append("field-type:quantitative (must be a bool)")
    if kind == KIND_QUIZ and "answer_options" in item:
        options = item["answer_options"]
        if (not isinstance(options, list) or not options
                or not all(isinstance(option, str) and option.strip() for option in options)):
            issues.append("field-type:answer_options (must be a non-empty list of strings)")
    if kind == KIND_QUIZ and "trusted_answer" in item:
        answers = item["trusted_answer"]
        if (not isinstance(answers, list) or not answers
                or not all(isinstance(answer, str) and answer.strip() for answer in answers)):
            issues.append("field-type:trusted_answer (must be a non-empty list of strings)")
    if kind == KIND_TEACHBACK:
        points = item.get("rubric_points")
        if not isinstance(points, list) or not all(isinstance(point, str) for point in points):
            issues.append("field-type:rubric_points (must be a list of concept strings)")
    return issues


def _grade_quiz(item: dict[str, Any], node: Node, key: AnswerKey,
                response: LearnerResponse, grounded: bool,
                issues: list[str]) -> dict[str, Any]:
    """A quiz is graded against its verified key. Honesty: an unverified attempt
    never scores a positive grade (P1.3 rule); a grounded attempt is scored by
    the key (oracle match certified by the gate, or T2 citation coverage)."""
    # No usable key at all -> the item cannot be scored.
    if key.source == "none":
        if bool(item.get("quantitative")):
            issues.append("no-t3: quantitative item with no usable T3 oracle — "
                          "degraded to worked example + citations, not a runnable key")
            return _report(item, node, key, DEGRADED, None, issues)
        issues.append("no-key: the item has no verified answer key "
                      "(no T3 binding, no required citations) — flagged, not scored")
        return _report(item, node, key, FLAGGED, None, issues)

    # Unverified attempt: never awards (a positive or partial grade alike).
    if not grounded:
        issues.append("unverified-attempt: the gate did not certify this attempt; "
                      "no positive grade (honesty invariant)")
        return _report(item, node, key, INCORRECT, 0.0, issues)

    if key.source == "trusted-answer":
        accepted = item.get("trusted_answer")
        if not isinstance(accepted, list) or not accepted:
            issues.append("trusted-answer-invalid: no server-owned accepted response")
            return _report(item, node, key, FLAGGED, None, issues)
        answer = re.sub(r"\s+", " ", response.text.strip()).casefold()
        if not answer:
            issues.append("unsupported-response: no answer was supplied")
            return _report(item, node, key, FLAGGED, None, issues)
        if re.search(r"\b(?:or|either|and)\b", answer):
            issues.append("ambiguous-response: multiple conceptual answers were supplied")
            return _report(item, node, key, FLAGGED, None, issues)
        accepted_normalized = {
            re.sub(r"\s+", " ", value.strip()).casefold()
            for value in accepted if isinstance(value, str) and value.strip()
        }
        if answer in accepted_normalized:
            return _report(item, node, key, CORRECT, 1.0, issues)
        issues.append("answer-mismatch: response does not match the authored template key")
        return _report(item, node, key, INCORRECT, 0.0, issues)

    if key.source == "oracle":
        value, ambiguous = _numeric_response(response.text)
        if ambiguous:
            issues.append("ambiguous-response: more than one numeric value was supplied")
            return _report(item, node, key, FLAGGED, None, issues)
        if value is None:
            issues.append("unsupported-response: no numeric value was supplied")
            return _report(item, node, key, FLAGGED, None, issues)
        if value == key.numeric_key:
            return _report(item, node, key, CORRECT, 1.0, issues)
        issues.append("numeric-mismatch: answer does not equal the executed key")
        return _report(item, node, key, INCORRECT, 0.0, issues)

    # key.source == "citation": grounded, so check the citation set.
    covered = _citations_cover(response.citations, key.citation_ids)
    if covered:
        return _report(item, node, key, CORRECT, 1.0, issues)
    issues.append(
        f"citation-coverage: the learner's citations "
        f"{sorted({(c.get('source_id') or '') for c in response.citations})} do "
        f"not cover the required {sorted(key.citation_ids)}")
    return _report(item, node, key, PARTIAL, 0.5, issues)


def _grade_teachback(item: dict[str, Any], node: Node, key: AnswerKey,
                     response: LearnerResponse, spec: CurriculumSpec,
                     issues: list[str]) -> dict[str, Any]:
    """A teach-back is retrieval practice: the learner explains, graded on a
    deterministic rubric (required concept points) + validated misconception
    avoidance. Unsupported/ambiguous (empty text, no rubric to grade against) is
    flagged, never scored."""
    text = response.text or ""
    if not text.strip():
        issues.append("empty-teachback: the learner provided no explanation to grade")
        return _report(item, node, key, FLAGGED, None, issues)

    points = item.get("rubric_points") or []
    if not points:
        # No rubric and nothing to grade against -> ambiguous, not a silent 0.
        issues.append("no-rubric: the teach-back item defines no rubric points; "
                      "ambiguous grading is flagged, not scored")
        return _report(item, node, key, FLAGGED, None, issues)

    score, hits = _rubric_score(text, points)

    if not response.grounded:
        issues.append("unverified-attempt: the gate did not certify this attempt; "
                      "no positive grade (honesty invariant)")
        return _report(item, node, key, INCORRECT, 0.0, issues,
                       extra={"rubric": 0.0, "rubric_hits": [],
                              "misconceptions": []})
    if _ambiguous_teachback(text, hits):
        issues.append("ambiguous-teachback: hedged language makes rubric overlap "
                      "insufficient evidence")
        return _report(item, node, key, FLAGGED, None, issues,
                       extra={"rubric": None, "rubric_hits": hits,
                              "misconceptions": []})

    # A validated misconception fired on the explanation -> deterministic penalty.
    mis = _teachback_misconceptions(text, spec, node)
    if mis:
        score *= MISCONCEPTION_FACTOR
        issues.append(
            "misconception-triggered: the explanation triggered "
            f"{[m['misconception_id'] for m in mis]} (validated T1 reference)")

    # Required citations (when present) must be covered by the learner's spans.
    if key.citation_ids and not _citations_cover(response.citations, key.citation_ids):
        score *= MISSING_CITATION_FACTOR
        issues.append("citation-coverage: required citations not covered by the "
                      "learner's explanation")

    score = round(score, 4)
    if score >= RUBRIC_CORRECT:
        verdict = CORRECT
    elif score >= RUBRIC_PARTIAL:
        verdict = PARTIAL
    else:
        verdict = INCORRECT
    return _report(item, node, key, verdict, score, issues,
                   extra={"rubric": round(score, 4), "rubric_hits": hits,
                          "misconceptions": mis})


def _grade_worked(item: dict[str, Any], node: Node, key: AnswerKey,
                  issues: list[str]) -> dict[str, Any]:
    """A worked solution is a T4 *answer key*: it must bind to an oracle that
    RAN to be a valid, reproducible key. Ran -> correct (a verified key); ran
    but incomplete -> partial; no oracle bound -> flagged; oracle bound but did
    not run (no T3) -> degraded (worked example + citations, not a runnable key).
    A worked item's validity is about the *key*, so it does not grade a learner
    response's groundedness."""
    if not key.oracle:
        issues.append("no-oracle: a worked solution must bind a T3 oracle to be a "
                      "verified key — flagged, not scored")
        return _report(item, node, key, FLAGGED, None, issues)
    if key.oracle_ran:
        if key.numeric_key is not None or not item.get("oracle_field"):
            return _report(item, node, key, CORRECT, 1.0, issues)
        issues.append("incomplete-key: the oracle ran but the bound field is not "
                      "a usable numeric value")
        return _report(item, node, key, PARTIAL, 0.5, issues)
    issues.append("no-t3-run: the bound oracle did not run (no usable T3) — "
                  "degraded to a worked example + citations, not a runnable key")
    return _report(item, node, key, DEGRADED, None, issues)


def _unscorable(item: dict[str, Any], spec: CurriculumSpec,
                issues: list[str]) -> dict[str, Any]:
    node = spec.node(item.get("node_id") or "")
    key = AnswerKey(source="none", issues=list(issues))
    return _report(item, node, key, FLAGGED, None, issues)


def _report(item: dict[str, Any], node: Node | None, key: AnswerKey,
            verdict: str, score: float | None, issues: list[str],
            extra: dict[str, Any] | None = None) -> dict[str, Any]:
    rep: dict[str, Any] = {
        "item_id": item.get("id"),
        "node_id": item.get("node_id"),
        "kind": item.get("kind"),
        "verdict": verdict,
        "score": score,
        "answer_key": key.to_dict(),
        "issues": list(issues),
    }
    if extra:
        rep.update(extra)
    return rep


# ---------- item factory + validation (deterministic, spec-checked) ----------
def make_item(kind: str, node_id: str, prompt: str, *,
              quantitative: bool = False,
              required_citations: list[str] | None = None,
              oracle: str | None = None,
              oracle_field: str | None = None,
              rubric_points: list[str] | None = None,
              answer_options: list[str] | None = None,
              trusted_answer: list[str] | None = None,
              item_id: str | None = None) -> dict[str, Any]:
    """Build a versioned assessment item (T4). The drafter never self-grades:
    this only assembles the item; ``build_answer_key`` / ``grade`` decide.
    Unknown kinds are rejected up front (a first-class error, not a silent pass)."""
    if kind not in KINDS:
        raise ValueError(f"unknown item kind {kind!r} (known: {list(KINDS)})")
    item: dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "kind": kind,
        "id": item_id or f"{kind}-{node_id}",
        "node_id": node_id,
        "prompt": prompt,
    }
    # Emit exactly this kind's fields — a kind-specific field on the wrong kind
    # would be a shape violation, so the factory must not add it.
    if kind == KIND_QUIZ:
        item["quantitative"] = bool(quantitative)
        item["required_citations"] = list(required_citations or [])
        item["oracle"] = oracle
        item["oracle_field"] = oracle_field
        if answer_options is not None:
            item["answer_options"] = list(answer_options)
        if trusted_answer is not None:
            item["trusted_answer"] = list(trusted_answer)
    elif kind == KIND_TEACHBACK:
        item["required_citations"] = list(required_citations or [])
        item["rubric_points"] = list(rubric_points or [])
    else:  # KIND_WORKED — a worked key binds a T3 oracle
        item["oracle"] = oracle
        item["oracle_field"] = oracle_field
        item["required_citations"] = list(required_citations or [])
    return item


def validate_item(item: dict[str, Any], spec: CurriculumSpec) -> tuple[bool, list[str]]:
    """Validate one item against the spec. Returns ``(ok, issues)``.

    Named, first-class issues: ``item-not-mapping``, ``version-unknown``,
    ``kind-unknown``, ``field-missing:<f>``, ``field-extra:<f>``,
    ``field-type:<f>``, ``node-unknown``, ``prompt-empty``, ``oracle-unknown``,
    ``oracle-binding``, ``citation-unknown:<id>``, ``worked-needs-oracle``.
    A valid item is a mapping with exactly its kind's fields, a known version,
    and a real node — nothing free-form may be scored without one.
    """
    if not isinstance(item, dict):
        return False, ["item-not-mapping: item must be a mapping"]
    issues: list[str] = []

    v = item.get("v")
    if v != SCHEMA_VERSION:
        issues.append(f"version-unknown: v={v!r} (known: {SCHEMA_VERSION!r})")

    kind = item.get("kind")
    if kind not in KINDS:
        issues.append(f"kind-unknown: {kind!r} (known: {list(KINDS)})")
        return False, issues

    # Strict shape: exactly the declared fields, no more, no less.
    allowed = _CORE_FIELDS[kind]
    allowed_with_optional = allowed | _OPTIONAL_FIELDS
    for f in sorted(set(item) - allowed_with_optional):
        issues.append(f"field-extra:{f}")
    for f in sorted(allowed - set(item)):
        issues.append(f"field-missing:{f}")
    if set(item) - allowed_with_optional or allowed - set(item):
        return False, issues

    # Common fields.
    nid = item.get("node_id")
    if not isinstance(nid, str) or not nid:
        issues.append("field-type:node_id (must be a non-empty string)")
        return False, issues
    node = spec.node(nid)
    if node is None:
        issues.append(f"node-unknown: node_id={nid!r} is not in this spec")
        return False, issues

    prompt = item.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        issues.append("prompt-empty: the prompt is the question/explanation request")

    # Citations must be a list of corpus ids that exist in this spec.
    def _check_citations() -> None:
        cits = item.get("required_citations")
        if cits is None:
            issues.append("field-type:required_citations (must be a list of ids)")
            return
        if not isinstance(cits, list) or not all(isinstance(c, str) for c in cits):
            issues.append("field-type:required_citations (must be a list of ids)")
            return
        known = {c.id for c in spec.corpus}
        for cid in cits:
            if cid not in known:
                issues.append(f"citation-unknown:{cid} (not a corpus id in this spec)")
            elif cid not in node.grounding_corpus:
                issues.append(f"citation-binding:{cid} (not cited by node {node.id})")
            else:
                source = next(source for source in spec.corpus if source.id == cid)
                if source.status in {"dead", "needs-render"}:
                    issues.append(f"citation-unusable:{cid} (source is {source.status})")

    if kind == KIND_QUIZ:
        if not isinstance(item.get("quantitative"), bool):
            issues.append("field-type:quantitative (must be a bool)")
        oracle = item.get("oracle")
        if oracle and node.oracle and oracle != node.oracle:
            issues.append(f"oracle-binding: '{oracle}' != node oracle '{node.oracle}'")
        if oracle:
            from . import oracles
            if oracle not in oracles.REGISTRY:
                issues.append(f"oracle-unknown: '{oracle}' is not in the oracle registry")
        _check_citations()
        if "answer_options" in item:
            options = item["answer_options"]
            if (not isinstance(options, list) or not options
                    or not all(isinstance(option, str) and option.strip() for option in options)):
                issues.append("field-type:answer_options (must be a non-empty list of strings)")
        if "trusted_answer" in item:
            answers = item["trusted_answer"]
            if (not isinstance(answers, list) or not answers
                    or not all(isinstance(answer, str) and answer.strip() for answer in answers)):
                issues.append("field-type:trusted_answer (must be a non-empty list of strings)")
            if item.get("quantitative"):
                issues.append("trusted-answer-quantitative: trusted conceptual keys cannot be quantitative")
    elif kind == KIND_TEACHBACK:
        _check_citations()
        rp = item.get("rubric_points")
        if not isinstance(rp, list) or not all(isinstance(p, str) for p in rp):
            issues.append("field-type:rubric_points (must be a list of concept strings)")
    else:  # KIND_WORKED
        oracle = item.get("oracle")
        if not isinstance(oracle, str) or not oracle:
            issues.append("worked-needs-oracle: a worked solution must bind a T3 oracle")
        else:
            if node.oracle and oracle != node.oracle:
                issues.append(f"oracle-binding: '{oracle}' != node oracle '{node.oracle}'")
            from . import oracles
            if oracle not in oracles.REGISTRY:
                issues.append(f"oracle-unknown: '{oracle}' is not in the oracle "
                              f"registry — the key would not run")
        _check_citations()

    return (not issues), issues


# ---------- the one-call boundary (pure; no compile, no I/O, no state) --------
def assess(spec: CurriculumSpec, items: list[dict[str, Any]],
           responses: dict[str, Any] | None = None) -> dict[str, Any]:
    """Grade a set of assessment items against their responses. Pure.

    ``responses`` maps ``item_id -> LearnerResponse`` (or an ``engine.tutor``
    ``Answer`` / mapping, auto-adapted). Items with no response are reported
    ``not-scored`` — never scored. The result is the honest, diffable T4 report:
    per-item verdicts/scores/keys, the summary counts, and the schema version.

    This module never compiles, persists, or mutates the spec: the human
    curriculum gate (P1.0) and the learner-state gate (P1.3) are the boundaries
    this report feeds — not replaces.
    """
    responses = responses or {}
    results: list[dict[str, Any]] = []
    for item in items:
        raw = responses.get(item.get("id"))
        if isinstance(raw, LearnerResponse):
            response = raw
        else:
            response = response_from_answer(raw)
        results.append(grade(item, response, spec))

    def count(verdict: str) -> int:
        return sum(1 for r in results if r["verdict"] == verdict)

    return {
        "subject": spec.subject,
        "schema_version": SCHEMA_VERSION,
        "items_total": len(results),
        "summary": {
            CORRECT: count(CORRECT),
            PARTIAL: count(PARTIAL),
            INCORRECT: count(INCORRECT),
            FLAGGED: count(FLAGGED),
            DEGRADED: count(DEGRADED),
            NOT_SCORED: count(NOT_SCORED),
        },
        "results": results,
    }
