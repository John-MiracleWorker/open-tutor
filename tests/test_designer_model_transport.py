"""Transport compatibility for curriculum design's local model client."""
import io
import json
import urllib.error
from email.message import Message

from open_tutor import designer
from open_tutor.designer import LocalCompletionClient


def test_generic_designer_model_uses_standard_fields_and_fallback_removes_optionals(monkeypatch):
    """Generic OpenAI-compatible models must not receive llama.cpp controls."""
    calls = []

    def open_request(request, timeout):
        calls.append(json.loads(request.data))
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 400, "unsupported option", Message(), None)
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

    monkeypatch.setattr(designer, "_open_local", open_request)
    assert LocalCompletionClient("http://127.0.0.1:9120", "generic-instruct").complete("Design a curriculum") == "{}"

    assert "chat_template_kwargs" not in calls[0]
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "chat_template_kwargs" not in calls[1]
    assert "response_format" not in calls[1]


def test_qwen_designer_model_disables_thinking(monkeypatch):
    """Keep the llama.cpp control for Qwen while generic models stay portable."""
    calls = []

    def open_request(request, timeout):
        calls.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode())

    monkeypatch.setattr(designer, "_open_local", open_request)
    LocalCompletionClient("http://127.0.0.1:9120", "Qwen-local").complete("Design a curriculum")

    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
