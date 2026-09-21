"""The qualified candidate is teaching-only; evidence drafting stays unchanged."""
import io
import json

import pytest

from open_tutor import llm
from open_tutor.teaching import generate_teaching, select_plan

TURN = {"title": "Starting point", "explanation": "A brief orientation.", "steps": [],
        "diagram": None, "activity": {"kind": "explain", "prompt": "Explain the idea."},
        "evidence_refs": [1]}


def capture_transport(monkeypatch):
    calls = []
    def capture(request, timeout):
        calls.append((json.loads(request.data), timeout))
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(TURN), "reasoning_content": "Private planning is not a teaching answer."}}]}).encode())
    monkeypatch.setattr(llm, "_open_local", capture)
    return calls


def test_teaching_uses_reasoning_and_bounded_1600_budget_without_stop_strings(monkeypatch):
    calls = capture_transport(monkeypatch)
    result = generate_teaching(plan=select_plan("Teach"), history=[], pending_question=None,
        request="Teach", evidence=[{"ref": 1, "text": "A source fact."}], oracle={}, profile={},
        base_url="http://127.0.0.1:8081", model="qwen-test-model")
    payload, timeout = calls[0]
    assert payload["max_tokens"] == 1600
    assert payload["chat_template_kwargs"]["enable_thinking"] is True
    assert payload["reasoning_budget_tokens"] == 900
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "stop" not in payload
    assert timeout == 120
    assert result == TURN
    assert len(calls) == 1


def test_shared_evidence_transport_retains_non_thinking_800_default(monkeypatch):
    calls = capture_transport(monkeypatch)
    llm.local_completion([{"role": "user", "content": "Evidence draft"}],
                         "http://127.0.0.1:8081", "Qwen-local")
    payload, _ = calls[0]
    assert payload["max_tokens"] == 800
    assert payload["chat_template_kwargs"]["enable_thinking"] is False


def test_non_qwen_teaching_uses_standard_openai_chat_fields_only(monkeypatch):
    calls = capture_transport(monkeypatch)
    generate_teaching(plan=select_plan("Teach"), history=[], pending_question=None,
        request="Teach", evidence=[{"ref": 1, "text": "A source fact."}], oracle={}, profile={},
        base_url="http://127.0.0.1:8081", model="my-local-instruct-model")
    payload, _ = calls[0]
    assert payload["max_tokens"] == 1600
    assert "chat_template_kwargs" not in payload
    assert "reasoning_budget_tokens" not in payload
    assert payload["response_format"]["json_schema"]["strict"] is True


@pytest.mark.parametrize("invalid", [1, "true"])
def test_thinking_override_rejects_non_boolean_values(invalid):
    with pytest.raises(TypeError, match="enable_thinking must be boolean"):
        llm.local_completion([], "http://localhost:8081", "Qwen-local", enable_thinking=invalid)


@pytest.mark.parametrize("budget,error", [(True, TypeError), (1.5, TypeError), (-1, ValueError), (1600, ValueError)])
def test_reasoning_budget_must_leave_room_for_final_output(budget, error):
    with pytest.raises(error, match="reasoning_budget_tokens must"):
        llm.local_completion([], "http://localhost:8081", "Qwen-local", max_tokens=1600,
                             enable_thinking=True, reasoning_budget_tokens=budget)


def test_reasoning_budget_requires_explicit_thinking():
    with pytest.raises(ValueError, match="requires enable_thinking"):
        llm.local_completion([], "http://localhost:8081", "Qwen-local", reasoning_budget_tokens=100)
