"""Keep coaching actions tied to the actual learner task."""
import json

import pytest

from open_tutor.llm import LocalCompletionError
from open_tutor.teaching import TeachingState, build_teaching_prompt, generate_teaching, select_plan

PENDING = "What physical difference distinguishes superposition from an unknown classical bit?"


def turn(prompt):
    return {"title": "Interference clue", "explanation": "Consider how overlapping waves can interact.",
            "steps": [], "diagram": None,
            "activity": {"kind": "explain", "prompt": prompt}, "evidence_refs": [1]}


def generate_hint(completion):
    state = TeachingState(node_id="qubit", approach="socratic", pending_question=PENDING)
    return generate_teaching(
        plan=select_plan("A small hint please", action="hint", state=state),
        history=[], pending_question=PENDING, request="A small hint please",
        evidence=[{"ref": 1, "text": "Test fixture about interference."}], oracle={}, profile={},
        base_url="http://localhost:9120", model="test", completion_fn=completion)


def test_hint_question_drift_gets_one_actionable_repair():
    calls = []
    def completion(messages):
        calls.append(messages)
        return json.dumps(turn("Why does interference matter?" if len(calls) == 1 else PENDING))
    result = generate_hint(completion)
    assert result["activity"]["prompt"] == PENDING
    assert len(calls) == 2
    repair = calls[1][-1]["content"]
    assert "activity.prompt" in repair
    assert "exactly repeat" in repair
    assert PENDING in repair


def test_hint_question_drift_after_repair_is_not_ready():
    calls = []
    def completion(messages):
        calls.append(messages)
        return json.dumps(turn("Try a different exercise?"))
    with pytest.raises(LocalCompletionError, match="activity.prompt"):
        generate_hint(completion)
    assert len(calls) == 2


def test_hint_keeps_valid_pending_question_without_repair():
    calls = []
    def completion(messages):
        calls.append(messages)
        return json.dumps(turn(PENDING))
    assert generate_hint(completion)["activity"]["prompt"] == PENDING
    assert len(calls) == 1


def test_hint_prompt_requires_verbatim_task_not_a_harder_replacement():
    prompt = build_teaching_prompt(
        plan=select_plan("hint", action="hint", state=TeachingState(pending_question=PENDING)),
        history=[], pending_question=PENDING, request="hint", evidence=[], oracle={}, profile={})
    assert "exactly repeat PENDING_QUESTION" in prompt
    assert "do not replace it" in prompt


@pytest.mark.parametrize("method", ["plain", "socratic", "worked-example", "analogy", "visual", "challenge"])
def test_clicked_selects_unsolved_transfer_not_previous_method(method):
    state = TeachingState(node_id="qubit", approach=method, pending_question=PENDING)
    plan = select_plan("That clicked", action="got-it", state=state,
                       preferences={"approach": method})
    assert plan.intent == "transfer"
    assert plan.approach == "challenge"
    assert plan.next_state.approach == "challenge"
    assert plan.pending_question == PENDING  # do not invent a delivered question


def test_clicked_prompt_does_not_treat_self_report_as_demonstrated_understanding():
    prompt = build_teaching_prompt(
        plan=select_plan("That clicked", action="got-it"), history=[],
        pending_question=None, request="That clicked", evidence=[], oracle={}, profile={})
    assert "self-report is not demonstrated understanding" in prompt
    assert "only concepts already introduced" in prompt
    assert "one answerable task" in prompt
