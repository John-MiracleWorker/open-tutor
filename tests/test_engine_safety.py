"""Local-model and runtime safety contracts for the owned engine subsystem."""
from __future__ import annotations

import json

import pytest

from open_tutor import engine
from open_tutor.llm import LocalCompletionError, local_completion, validate_local_endpoint
from open_tutor.spec import CorpusSource, CurriculumSpec, Node


def _spec() -> CurriculumSpec:
    body = "A qubit is a two-level quantum system. " * 120
    return CurriculumSpec(
        subject="safety", title="Safety",
        corpus=[CorpusSource("src", "Source", "http://127.0.0.1/source")],
        nodes=[Node(id="qubit", title="Qubit", defn="A two-level system.",
                    grounding_corpus=["src"], covers_keywords=["qubit"])],
    ), {"src": body}


def test_public_and_non_http_model_endpoints_are_rejected_without_network():
    with pytest.raises(ValueError):
        validate_local_endpoint("https://api.example.com")
    with pytest.raises(ValueError):
        validate_local_endpoint("file:///tmp/model")
    assert validate_local_endpoint("http://127.0.0.1:8081")
    assert validate_local_endpoint("http://10.0.0.24:8081")


def test_local_completion_sends_only_explicit_messages(monkeypatch):
    seen = {}

    class Response:
        def read(self, limit):
            return json.dumps({"choices": [{"message": {"content": "draft"}}]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, timeout=timeout,
                    payload=json.loads(request.data.decode()))
        return Response()

    monkeypatch.setattr("open_tutor.llm._open_local", fake_urlopen)
    result = local_completion([{"role": "user", "content": "SOURCE_DATA: text"}],
                              "http://127.0.0.1:8081", "test-model",
                              timeout=2, max_tokens=50)
    assert result == "draft"
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["payload"]["temperature"] == 0
    assert "SOURCE_DATA: text" in seen["payload"]["messages"][0]["content"]


def test_local_completion_bad_response_is_first_class(monkeypatch):
    class Response:
        def read(self, limit):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("open_tutor.llm._open_local", lambda *args, **kwargs: Response())
    with pytest.raises(LocalCompletionError):
        local_completion([{"role": "user", "content": "x"}],
                         "http://127.0.0.1:8081", "test-model")


def test_engine_local_model_failure_does_not_raise_or_claim_grounded(monkeypatch):
    spec, text = _spec()

    def fail(*args, **kwargs):
        raise LocalCompletionError("fixture provider unavailable")

    answer = engine.tutor(spec, "What is a qubit?", corpus_text=text,
                          mode="local-model", base_url="http://127.0.0.1:8081",
                          model="test-model", local_completion_fn=fail)
    assert answer.status == "llm-error"
    assert answer.grounded is False
    assert any(issue.startswith("llm-error") for issue in answer.failures)


def test_local_model_draft_is_still_gate_checked(monkeypatch):
    spec, text = _spec()

    def unsupported(messages, base_url, model, **kwargs):
        return "## Claim\nA qubit is a classical bit. [1]\n## Citations\n"

    answer = engine.tutor(spec, "What is a qubit?", corpus_text=text,
                          mode="local-model", base_url="http://127.0.0.1:8081",
                          model="test-model", local_completion_fn=unsupported)
    assert answer.grounded is False
    assert answer.status == "unverified"
    assert any(issue.startswith("unsupported-claim")
               for issue in answer.verification.issues)
