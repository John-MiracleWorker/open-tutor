"""P1.3 — Misconception events and the learner-state update contract.

Deterministic and offline. The learner runtime boundary (learner_events.py)
is exercised against synthetic specs and, for the end-to-end path, the real
quantum-computing generator spec with injected corpus text and monkeypatched
oracles — no model, no network, no clock.

Covered, per the card:
  - known mapping          -> utterance maps to the right node/misconception ids
  - unknown misconception  -> rejected with a named issue, never applied
  - duplicate / idempotent -> replaying an event is a no-op (no double state)
  - unverified answers     -> never falsely award mastery (delta is 0.0)
  - versioned shapes       -> SCHEMA_VERSION pinned; unknown versions rejected
  - deterministic rules    -> named mastery-delta rules, bounded, reproducible
  - free-form LLM guard    -> an LLM-labelled detector is rejected
  - engine integration     -> events_from_answer builds the contract from an
                              Answer object; no-node answers emit no events
"""
from __future__ import annotations

import pytest

from open_tutor import engine
from open_tutor import learner_events as le
from open_tutor.spec import CorpusSource, CurriculumSpec, Misconception, Node

# ---------- synthetic spec (the mapping fixture) ------------------------------
QUBIT_M1_TEXT = "A qubit is just a bit that is either zero or one that we don't know which."
SUPER_M1_TEXT = "Superposition means the qubit is secretly zero or one and we just haven't looked."


def make_spec() -> CurriculumSpec:
    corpus = [
        CorpusSource(id="src-a", name="Source A", url="https://a.example/qubit"),
        CorpusSource(id="src-b", name="Source B", url="https://b.example/qubit"),
    ]
    node = Node(
        id="qubit", title="The Qubit",
        defn="A qubit is a two-level quantum system with basis states.",
        misconceptions=[Misconception(id="qubit-m1", text=QUBIT_M1_TEXT),
                        Misconception(id="qubit-m2", text="A qubit is a tiny spinning magnet.")],
        covers_keywords=["qubit"],
        grounding_corpus=["src-a", "src-b"],
        oracle="fake_qubit",
    )
    super_node = Node(
        id="superposition", title="Superposition",
        defn="A qubit in a linear combination of basis states.",
        prereqs=["qubit"],
        misconceptions=[Misconception(id="super-m1", text=SUPER_M1_TEXT)],
        covers_keywords=["superposition"],
        grounding_corpus=["src-a"],
    )
    return CurriculumSpec(subject="test-subject", title="Test", corpus=corpus,
                          nodes=[node, super_node])


QUBIT_TEXT = "\n\n".join(
    f"A qubit is the fundamental two-level quantum unit, the quantum analogue of "
    f"the classical bit, realised by a pair of basis states labelled zero and one. "
    f"Paragraph {i} develops the qubit with further context so the span stays long."
    for i in range(8)
)


@pytest.fixture
def good_oracle(monkeypatch):
    monkeypatch.setattr(
        engine, "run_oracle",
        lambda name: {"ok": True, "result": {"P(|0>)": 0.5, "P(|1>)": 0.5,
                                             "normalized": True}})


def attempt_event(node_id="qubit", grounded=True, **over) -> dict:
    e = {
        "v": le.SCHEMA_VERSION,
        "kind": le.KIND_ATTEMPT,
        "subject": "test-subject",
        "node_id": node_id,
        "question": "What is a qubit?",
        "answer_status": "grounded" if grounded else "unverified",
        "grounded": grounded,
        "quantitative": False,
        "citations_used": [1, 2],
        "oracle_status": "not-required",
    }
    e.update(over)
    return e


def misconception_event(mid="qubit-m1", node_id="qubit", **over) -> dict:
    e = {
        "v": le.SCHEMA_VERSION,
        "kind": le.KIND_MISCONCEPTION,
        "subject": "test-subject",
        "node_id": node_id,
        "misconception_id": mid,
        "evidence": "A qubit is just a bit we don't know which.",
        "detected_by": le.DETECTOR_KEYWORD_MAP,
    }
    e.update(over)
    return e


