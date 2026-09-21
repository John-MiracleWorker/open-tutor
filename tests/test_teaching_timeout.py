"""Reasoning-capable local teaching needs bounded finite deadlines."""
import io
import json

import pytest

from open_tutor import llm
from open_tutor.teaching import generate_teaching, select_plan

TURN = {"title": "Starting point", "explanation": "A brief orientation.", "steps": [],
        "diagram": None, "activity": {"kind": "explain", "prompt": "Explain the idea."},
        "evidence_refs": [1]}


def teach(**kwargs):
    return generate_teaching(
        plan=select_plan("Teach"), history=[], pending_question=None,
        request="Teach", evidence=[{"ref": 1, "text": "A source fact."}],
        oracle={}, profile={}, base_url="http://127.0.0.1:8081",
        model="qwen-test-model", **kwargs,
    )


def test_valid_70_second_teacher_is_not_cut_off_at_60_seconds(monkeypatch):
    calls = []

    def transport(request, timeout):
        calls.append(json.loads(request.data))
        # Simulate the observed transport deadline without sleeping for 70s.
        if timeout < 70:
            raise TimeoutError("valid reasoning lesson still generating at the old deadline")
        assert timeout == 120
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(TURN)}}]}).encode())

    monkeypatch.setattr(llm, "_open_local", transport)
    assert teach() == TURN
    assert len(calls) == 1
    assert calls[0]["model"] == "qwen-test-model"
    assert calls[0]["max_tokens"] == 1600
    assert calls[0]["reasoning_budget_tokens"] == 900
    assert calls[0]["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.parametrize("timeout", [0, -1, 120.01, float("inf"), float("nan")])
def test_teacher_refuses_invalid_deadline_before_calling_provider(timeout):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid timeout reached provider")

    with pytest.raises(ValueError, match="teaching model bounds"):
        teach(timeout=timeout, completion_fn=unexpected)


@pytest.mark.parametrize("timeout", [30, 60, 120])
def test_explicit_bounded_deadline_is_honored(timeout):
    calls = []

    def completion(messages, base_url, model, **options):
        calls.append(options)
        return json.dumps(TURN)

    assert teach(timeout=timeout, completion_fn=completion) == TURN
    assert calls == [{"timeout": timeout, "max_tokens": 1600}]


def test_deadline_expiry_is_not_retried_as_schema_repair():
    calls = []

    def completion(messages, base_url, model, **options):
        calls.append(options)
        raise llm.LocalCompletionError("local model request failed: TimeoutError")

    with pytest.raises(llm.LocalCompletionError, match="TimeoutError"):
        teach(completion_fn=completion)
    assert calls == [{"timeout": 120, "max_tokens": 1600}]
