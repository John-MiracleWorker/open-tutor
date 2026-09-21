"""Adaptive history must not be rejected by the unused legacy context path."""
import pytest

from test_teaching_server import _setup, _wait


@pytest.mark.parametrize("character", ["\\", '"'])
def test_escaped_history_does_not_block_adaptive_admission(tmp_path, monkeypatch, character):
    app, client = _setup(tmp_path)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()["id"]
    for role in ("user", "assistant", "user", "assistant"):
        app.state.storage.add_message(thread, role, character * 1500)
    captured = []
    def generate(**kwargs):
        captured.append(kwargs["history"])
        return {"title": "A concept", "explanation": "One idea.", "steps": [], "diagram": None,
                "activity": {"kind": "explain", "prompt": "Explain the idea."}, "evidence_refs": [1]}
    monkeypatch.setattr("open_tutor.server.generate_teaching", generate)
    response = client.post(f"/api/threads/{thread}/ask", json={"question": "Teach node", "node_id": "node", "teaching": True})
    assert response.status_code == 200, response.text
    job = _wait(client, response.json()["job_id"])
    assert job["result"]["teaching"]["status"] == "ready"
    assert captured[0][0]["content"] == character * 1500
    assert client.get(f"/api/threads/{thread}").json()["messages"][-1]["answer"] == job["result"]
    app.state.storage.close()


def test_legacy_explicit_context_size_limit_is_preserved(tmp_path):
    app, client = _setup(tmp_path)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()["id"]
    response = client.post(f"/api/threads/{thread}/ask", json={"question": "Teach node", "followup_context": {"text": "x" * 8001}})
    assert response.status_code == 413
    assert client.get(f"/api/threads/{thread}").json()["messages"] == []
    app.state.storage.close()