def mastery_event(node_id="qubit", delta=0.1, **over) -> dict:
    e = {
        "v": le.SCHEMA_VERSION,
        "kind": le.KIND_MASTERY_DELTA,
        "subject": "test-subject",
        "node_id": node_id,
        "delta": delta,
        "reason": le.RULE_GROUNDED_ATTEMPT,
        "attempts_before": 0,
    }
    e.update(over)
    return e


# ---------- 1. known mapping (deterministic keyword map) ----------------------
def test_known_mapping_finds_the_exact_misconception():
    spec = make_spec()
    rows = le.misconception_candidates(
        "So a qubit is just a bit that is either zero or one and we don't know which.",
        spec)
    top = rows[0]
    assert top["node_id"] == "qubit"
    assert top["misconception_id"] == "qubit-m1"
    assert top["score"] >= le._DETECT_MIN_SCORE
    assert "qubit" in top["matched"]


def test_known_mapping_respects_the_node_scope():
    spec = make_spec()
    # restricted to the superposition node: the qubit-m1 text must NOT appear
    rows = le.misconception_candidates(
        "So a qubit is just a bit that is either zero or one and we don't know which.",
        spec, node_id="superposition")
    assert rows == []


def test_mapping_is_deterministic_and_ranked():
    spec = make_spec()
    utter = "A qubit is just a bit we don't know which, and superposition " \
            "means it is secretly zero or one and we just haven't looked."
    a = le.misconception_candidates(utter, spec)
    b = le.misconception_candidates(utter, spec)
    assert a == b
    assert a[0]["misconception_id"] in ("qubit-m1", "super-m1")
    scores = [r["score"] for r in a]
    assert scores == sorted(scores, reverse=True)


def test_weak_overlap_does_not_map():
    spec = make_spec()
    # shares 'qubit' but far too little of the distinctive content
    rows = le.misconception_candidates("Tell me about the qubit.", spec)
    assert all(r["misconception_id"] != "qubit-m1" for r in rows)


def test_event_from_candidate_carries_validated_reference():
    spec = make_spec()
    cand = le.misconception_candidates(
        "A qubit is just a bit that is either zero or one we don't know which.",
        spec)[0]
    ev = le.make_misconception_event(cand, spec, "learner utterance here")
    ok, issues = le.validate_event(ev, spec)
    assert ok, issues
    assert ev["detected_by"] == le.DETECTOR_KEYWORD_MAP


# ---------- 2. validation: known mapping vs. unknown rejection -----------------
def test_valid_attempt_event_passes():
    spec = make_spec()
    ok, issues = le.validate_event(attempt_event(), spec)
    assert ok, issues


def test_unknown_node_is_rejected():
    spec = make_spec()
    ok, issues = le.validate_event(attempt_event(node_id="does-not-exist"), spec)
    assert not ok
    assert any(i.startswith("node-unknown") for i in issues)


def test_unknown_misconception_is_rejected():
    spec = make_spec()
    # 'qubit-m99' is not a misconception of node 'qubit' -> must be rejected
    ok, issues = le.validate_event(misconception_event(mid="qubit-m99"), spec)
    assert not ok
    assert any(i.startswith("misconception-unknown") and "qubit-m99" in i
               for i in issues)
    # and the log refuses it: nothing applied
    log = le.EventLog(spec)
    res = log.record(misconception_event(mid="qubit-m99"))
    assert res.status == "rejected"
    assert log.entries == []
    assert log.misconceptions_of("qubit") == []
    assert log.rejections and any("misconception-unknown" in i
                                  for i in log.rejections[0]["issues"])


def test_misconception_on_wrong_node_is_rejected():
    spec = make_spec()
    # qubit-m1 exists, but NOT on node 'superposition'
    ok, issues = le.validate_event(misconception_event(node_id="superposition"), spec)
    assert not ok
    assert any(i.startswith("misconception-unknown") for i in issues)


def test_missing_field_is_rejected():
    spec = make_spec()
    e = attempt_event()
    del e["node_id"]
    ok, issues = le.validate_event(e, spec)
    assert not ok
    assert any(i.startswith("field-missing:node_id") for i in issues)


