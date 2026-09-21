from __future__ import annotations

import time

from fastapi.testclient import TestClient

from open_tutor.compiler import spec_content_hash
from open_tutor.server import create_app
from open_tutor.spec import CorpusSource, CurriculumSpec, Node


def spec() -> CurriculumSpec:
    return CurriculumSpec(
        subject="demo",
        title="Demo",
        corpus=[CorpusSource(id="src", name="Source", url="https://example.test")],
        nodes=[
            Node(
                id="node",
                title="Node",
                defn="A useful definition.",
                covers_keywords=["node"],
                grounding_corpus=["src"],
            )
        ],
    )


def good_report():
    return {
        "subject": "demo",
        "summary": {
            "all_grounded": True,
            "grounded": 1,
            "nodes_total": 1,
            "corpus_live": 1,
            "corpus_total": 1,
        },
        "structural": {"ok": True, "errors": {}},
        "corpus": [{"id": "src", "status": "live", "http_status": 200, "text_len": 3000}],
        "nodes": [{"id": "node", "status": "grounded", "grounding_ok": True}],
    }


def make_client(tmp_path, token=None):
    app = create_app(data_dir=tmp_path, write_token=token)
    app.state.storage.put_active_bundle(spec(), good_report(), {"status": "compiled"}, "fp")
    return TestClient(app)


def test_no_seed_data_and_normalized_subject_detail(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    assert client.get("/api/subjects").json() == {"subjects": []}
    app.state.storage.put_active_bundle(spec(), good_report(), {"status": "compiled"}, "fp")
    result = client.get("/api/subjects/demo")
    assert result.status_code == 200
    assert result.json()["spec"]["subject"] == "demo"
    assert set(result.json()["state"]["nodes"]) == {"node"}
    assert result.json()["gates"]["node"]["unlocked"] is True


def test_csrf_token_and_local_settings_validation(tmp_path):
    client = make_client(tmp_path, token="secret")
    assert client.put("/api/settings", json={"mode": "extractive"}).status_code == 401
    assert (
        client.put(
            "/api/settings",
            headers={"Authorization": "Bearer secret", "Origin": "https://evil.test"},
            json={"mode": "extractive"},
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/api/settings",
            headers={"Authorization": "Bearer secret"},
            json={"base_url": "https://example.com"},
        ).status_code
        == 400
    )
    response = client.put(
        "/api/settings",
        headers={"Authorization": "Bearer secret"},
        json={"base_url": "http://127.0.0.1:1234/v1", "model": "local", "mode": "local-model"},
    )
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert "api_key" not in response.text.lower()


def test_settings_get_response_round_trips_through_put(tmp_path):
    # The settings panel sends the full GET response back on save; a derived
    # GET-only flag in that payload must not 422 the PUT.
    client = make_client(tmp_path)
    client.put(
        "/api/settings",
        json={"base_url": "http://127.0.0.1:1234/v1", "model": "local", "mode": "extractive"},
    )
    payload = client.get("/api/settings").json()
    payload["mode"] = "local-model"
    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200
    assert response.json()["mode"] == "local-model"
    assert response.json()["configured"] is True


def test_body_limit_measures_actual_body_not_advisory_content_length(tmp_path):
    client = make_client(tmp_path)
    oversized = b"x" * (1_000_001)
    response = client.post(
        "/api/threads",
        content=oversized,
        headers={"content-type": "application/json", "content-length": "1"},
    )
    assert response.status_code == 413


def test_assessment_is_server_certified_and_updates_subject_state(tmp_path):
    client = make_client(tmp_path)
    item = client.get(
        "/api/subjects/demo/assessment", params={"node_id": "node", "kind": "teach-back"}
    )
    assert item.status_code == 200
    issued = item.json()["item"]
    assert "answer_key" not in issued
    graded = client.post(
        "/api/subjects/demo/assessment", json={"item_id": issued["id"], "response": "node"}
    )
    assert graded.status_code == 200
    assert graded.json()["grade"]["verdict"] in {"correct", "partial", "incorrect"}
    assert graded.json()["state"]["subject"] == "demo"


def test_ask_persists_answer_and_sse_can_reconnect(tmp_path, monkeypatch):
    client = make_client(tmp_path)

    def fake_tutor(*args, **kwargs):
        class Answer:
            grounded = True

            def to_dict(self):
                return {"status": "grounded", "grounded": True, "draft": "verified"}

        return Answer()

    monkeypatch.setattr("open_tutor.server.tutor", fake_tutor)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()
    job_id = client.post(
        f"/api/threads/{thread['id']}/ask", json={"question": "Explain node"}
    ).json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed", "model-error"}:
            break
        time.sleep(0.01)
    assert job["status"] == "completed"
    readback = client.get(f"/api/threads/{thread['id']}").json()
    assert [m["role"] for m in readback["messages"]] == ["user", "assistant"]
    events = client.get(f"/api/jobs/{job_id}/events")
    assert events.status_code == 200
    assert "event: answer" in events.text
    assert "event: done" in events.text


def test_failed_candidate_cannot_be_approved_or_replace_active(tmp_path):
    app = create_app(data_dir=tmp_path)
    app.state.storage.put_active_bundle(spec(), good_report(), {"status": "compiled"}, "active")
    candidate = spec().to_dict()
    candidate["title"] = "Bad candidate"
    app.state.storage.put_candidate_bundle(
        candidate,
        {"subject": "demo", "summary": {"all_grounded": False}, "corpus": [], "nodes": []},
        {"status": "blocked"},
        "candidate",
    )
    client = TestClient(app)
    response = client.post("/api/subjects/demo/review", json={"action": "approve"})
    assert response.status_code == 409
    assert client.get("/api/subjects/demo").json()["spec"]["title"] == "Demo"


def test_review_approval_promotes_only_matching_verified_candidate(tmp_path):
    app = create_app(data_dir=tmp_path)
    app.state.storage.put_active_bundle(spec(), good_report(), {"status": "compiled"}, "active")
    candidate = spec()
    candidate.title = "Reviewed"
    fingerprint = spec_content_hash(candidate)
    app.state.storage.put_candidate_bundle(
        candidate, good_report(), {"status": "paused"}, fingerprint
    )
    client = TestClient(app)
    response = client.post(
        "/api/subjects/demo/review", json={"action": "approve", "fingerprint": fingerprint}
    )
    assert response.status_code == 200
    assert response.json()["decision"]["status"] == "compiled"
    detail = client.get("/api/subjects/demo").json()
    assert detail["spec"]["title"] == "Reviewed"
    assert detail["decision"]["status"] == "compiled"
