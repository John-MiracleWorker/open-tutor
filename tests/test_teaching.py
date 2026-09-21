from __future__ import annotations

import json

import pytest

from open_tutor.spec import CorpusSource, CurriculumSpec, Node
from open_tutor.teaching import (
    DEFAULT_PREFERENCES,
    TeachingState,
    build_teaching_prompt,
    select_plan,
    validate_teaching_output,
)


def _spec() -> CurriculumSpec:
    return CurriculumSpec(
        subject="demo",
        title="Demo",
        corpus=[CorpusSource("src", "Source", "https://example.test")],
        nodes=[Node("qubit", "Qubit", "A two-level system.", covers_keywords=["qubit"])],
    )


def test_policy_explicit_controls_and_confusion_scaffold():
    state = TeachingState(node_id="qubit", approach="visual", pace="brisk", confusion_count=1)
    plan = select_plan(
        question="I am confused about this",
        action="simpler",
        approach="challenge",
        preferences=DEFAULT_PREFERENCES,
        state=state,
    )
    assert plan.approach == "challenge"
    assert plan.intent == "scaffold"
    assert plan.pace == "gentle"


def test_another_way_changes_strategy_and_hint_ramps_without_solution():
    state = TeachingState(node_id="qubit", approach="plain", hint_level=0)
    first = select_plan("another way", "another-way", None, DEFAULT_PREFERENCES, state)
    assert first.approach != "plain"
    assert first.hint_level == 0
    hinted = select_plan("hint please", "hint", None, DEFAULT_PREFERENCES, first.next_state)
    assert hinted.hint_level == 1
    assert hinted.hint_level <= 3
    assert hinted.intent == "scaffold"


def test_short_reply_uses_pending_node_and_got_it_requests_transfer():
    state = TeachingState(node_id="qubit", pending_question="What would happen next?", turn_count=2)
    plan = select_plan("It would be a superposition", "respond", None, DEFAULT_PREFERENCES, state)
    assert plan.node_id == "qubit"
    assert plan.pending_question == "What would happen next?"
    transfer = select_plan("got it", "got-it", None, DEFAULT_PREFERENCES, state)
    assert transfer.intent == "transfer"
    assert transfer.next_state.pending_question


def test_node_change_resets_node_scaffolding():
    state = TeachingState(node_id="qubit", hint_level=3, confusion_count=4, pending_question="old")
    plan = select_plan("new concept", "respond", None, DEFAULT_PREFERENCES, state, node_id="new-node")
    assert plan.node_id == "new-node"
    assert plan.next_state.hint_level == 0
    assert plan.next_state.confusion_count == 0
    assert plan.next_state.pending_question is None


def test_prompt_delimits_canonical_history_and_source_data():
    prompt = build_teaching_prompt(
        plan=select_plan("Explain qubit", "respond", "plain", DEFAULT_PREFERENCES, TeachingState()),
        history=[{"role": "user", "content": "prior"}],
        pending_question=None,
        request="Explain qubit",
        evidence=[{"ref": 1, "text": "SOURCE DATA"}],
        oracle={"status": "not-required"},
        profile={"goal": "learn"},
    )
    assert "CANONICAL_HISTORY_BEGIN" in prompt
    assert "SOURCE_DATA_BEGIN" in prompt
    assert "system-owned" in prompt.lower()
    assert "prior" in prompt


def test_model_output_is_strict_and_validates_refs_and_diagram_edges():
    valid = {
        "title": "A title",
        "explanation": "A short explanation.",
        "steps": [{"title": "Step", "body": "Detail"}],
        "diagram": {
            "title": "Map",
            "nodes": [
                {"id": "a", "label": "A", "detail": "One"},
                {"id": "b", "label": "B", "detail": "Two"},
            ],
            "edges": [{"from": "a", "to": "b", "label": "leads to"}],
        },
        "activity": {"kind": "predict", "prompt": "What changes?"},
        "evidence_refs": [1],
    }
    result = validate_teaching_output(json.dumps(valid), known_refs={1})
    assert result["title"] == "A title"
    assert "verified" not in result
    with pytest.raises(ValueError, match="unknown field"):
        validate_teaching_output(json.dumps({**valid, "verified": True}), known_refs={1})
    with pytest.raises(ValueError, match="evidence reference"):
        validate_teaching_output(json.dumps({**valid, "evidence_refs": [2]}), known_refs={1})
    bad_diagram = {**valid, "diagram": {**valid["diagram"], "edges": [{"from": "a", "to": "z"}]}}
    with pytest.raises(ValueError, match="diagram edge"):
        validate_teaching_output(json.dumps(bad_diagram), known_refs={1})


def test_model_output_rejects_html_empty_and_oversized_content():
    base = {
        "title": "T",
        "explanation": "Explanation",
        "steps": [],
        "diagram": None,
        "activity": {"kind": "reflect", "prompt": "Think"},
        "evidence_refs": [],
    }
    with pytest.raises(ValueError):
        validate_teaching_output(json.dumps({**base, "explanation": "<script>x</script>"}), known_refs=set())
    with pytest.raises(ValueError):
        validate_teaching_output("not json", known_refs=set())
    with pytest.raises(ValueError):
        validate_teaching_output(json.dumps({**base, "title": ""}), known_refs=set())
    with pytest.raises(ValueError):
        validate_teaching_output(json.dumps({**base, "explanation": "x" * 5001}), known_refs=set())


def test_generation_allows_only_one_bounded_schema_repair():
    responses = ["{}", json.dumps({
        "title": "T", "explanation": "Explanation", "steps": [], "diagram": None,
        "activity": {"kind": "apply", "prompt": "Try it"}, "evidence_refs": [1],
    })]
    calls = []

    def completion(messages):
        calls.append(messages)
        return responses.pop(0)

    from open_tutor.teaching import generate_teaching

    result = generate_teaching(
        plan=select_plan("Explain", "respond", "plain", DEFAULT_PREFERENCES, TeachingState()),
        history=[], pending_question=None, request="Explain", evidence=[{"ref": 1, "text": "x"}],
        oracle=None, profile=None, base_url="http://127.0.0.1:9120", model="local",
        completion_fn=completion,
    )
    assert result["activity"]["kind"] == "apply"
    assert len(calls) == 2
