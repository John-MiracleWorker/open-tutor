"""Instruction precedence regressions; live transcripts qualify semantics separately."""
import json

import pytest

from open_tutor.llm import LocalCompletionError
from open_tutor.teaching import TeachingState, build_teaching_prompt, generate_teaching, select_plan


def prompt_for(action, *, method="plain"):
    state = TeachingState(node_id="n", approach=method, pending_question="The previous difficult task?")
    plan = select_plan("I need help with the foundation", action=action, state=state)
    return build_teaching_prompt(plan=plan, history=[], pending_question=plan.pending_question,
                                 request="I need help with the foundation", evidence=[], oracle={}, profile={})


@pytest.mark.parametrize("method", ["worked-example", "visual", "challenge"])
def test_hint_does_not_inherit_a_solved_or_complex_representation(method):
    state = TeachingState(node_id="n", approach=method, pending_question="Find the final rate?")
    plan = select_plan("A hint", action="hint", state=state, preferences={"approach": method})
    assert plan.approach == "plain"
    assert plan.pending_question == state.pending_question
    assert plan.next_state.hint_level == 1


@pytest.mark.parametrize("action", ["confused", "simpler"])
def test_confusion_overrides_answer_previous_task_instruction(action):
    prompt = prompt_for(action)
    system, _, _ = prompt.partition("CANONICAL_HISTORY_BEGIN")
    assert "replace the harder pending activity" in system
    assert "check only that foundation" in system
    assert "Respond to the learner's latest attempt on PENDING_QUESTION first" not in system


def test_hint_policy_has_no_competing_retrieval_or_example_request():
    system = prompt_for("hint", method="worked-example").partition("CANONICAL_HISTORY_BEGIN")[0]
    assert "one cue about where to start" in system
    assert "leave the decisive inference" in system
    assert "exactly repeat PENDING_QUESTION" in system
    assert "worked-example=2-3" not in system
    assert "For got-it request" not in system


def test_transfer_requests_only_one_final_output_not_intermediate_subparts():
    system = prompt_for("got-it").partition("CANONICAL_HISTORY_BEGIN")[0]
    assert "one final output" in system
    assert "intermediate results" in system
    assert "neutral setup" in system


def test_injected_provider_cannot_add_solution_steps_to_hint():
    state = TeachingState(node_id="n", approach="plain", pending_question="Find the final rate?")
    calls = []
    def completion(messages):
        calls.append(messages)
        return json.dumps({"title": "Hint", "explanation": "Consider the exponent.",
                           "steps": [{"title": "Solution", "body": "The answer is 6."}], "diagram": None,
                           "activity": {"kind": "apply", "prompt": state.pending_question}, "evidence_refs": [1]})
    with pytest.raises(LocalCompletionError, match="steps must be empty"):
        generate_teaching(plan=select_plan("A hint", action="hint", state=state), history=[],
                          pending_question=state.pending_question, request="A hint", evidence=[{"ref": 1}],
                          oracle={}, profile={}, base_url="http://localhost:9120", model="test", completion_fn=completion)
    assert len(calls) == 2
