"""Actionable host errors for the real Qwen repair path, not weaker policy."""
import json
import pytest
from open_tutor.teaching import generate_teaching, select_plan, validate_teaching_output


def turn():
    return {"title": "State", "explanation": "A state describes a condition.", "steps": [],
            "diagram": None, "activity": {"kind": "explain", "prompt": "What is its state?"},
            "evidence_refs": [1]}


def test_question_title_error_names_the_exact_field_and_rewrite():
    bad = {**turn(), "title": "What is a state?"}
    with pytest.raises(ValueError) as error:
        validate_teaching_output(bad, known_refs={1})
    assert str(error.value) == (
        "title must be a declarative heading without question marks; rewrite it as a noun phrase. "
        "Put the only learner question in activity.prompt"
    )


@pytest.mark.parametrize("path", ["explanation", "steps[0].title", "steps[0].body",
                                  "diagram.title", "diagram.nodes[1].label", "diagram.edges[0].label"])
def test_question_error_locates_nested_offending_text(path):
    bad = turn()
    bad["steps"] = [{"title": "Step", "body": "A detail."}]
    bad["diagram"] = {"title": "Map", "nodes": [
        {"id": "a", "label": "First", "detail": "First detail"},
        {"id": "b", "label": "Second", "detail": "Second detail"}],
        "edges": [{"from": "a", "to": "b", "label": "connects"}]}
    if path == "explanation":
        bad["explanation"] += "？"
    elif path.startswith("steps"):
        bad["steps"][0][path.rsplit('.', 1)[1]] += "?"
    elif path == "diagram.title":
        bad["diagram"]["title"] += "?"
    elif path == "diagram.nodes[1].label":
        bad["diagram"]["nodes"][1]["label"] += "?"
    else:
        bad["diagram"]["edges"][0]["label"] += "?"
    with pytest.raises(ValueError) as error:
        validate_teaching_output(bad, known_refs={1})
    assert str(error.value).startswith(path + " ")
    assert "only learner question" in str(error.value)


def test_real_validator_passes_actionable_title_error_to_single_repair():
    bad = {**turn(), "title": "What is a state?"}
    calls = []
    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(bad)
        assert messages[-2] == {"role": "assistant", "content": json.dumps(bad)}
        assert "title must be a declarative heading" in messages[-1]["content"]
        return json.dumps(turn())
    result = generate_teaching(plan=select_plan("I am lost", action="confused"),
        history=[], pending_question=None, request="I am lost", evidence=[{"ref": 1}],
        oracle={}, profile={}, base_url="http://localhost:9120", model="test", completion_fn=complete)
    assert result == turn()
    assert len(calls) == 2


def test_provider_boundary_does_not_stop_at_legal_json_whitespace(monkeypatch):
    calls = []

    def provider(messages, base_url, model, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("stop") is None, "newline plus brace can begin the first JSON answer"
        assert kwargs["max_tokens"] == 1600
        assert kwargs["response_format"]["type"] == "json_schema"
        return "\n\n" + json.dumps(turn())

    monkeypatch.setattr("open_tutor.teaching.local_completion", provider)
    result = generate_teaching(plan=select_plan("Explain state"), history=[], pending_question=None,
        request="Explain state", evidence=[{"ref": 1}], oracle={}, profile={},
        base_url="http://localhost:9120", model="test")
    assert result == turn()
    assert len(calls) == 1
