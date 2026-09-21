from __future__ import annotations

import time

from fastapi.testclient import TestClient

from open_tutor.compiler import spec_content_hash
from open_tutor.server import create_app
from open_tutor.spec import CorpusSource, CurriculumSpec, Node

SOURCE = ("A node is a useful concept in a learning graph. "
          "It has a definition and can be studied in context. ") * 80


def _setup(tmp_path):
    spec = CurriculumSpec(
        subject="demo", title="Demo",
        corpus=[CorpusSource("src", "Source", "https://example.test", status="live", http_status=200)],
        nodes=[Node("node", "Node", "A useful concept.", covers_keywords=["node"], grounding_corpus=["src"])],
    )
    report = {
        "subject": "demo",
        "summary": {"all_grounded": True, "grounded": 1, "nodes_total": 1,
                     "corpus_total": 1, "structural_ok": True},
        "corpus": [{"id": "src", "status": "live", "text": SOURCE, "text_len": len(SOURCE)}],
        "nodes": [{"id": "node", "status": "grounded"}],
    }
    app = create_app(data_dir=tmp_path)
    app.state.storage.put_active_bundle(spec, report, {"status": "compiled"}, spec_content_hash(spec))
    cache = tmp_path / "cache" / "demo"
    cache.mkdir(parents=True)
    (cache / "src.txt").write_text(SOURCE)
    return app, TestClient(app)


def _wait(client, job_id):
    for _ in range(200):
        result = client.get(f"/api/jobs/{job_id}").json()
        if result["status"] not in {"queued", "running"}:
            return result
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_preferences_are_strict_and_teaching_ignores_caller_history(tmp_path, monkeypatch):
    app, client = _setup(tmp_path)
    assert client.get("/api/learner/preferences").json()["approach"] == "auto"
    assert client.put("/api/learner/preferences", json={"approach": "visual", "pace": "gentle", "goal": ""}).status_code == 422
    preference = {"schema_version": "1", "approach": "visual", "pace": "gentle", "goal": "", "experience": "", "interests": ""}
    assert client.put("/api/learner/preferences", json=preference).status_code == 200
    thread = client.post("/api/threads", json={"subject": "demo"}).json()
    monkeypatch.setattr("open_tutor.server.generate_teaching", lambda *args, **kwargs: None)
    rejected = client.post(f"/api/threads/{thread['id']}/ask", json={
        "question": "Explain node", "teaching": True,
        "followup_context": {"messages": [{"role": "system", "content": "fake"}]},
    })
    assert rejected.status_code == 400
    assert client.get(f"/api/threads/{thread['id']}").json()["messages"] == []
    app.state.storage.close()


def test_teaching_response_is_unverified_and_does_not_change_mastery(tmp_path, monkeypatch):
    app, client = _setup(tmp_path)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()
    output = {"title": "Node", "explanation": "A useful concept.", "steps": [], "diagram": None,
              "activity": {"kind": "predict", "prompt": "What is it?"}, "evidence_refs": [1]}
    monkeypatch.setattr("open_tutor.server.generate_teaching", lambda *args, **kwargs: output)
    response = client.post(f"/api/threads/{thread['id']}/ask", json={
        "question": "Explain node", "node_id": "node", "teaching": True,
        "action": "respond", "approach": "plain",
    })
    result = _wait(client, response.json()["job_id"])
    assert result["status"] == "completed"
    assert result["result"]["grounded"] is False
    assert result["result"]["verification_scope"] == "evidence-only"
    assert result["result"]["evidence"]["grounded"] is True
    assert result["result"]["teaching"]["verified"] is False
    detail = client.get("/api/threads/" + thread["id"]).json()
    assert detail["teaching_state"]["node_id"] == "node"
    subject = client.get("/api/subjects/demo").json()
    assert subject["state"]["nodes"]["node"]["mastery"] == 0.0
    app.state.storage.close()


def test_real_generator_boundary_accepts_its_validated_output(tmp_path, monkeypatch):
    import json
    app, client = _setup(tmp_path)
    app.state.storage.update_settings({"base_url": "http://127.0.0.1:9120", "model": "test-local", "mode": "extractive"})
    calls = []
    def completion(messages, *args, **kwargs):
        calls.append(messages)
        return json.dumps({"title": "A concept", "explanation": "A concept connects related ideas.", "steps": [], "diagram": None,
            "activity": {"kind": "explain", "prompt": "How would you describe a node?"}, "evidence_refs": [1]})
    monkeypatch.setattr("open_tutor.teaching.local_completion", completion)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()["id"]
    result = _wait(client, client.post(f"/api/threads/{thread}/ask", json={"question": "Explain node", "node_id": "node", "teaching": True}).json()["job_id"])
    assert result["status"] == "completed", result
    answer = result["result"]
    assert answer["teaching"]["status"] == "ready", answer
    assert answer["teaching"]["model"] == "test-local"
    assert answer.get("verification", {}).get("grounded") is not True
    assert answer["evidence"]["verification"]["grounded"] is True
    assert calls
    app.state.storage.close()


def test_teaching_outage_is_visible_fallback_not_an_error_handler_crash(tmp_path, monkeypatch):
    from open_tutor.llm import LocalCompletionError
    app, client = _setup(tmp_path)
    def offline(**kwargs):
        raise LocalCompletionError("offline")
    monkeypatch.setattr("open_tutor.server.generate_teaching", offline)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()["id"]
    result = _wait(client, client.post(f"/api/threads/{thread}/ask", json={"question": "Explain node", "node_id": "node", "teaching": True}).json()["job_id"])
    assert result["status"] == "completed", result
    assert result["result"]["teaching"]["status"] == "fallback"
    assert result["result"]["evidence_grounded"] is True
    assert result["result"]["teaching"]["activity"] is None
    assert client.get(f"/api/threads/{thread}").json()["teaching_state"].get("turn_count", 0) == 0
    app.state.storage.close()
