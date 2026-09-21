"""Completion tests for the P2 mastery boundary.

These tests deliberately distinguish a grounded tutor answer from a verified
assessment outcome.  A tutor answer supplies context; only ``assessment.grade``
may supply a mastery signal.
"""
from __future__ import annotations

import json

from open_tutor import learner_events as le
from open_tutor import learner_state as ls
from open_tutor.assessment import KIND_QUIZ, KIND_TEACHBACK, LearnerResponse, make_item
from open_tutor.spec import CorpusSource, CurriculumSpec, Misconception, Node

NOW = "2026-01-01T00:00:00Z"
LATER = "2026-01-05T00:00:00Z"


def make_spec() -> CurriculumSpec:
    return CurriculumSpec(
        subject="mastery-test",
        title="Mastery test",
        corpus=[CorpusSource(id="source", name="Source", url="https://example.test")],
        nodes=[
            Node(id="foundation", title="Foundation", defn="A foundation.",
                 grounding_corpus=["source"]),
            Node(id="dependent", title="Dependent", defn="A dependent concept.",
                 prereqs=["foundation"]),
        ],
    )


def test_grounded_tutor_answer_is_not_a_mastery_signal(tmp_path):
    spec = make_spec()
    answer = type("Answer", (), {
        "resolution": type("Resolution", (), {"node_id": "foundation"})(),
        "question": "Explain foundation",
        "status": "grounded",
        "grounded": True,
        "quantitative": False,
        "verification": None,
        "oracle": None,
    })()

    events = le.events_from_answer(answer, spec, now=NOW)
    assert all(e["kind"] != le.KIND_ASSESSMENT for e in events)
    assert all(e["kind"] != le.KIND_REVIEW for e in events)
    store = ls.LearnerStateStore(str(tmp_path / "state.json"), spec)
    store.record_events(events, now=NOW)
    assert store.mastery_of("foundation") == 0.0


def test_verified_assessment_is_the_only_mastery_and_review_signal(tmp_path):
    spec = make_spec()
    item = make_item(KIND_QUIZ, "foundation", "What is it?",
                     required_citations=["source"])
    response = LearnerResponse("A foundation.", grounded=True,
                               citations=[{"source_id": "source"}])
    events = le.events_from_assessment(item, response, spec, now=NOW)
    assert [e["kind"] for e in events] == [le.KIND_ATTEMPT, le.KIND_ASSESSMENT,
                                            le.KIND_REVIEW]

    store = ls.LearnerStateStore(str(tmp_path / "state.json"), spec)
    results = store.record_events(events, now=NOW)
    assert all(r.status == "recorded" for r in results)
    assert store.mastery_of("foundation") > 0.0
    assert store.state().schedules["foundation"]["next_review"]


def test_unverified_teachback_cannot_become_mastery(tmp_path):
    spec = make_spec()
    item = make_item(KIND_TEACHBACK, "foundation", "Explain it",
                     rubric_points=["foundation"])
    response = LearnerResponse("Foundation.", grounded=False)
    events = le.events_from_assessment(item, response, spec, now=NOW)
    assessment = next(e for e in events if e["kind"] == le.KIND_ASSESSMENT)
    assert assessment["verdict"] == "flagged"
    assert not any(e["kind"] == le.KIND_REVIEW for e in events)
    store = ls.LearnerStateStore(str(tmp_path / "state.json"), spec)
    store.record_events(events, now=NOW)
    assert store.mastery_of("foundation") == 0.0


def test_unverified_quiz_cannot_become_a_negative_or_positive_signal(tmp_path):
    spec = make_spec()
    item = make_item(KIND_QUIZ, "foundation", "What is it?",
                     required_citations=["source"])
    response = LearnerResponse("not supported by the gate", grounded=False)
    events = le.events_from_assessment(item, response, spec, now=NOW)
    assessment = next(e for e in events if e["kind"] == le.KIND_ASSESSMENT)
    assert assessment["verdict"] == "flagged"
    assert assessment["score"] is None
    assert not any(e["kind"] == le.KIND_REVIEW for e in events)
    store = ls.LearnerStateStore(str(tmp_path / "state.json"), spec)
    store.record_events(events, now=NOW)
    assert store.mastery_of("foundation") == 0.0