def test_extra_field_is_rejected():
    spec = make_spec()
    ok, issues = le.validate_event(attempt_event(extra="self-assessed: i feel smart"), spec)
    assert not ok
    assert any(i.startswith("field-extra:extra") for i in issues)


def test_unknown_schema_version_is_rejected():
    spec = make_spec()
    ok, issues = le.validate_event(attempt_event(v="99"), spec)
    assert not ok
    assert any(i.startswith("version-unknown") for i in issues)


def test_subject_mismatch_is_rejected():
    spec = make_spec()
    ok, issues = le.validate_event(attempt_event(subject="other-subject"), spec)
    assert not ok
    assert any(i.startswith("subject-mismatch") for i in issues)


def test_llm_self_assessment_cannot_mutate_state():
    spec = make_spec()
    # a free-form LLM label is not a validated reference: rejected, named
    ev = misconception_event(detected_by="llm-self-assessment: 'the student seems to get it'")
    ok, issues = le.validate_event(ev, spec)
    assert not ok
    assert any(i.startswith("detector-unknown") for i in issues)


def test_non_finite_delta_is_rejected():
    spec = make_spec()
    ok, issues = le.validate_event(mastery_event(delta=float("nan")), spec)
    assert not ok
    assert any(i.startswith("delta-not-finite") for i in issues)


def test_validate_is_pure_and_deterministic():
    spec = make_spec()
    ev = misconception_event()
    r1 = le.validate_event(ev, spec)
    r2 = le.validate_event(ev, spec)
    assert r1 == r2


# ---------- 3. duplicates / idempotent handling --------------------------------
def test_duplicate_event_is_a_noop():
    spec = make_spec()
    log = le.EventLog(spec)
    ev = attempt_event()
    first = log.record(ev)
    second = log.record(ev)
    assert first.status == "recorded"
    assert second.status == "duplicate"
    assert second.event_id == first.event_id
    # state changed exactly once
    assert log.attempts.get("qubit") == 1
    assert len(log.entries) == 1


def test_duplicate_misconception_not_double_counted():
    spec = make_spec()
    log = le.EventLog(spec)
    ev = misconception_event()
    log.record(ev)
    assert log.record(ev).status == "duplicate"
    assert log.misconceptions_of("qubit") == ["qubit-m1"]


def test_replay_of_a_whole_stream_is_idempotent():
    spec = make_spec()
    log = le.EventLog(spec)
    stream = [
        attempt_event(),
        misconception_event(),
        mastery_event(delta=le.RULE_DELTAS[le.RULE_GROUNDED_ATTEMPT]),
    ]
    first = log.record_events(stream)
    second = log.record_events(stream)  # full replay
    assert log.attempts.get("qubit") == 1
    assert log.misconceptions_of("qubit") == ["qubit-m1"]
    assert first[-1].status == "rejected"  # no assessment provenance
    assert second[-1].status == "rejected"
    assert log.mastery_of("qubit") == 0.0
    assert len(log.entries) == 2


def test_event_id_is_stable_and_content_derived():
    a = attempt_event()
    b = attempt_event()
    assert le.event_id(le.canonical_payload(a)) == le.event_id(le.canonical_payload(b))
    # a different question is a different logical event
    c = attempt_event(question="What is superposition?")
    assert le.event_id(le.canonical_payload(c)) != le.event_id(le.canonical_payload(a))


def test_caller_supplied_event_id_is_never_trusted():
    spec = make_spec()
    ev = attempt_event(event_id="forged-id")
    ok, issues = le.validate_event(ev, spec)
    assert ok, issues  # event_id is not a payload field: it is derived
    canonical = le.normalize_event(ev, spec)
    assert canonical["event_id"] != "forged-id"


# ---------- 4. unverified answers never award mastery ---------------------------
def test_unverified_attempt_fires_zero_delta_rule():
    r = le.mastery_delta_for_attempt(grounded=False)
    assert r["rules"] == [le.RULE_UNVERIFIED_ATTEMPT]
    assert r["delta"] == 0.0


