"""Behavior contracts surfaced by the first live acceptance probe."""
import json
import pytest
from open_tutor.llm import LocalCompletionError
from open_tutor.teaching import TeachingState, build_teaching_prompt, generate_teaching, select_plan, validate_teaching_output

@pytest.mark.parametrize("question,intent,action", [
    ("I'm still lost", "scaffold", "confused"),
    ("Please make it simpler", "scaffold", "simpler"),
    ("Just a hint please", "scaffold", "hint"),
    ("That clicked", "transfer", "got-it"),
])
def test_natural_feedback_changes_teaching_without_button(question, intent, action):
    plan = select_plan(question, state=TeachingState(node_id="n", pending_question="Why?"))
    assert plan.intent == intent
    assert plan.next_state.last_action == action


def test_changed_node_does_not_inherit_old_question_or_method():
    state = TeachingState(node_id="a", pending_question="Question A", approach="challenge")
    plan = select_plan("Teach B", state=state, node_id="b")
    assert plan.pending_question is None
    assert plan.approach == "plain"


def test_generation_prompt_teaches_nested_contract_and_real_strategy():
    prompt = build_teaching_prompt(plan=select_plan("help", action="hint", state=TeachingState(pending_question="Why?")),
        history=[], pending_question="Why?", request="hint", evidence=[{"ref":1,"text":"Fact"}], oracle={}, profile={})
    assert '"kind"' in prompt and '"prompt"' in prompt
    assert '"nodes"' in prompt and '"edges"' in prompt
    assert "do not reveal" in prompt.lower()
    assert "analogy" in prompt.lower() and "limitation" in prompt.lower()
    assert "illustrative" in prompt.lower()


def valid():
    return {"title":"T", "explanation":"A clear idea.", "steps":[], "diagram":None,
            "activity":{"kind":"explain", "prompt":"What does this mean?"}, "evidence_refs":[1]}

@pytest.mark.parametrize("field,value", [("activity",None), ("activity",{"kind":"predict","prompt":"Why? What next?"}), ("explanation","Why? Who knows?")])
def test_ready_output_has_only_one_activity_and_no_scattered_questions(field,value):
    with pytest.raises(ValueError):
        validate_teaching_output({**valid(),field:value}, known_refs={1})


def test_visual_and_worked_output_must_really_have_their_representation():
    for strategy in ["visual","worked-example"]:
        with pytest.raises(LocalCompletionError):
            generate_teaching(plan=select_plan("Teach", approach=strategy), history=[], pending_question=None,
                request="Teach", evidence=[{"ref":1,"text":"Fact"}], oracle={}, profile={}, base_url="http://localhost:9120", model="test",
                completion_fn=lambda messages: json.dumps(valid()))


def test_transport_receives_method_specific_json_schema(monkeypatch):
    captured=[]
    def completion(messages, base_url, model, **kwargs):
        captured.append(kwargs)
        return json.dumps(valid())
    monkeypatch.setattr("open_tutor.teaching.local_completion", completion)
    generate_teaching(plan=select_plan("Teach", approach="plain"), history=[], pending_question=None,
        request="Teach", evidence=[{"ref":1,"text":"Fact"}], oracle={}, profile={}, base_url="http://localhost:9120", model="test")
    schema = captured[0]["response_format"]
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["schema"]["properties"]["diagram"] == {"type":"null"}
    assert captured[0].get("stop") is None
    # Reject extra objects rather than cutting off legitimate JSON whitespace.
    with pytest.raises(ValueError, match="not valid JSON"):
        validate_teaching_output(json.dumps(valid()) + "\n" + json.dumps(valid()), known_refs={1})


def test_transport_failure_does_not_retry_as_schema_repair():
    calls=[]
    def unavailable(messages):
        calls.append(messages)
        raise LocalCompletionError("connection refused")
    with pytest.raises(LocalCompletionError):
        generate_teaching(plan=select_plan("Teach"), history=[], pending_question=None,
            request="Teach", evidence=[{"ref":1,"text":"Fact"}], oracle={}, profile={}, base_url="http://localhost:9120", model="test",
            completion_fn=unavailable)
    assert len(calls) == 1