def test_gate_report_and_due_queue_are_deterministic(tmp_path):
    spec = make_spec()
    item = make_item(KIND_QUIZ, "foundation", "What is it?",
                     required_citations=["source"])
    response = LearnerResponse("A foundation.", grounded=True,
                               citations=[{"source_id": "source"}])
    store = ls.LearnerStateStore(str(tmp_path / "state.json"), spec)
    store.record_events(le.events_from_assessment(item, response, spec, now=NOW), now=NOW)
    state = store.state()

    report = ls.gate_report(spec, state)
    assert report["foundation"] == {"unlocked": True, "blocking_prereqs": []}
    assert report["dependent"] == {
        "unlocked": False, "blocking_prereqs": ["foundation"]}
    assert ls.due_nodes(spec, state, now=LATER) == ["foundation"]


def test_misconception_requests_review_without_crediting_mastery(tmp_path):
    spec = make_spec()
    item = make_item(KIND_TEACHBACK, "foundation", "Explain it",
                     rubric_points=["foundation"])
    response = LearnerResponse(
        "Foundation is a mysterious concept with hidden values.", grounded=True)
    # The synthetic node has no misconception, so explicitly add a valid one
    # through a small derived spec for this boundary test.
    spec.node("foundation").misconceptions = [Misconception(
        id="foundation-m1", text="Foundation is a mysterious concept with hidden values")]
    events = le.events_from_assessment(item, response, spec, now=NOW)
    assert any(e["kind"] == le.KIND_MISCONCEPTION for e in events)
    store = ls.LearnerStateStore(str(tmp_path / "state.json"), spec)
    store.record_events(events, now=NOW)
    assert store.mastery_of("foundation") == 0.0
    assert ls.due_nodes(spec, store.state(), now=LATER) == ["foundation"]


def test_tampered_projection_or_event_id_is_rejected(tmp_path):
    spec = make_spec()
    item = make_item(KIND_QUIZ, "foundation", "What is it?",
                     required_citations=["source"])
    response = LearnerResponse("A foundation.", grounded=True,
                               citations=[{"source_id": "source"}])
    path = str(tmp_path / "state.json")
    store = ls.LearnerStateStore(path, spec)
    store.record_events(le.events_from_assessment(item, response, spec, now=NOW), now=NOW)

    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    document["mastery"]["foundation"] = 1.0
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    try:
        ls.LearnerStateStore(path, spec).load()
    except ValueError as exc:
        assert "projection" in str(exc)
    else:
        raise AssertionError("tampered projection was accepted")

    document["mastery"]["foundation"] = store.state().mastery["foundation"]
    document["events"][0]["event_id"] = "0" * 64
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    try:
        ls.LearnerStateStore(path, spec).load()
    except ValueError as exc:
        assert "event-invalid" in str(exc)
    else:
        raise AssertionError("tampered event id was accepted")


def test_tampered_attempt_projection_is_rejected(tmp_path):
    spec = make_spec()
    item = make_item(KIND_QUIZ, "foundation", "What is it?",
                     required_citations=["source"])
    response = LearnerResponse("A foundation.", grounded=True,
                               citations=[{"source_id": "source"}])
    path = str(tmp_path / "state.json")
    store = ls.LearnerStateStore(path, spec)
    store.record_events(le.events_from_assessment(item, response, spec, now=NOW), now=NOW)

    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    document["attempts"]["foundation"] = 999
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    try:
        ls.LearnerStateStore(path, spec).load()
    except ValueError as exc:
        assert "projection" in str(exc)
    else:
        raise AssertionError("tampered attempts projection was accepted")


def test_corrupt_timestamp_is_rejected(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "state.json")
    store = ls.LearnerStateStore(path, spec)
    item = make_item(KIND_QUIZ, "foundation", "What is it?",
                     required_citations=["source"])
    response = LearnerResponse("A foundation.", grounded=True,
                               citations=[{"source_id": "source"}])
    store.record_events(le.events_from_assessment(item, response, spec, now=NOW), now=NOW)
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    document["last_updated"] = "not-a-timestamp"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)
    try:
        ls.LearnerStateStore(path, spec).load()
    except ValueError as exc:
        assert "timestamp-invalid" in str(exc)
    else:
        raise AssertionError("corrupt timestamp was accepted")