def test_unverified_with_misconception_only_goes_negative():
    r = le.mastery_delta_for_attempt(grounded=False, misconceptions_triggered=1)
    assert le.RULE_GROUNDED_ATTEMPT not in r["rules"]
    assert r["rules"] == [le.RULE_UNVERIFIED_ATTEMPT, le.RULE_MISCONCEPTION_TRIGGERED]
    assert r["delta"] == pytest.approx(le.RULE_DELTAS[le.RULE_MISCONCEPTION_TRIGGERED])


def test_unverified_attempt_event_yields_no_mastery_in_log():
    spec = make_spec()
    log = le.EventLog(spec)
    ev = attempt_event(grounded=False, answer_status="unverified")
    assert log.record(ev).status == "recorded"
    assert log.mastery_of("qubit") == 0.0
    # the attempt IS counted (honesty: the interaction happened)
    assert log.attempts.get("qubit") == 1


def test_positive_delta_requires_a_validated_mastery_event():
    spec = make_spec()
    log = le.EventLog(spec)
    # an unverified attempt alone must never move mastery up
    log.record(attempt_event(grounded=False))
    assert log.mastery_of("qubit") == 0.0
    # only a validated mastery_delta event (named rule) may move it — and the
    # rules cap what one interaction may add
    delta = le.mastery_delta_for_attempt(grounded=True, misconceptions_triggered=2)
    log.record(mastery_event(delta=delta["delta"], reason=delta["reason"]))
    assert log.mastery_of("qubit") == pytest.approx(delta["delta"])
    assert delta["delta"] == pytest.approx(
        le.RULE_DELTAS[le.RULE_GROUNDED_ATTEMPT]
        + 2 * le.RULE_DELTAS[le.RULE_MISCONCEPTION_TRIGGERED])


def test_mastery_delta_shape_is_versioned_and_named():
    spec = make_spec()
    ev = mastery_event(delta=0.0, reason=le.RULE_UNVERIFIED_ATTEMPT)
    ok, issues = le.validate_event(ev, spec)
    assert ok, issues
    assert ev["v"] == le.SCHEMA_VERSION
    assert isinstance(ev["reason"], str) and ev["reason"]  # the named rule


# ---------- 5. versioned contract ----------------------------------------------
def test_schema_version_is_pinned():
    assert le.SCHEMA_VERSION == "1"
    assert all(e["v"] == le.SCHEMA_VERSION for e in
               (attempt_event(), misconception_event(), mastery_event()))


def test_summary_exposes_version_and_state():
    spec = make_spec()
    log = le.EventLog(spec)
    log.record(attempt_event())
    s = log.summary()
    assert s["schema_version"] == le.SCHEMA_VERSION
    assert s["subject"] == "test-subject"
    assert s["attempts"] == {"qubit": 1}
    assert s["mastery"] == {}  # no mastery_delta event was recorded
    assert s["entries"] and s["entries"][0]["event_id"]


# ---------- 6. engine integration: events_from_answer ---------------------------
def _stub_draft(question, resolution, node, retrieval, oracle):
    # a minimal claim surface so the gate has something to judge
    return ("## Claim\n"
            "A qubit is described as: “A qubit is a two-level quantum system.” [1].\n"
            "\n"
            "## Citations\n"
            "[1] Source A — https://a.example/qubit\n")


def test_events_from_grounded_answer_does_not_award_mastery(good_oracle):
    spec = make_spec()
    a = engine.tutor(spec, "What is a qubit?",
                     corpus_text={"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT},
                     draft_fn=_stub_draft)
    assert a.grounded is True
    events = le.events_from_answer(a, spec)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == le.KIND_ATTEMPT
    assert kinds == [le.KIND_ATTEMPT]
    # every event in the stream validates against the spec
    for e in events:
        ok, issues = le.validate_event(e, spec)
        assert ok, issues
    # the grounded attempt carries the gate's honest verdict
    att = events[0]
    assert att["node_id"] == "qubit"
    assert att["grounded"] is True
    assert att["answer_status"] == "grounded"


def test_events_from_unverified_answer_award_no_mastery(good_oracle):
    spec = make_spec()
    # a draft the gate cannot certify -> unverified answer
    a = engine.tutor(spec, "What is a qubit?",
                     corpus_text={"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT},
                     draft_fn=lambda q, r, n, ret, o: "I think a qubit is magic. [7]")
    assert a.grounded is False
    events = le.events_from_answer(a, spec)
    kinds = [e["kind"] for e in events]
    att = events[0]
    assert att["grounded"] is False
    assert kinds == [le.KIND_ATTEMPT]
    log = le.EventLog(spec)
    out = le.process_answer(a, spec, log)
    assert log.mastery_of("qubit") == 0.0
    assert all(r.status == "recorded" for r in out["results"])


def test_events_with_learner_misconception_are_validated(good_oracle):
    spec = make_spec()
    a = engine.tutor(spec, "What is a qubit?",
                     corpus_text={"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT},
                     draft_fn=_stub_draft)
    utterance = "I thought a qubit is just a bit that is either zero or one " \
                "and we don't know which one it is."
    events = le.events_from_answer(a, spec, learner_utterance=utterance)
    mis = [e for e in events if e["kind"] == le.KIND_MISCONCEPTION]
    assert len(mis) == 1
    assert mis[0]["misconception_id"] == "qubit-m1"
    assert mis[0]["evidence"] == utterance
    # The misconception is retained as review context; it is not mastery.
    assert events[-1]["kind"] == le.KIND_MISCONCEPTION
    log = le.EventLog(spec)
    log.record_events(events)
    assert log.misconceptions_of("qubit") == ["qubit-m1"]


def test_no_node_answer_emits_no_events():
    spec = make_spec()
    a = engine.tutor(spec, "What is photosynthesis?",
                     corpus_text={"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT})
    assert a.status == "no-node"
    assert le.events_from_answer(a, spec) == []  # no validated node ref -> no state


def test_process_answer_replay_is_idempotent(good_oracle):
    spec = make_spec()
    a = engine.tutor(spec, "What is a qubit?",
                     corpus_text={"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT},
                     draft_fn=_stub_draft)
    log = le.EventLog(spec)
    first = le.process_answer(a, spec, log)
    second = le.process_answer(a, spec, log)
    assert all(r.status == "recorded" for r in first["results"])
    assert all(r.status == "duplicate" for r in second["results"])
    assert log.attempts.get("qubit") == 1
    # Tutor groundedness is not an assessment outcome.
    assert log.mastery_of("qubit") == 0.0


def test_rejected_events_are_surfaced_never_swallowed():
    spec = make_spec()
    log = le.EventLog(spec)
    res = log.record({"v": "99", "kind": "attempt", "node_id": "qubit",
                      "question": "x", "answer_status": "grounded", "grounded": True,
                      "quantitative": False, "citations_used": [], "oracle_status": None})
    assert res.status == "rejected"
    assert any(i.startswith("version-unknown") for i in res.issues)
    assert log.rejections  # the rejection is recorded, not dropped


def test_end_to_end_real_spec_mapping_and_rejection():
    """The real quantum-computing spec: the known misconception maps, the
    unknown id is rejected — the contract holds on production-shaped data."""
    from open_tutor.generator import generate_quantum_computing
    spec = generate_quantum_computing()
    rows = le.misconception_candidates(
        "I think a qubit is just a bit that is either 0 or 1 that we don't "
        "know which one it is.", spec, node_id="qubit")
    assert rows and rows[0]["misconception_id"] == "qubit-m1"

    log = le.EventLog(spec)
    good = le.make_misconception_event(rows[0], spec, "the learner's words")
    assert log.record(good).status == "recorded"

    bad = dict(good, misconception_id="qubit-m99")
    r = log.record(bad)
    assert r.status == "rejected"
    assert any(i.startswith("misconception-unknown") for i in r.issues)
    assert log.misconceptions_of("qubit") == ["qubit-m1"]
