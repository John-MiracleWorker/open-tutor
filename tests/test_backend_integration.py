"""Backend completion proofs using the real API and default implementations."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from open_tutor.compiler import spec_content_hash
from open_tutor.server import create_app
from open_tutor.spec import CorpusSource, CurriculumSpec, Node

SOURCE_TEXT = (
    "A node is a useful concept in a learning graph. A node has a definition, "
    "can cite an authoritative source, and may depend on prerequisite nodes. "
    "The source text describes the node as a stable unit of knowledge for study. "
) * 40


def _spec(*, oracle: str | None = None) -> CurriculumSpec:
    return CurriculumSpec(
        subject="integration-subject",
        title="Integration Subject",
        corpus=[CorpusSource(
            id="integration-source", name="Integration source",
            url="https://example.test/integration", status="live", http_status=200,
        )],
        nodes=[Node(
            id="node", title="Node", defn="A node is a useful concept in a learning graph.",
            grounding_corpus=["integration-source"], covers_keywords=["node"], oracle=oracle,
        )],
    )


def _report(spec: CurriculumSpec) -> dict:
    return {
        "subject": spec.subject,
        "summary": {
            "all_grounded": True, "grounded": len(spec.nodes),
            "nodes_total": len(spec.nodes), "corpus_live": 1,
            "corpus_total": 1, "structural_ok": True,
        },
        "structural": {"ok": True, "errors": {}},
        "corpus": [{
            "id": "integration-source", "status": "live", "http_status": 200,
            "text_len": len(SOURCE_TEXT), "text": SOURCE_TEXT,
            "method": "trafilatura", "needs_render": False,
        }],
        "nodes": [{"id": node.id, "status": "grounded", "grounding_ok": True,
                   "oracle": node.oracle, "oracle_ok": True if node.oracle else "na"}
                  for node in spec.nodes],
        "oracle_outputs": {},
    }


def _client(tmp_path, *, oracle: str | None = None):
    app = create_app(data_dir=tmp_path)
    spec = _spec(oracle=oracle)
    app.state.storage.put_active_bundle(spec, _report(spec), {"status": "compiled"},
                                        spec_content_hash(spec))
    cache = tmp_path / "cache" / spec.subject
    cache.mkdir(parents=True)
    (cache / "integration-source.txt").write_text(SOURCE_TEXT, encoding="utf-8")
    return app, TestClient(app)


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {job_id}")


def test_real_default_tutor_api_is_grounded_and_does_not_award_mastery(tmp_path):
    app, client = _client(tmp_path)
    thread = client.post("/api/threads", json={"subject": "integration-subject"})
    assert thread.status_code == 200
    job = client.post(
        f"/api/threads/{thread.json()['id']}/ask",
        json={
            "question": "Explain node",
            "node_id": "node",
            "followup_context": {"prior": "Please make it concrete."},
        },
    )
    assert job.status_code == 200
    result = _wait(client, job.json()["job_id"])
    assert result["status"] == "completed"
    assert result["result"]["grounded"] is True
    assert result["result"]["status"] == "grounded"
    assert "Mode:" not in result["result"]["draft"]
    assert "Executed oracle" not in result["result"]["draft"]
    assert result["result"]["draft"].count("##") == 2
    detail = client.get("/api/subjects/integration-subject").json()
    assert detail["state"]["nodes"]["node"]["mastery"] == 0.0
    events = client.get(f"/api/jobs/{job.json()['job_id']}/events").text
    assert "event: answer" in events and "event: done" in events
    app.state.storage.close()


def test_qubit_assessment_is_conceptual_server_owned_and_unlocks_prerequisite(tmp_path):
    """Exercise the public GET -> POST loop with real evidence offsets.

    The qubit oracle returns amplitudes, not a learner-facing scalar. The API
    therefore issues an authored choice item, keeps the answer hidden, and
    uses exact measured source evidence for the gate envelope.
    """
    body = ("In quantum computing, a qubit is a basic unit of quantum information. "
            "A qubit is a two-level quantum system that can be in a coherent "
            "superposition of its basis states. ") * 100
    spec = CurriculumSpec(
        subject="quantum-assessment", title="Quantum assessment",
        corpus=[CorpusSource("qubit-source", "Qubit source", "https://example.test/qubit",
                             status="live", http_status=200)],
        nodes=[
            Node("qubit", "The Qubit", "A two-level quantum system.",
                 grounding_corpus=["qubit-source"], covers_keywords=["qubit"],
                 oracle="qubit_state"),
            Node("next", "Next concept", "A follow-on concept.", prereqs=["qubit"],
                 grounding_corpus=["qubit-source"], covers_keywords=["concept"]),
        ],
    )
    report = {
        "subject": spec.subject,
        "summary": {"all_grounded": True, "grounded": 2, "nodes_total": 2,
                     "corpus_total": 1, "structural_ok": True},
        "corpus": [{"id": "qubit-source", "status": "live", "http_status": 200,
                     "text_len": len(body), "text": body}],
        "nodes": [{"id": node.id, "status": "grounded"} for node in spec.nodes],
    }
    app = create_app(data_dir=tmp_path)
    app.state.storage.put_active_bundle(spec, report, {"status": "compiled"}, "fp")
    client = TestClient(app)

    locked = client.get("/api/subjects/quantum-assessment/assessment",
                        params={"node_id": "next", "kind": "teach-back"})
    assert locked.status_code == 409

    issued = client.get("/api/subjects/quantum-assessment/assessment",
                        params={"node_id": "qubit", "kind": "quiz"})
    assert issued.status_code == 200
    payload = issued.json()
    item = payload["item"]
    assert item["quantitative"] is False
    assert item["prompt"].startswith("Which statement best describes")
    assert item["answer_options"][1].startswith("B.")
    assert "trusted_answer" not in item
    assert payload["evidence"] and payload["evidence"][0]["char_end"] > payload["evidence"][0]["char_start"]
    evidence = payload["evidence"][0]

    wrong = client.post("/api/subjects/quantum-assessment/assessment", json={
        "item_id": item["id"], "response": {"text": "a", "citations": [evidence]},
    })
    assert wrong.status_code == 200
    assert wrong.json()["grade"]["verdict"] == "incorrect"
    assert "trusted_answer" not in str(wrong.json())
    assert wrong.json()["state"]["nodes"]["qubit"]["attempts"] == 1
    assert wrong.json()["state"]["nodes"]["qubit"]["next_review"] is not None

    # Fresh item IDs make practice repeatable; the same item remains replay-safe.
    for _ in range(4):
        fresh = client.get("/api/subjects/quantum-assessment/assessment",
                           params={"node_id": "qubit", "kind": "quiz"}).json()
        result = client.post("/api/subjects/quantum-assessment/assessment", json={
            "item_id": fresh["item"]["id"],
            "response": {"text": "B", "citations": fresh["evidence"]},
        })
        assert result.json()["grade"]["verdict"] == "correct"

    detail = client.get("/api/subjects/quantum-assessment").json()
    assert detail["state"]["nodes"]["qubit"]["mastery"] >= 0.7
    assert detail["gates"]["next"]["unlocked"] is True
    unlocked = client.get("/api/subjects/quantum-assessment/assessment",
                          params={"node_id": "next", "kind": "teach-back"})
    assert unlocked.status_code == 200
    app.state.storage.close()


def test_server_owned_assessment_key_scores_wrong_numeric_as_incorrect(tmp_path):
    app, client = _client(tmp_path, oracle="sympy_derivative")
    issued = client.get(
        "/api/subjects/integration-subject/assessment",
        params={"node_id": "node", "kind": "quiz"},
    )
    assert issued.status_code == 200
    item = issued.json()["item"]
    assert "answer_key" not in item
    wrong = client.post(
        "/api/subjects/integration-subject/assessment",
        json={"item_id": item["id"],
              "response": {"text": "0", "grounded": True, "score": 1.0}},
    )
    assert wrong.status_code == 200
    assert wrong.json()["grade"]["verdict"] == "incorrect"
    assert wrong.json()["grade"]["verdict"] != "flagged"
    state = wrong.json()["state"]
    assert state["nodes"]["node"]["attempts"] == 1
    # BKT applies the documented learn transition after an incorrect signal;
    # this is not a reward from a client-supplied ``grounded`` flag.
    assert state["nodes"]["node"]["mastery"] == 0.2
    repeated = client.post(
        "/api/subjects/integration-subject/assessment",
        json={"item_id": item["id"], "response": "1"},
    )
    assert repeated.status_code == 200
    assert repeated.json() == wrong.json()
    app.state.storage.close()


def test_assessment_accepts_only_exact_measured_citation_spans(tmp_path):
    app, client = _client(tmp_path)
    issued = client.get(
        "/api/subjects/integration-subject/assessment",
        params={"node_id": "node", "kind": "teach-back"},
    )
    item = issued.json()["item"]
    valid_span = SOURCE_TEXT[:80]
    accepted = client.post(
        "/api/subjects/integration-subject/assessment",
        json={"item_id": item["id"], "response": {
            "text": "node", "citations": [{
                "source_id": "integration-source", "char_start": 0,
                "char_end": len(valid_span), "text": valid_span,
            }],
        }},
    )
    assert accepted.status_code == 200
    assert accepted.json()["grade"]["verdict"] == "correct"
    assert accepted.json()["state"]["nodes"]["node"]["mastery"] > 0
    app.state.storage.close()


def test_design_worker_registers_blocked_candidate_instead_of_failing(tmp_path, monkeypatch):
    """A blocked design (verifier said no) is a first-class outcome: the worker
    must register the candidate bundle and complete the job so the UI can show
    the real verification reasons instead of a generic designer crash."""
    from open_tutor import server as server_module
    from open_tutor.spec import save_yaml

    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    spec = _spec()
    spec.title = "Blocked candidate"
    candidate_dir = tmp_path / "candidates"
    candidate_dir.mkdir()
    spec_path = candidate_dir / "integration-subject.yaml"
    save_yaml(spec, str(spec_path))
    base_report = _report(spec)
    report = {**base_report,
              "summary": {**base_report["summary"], "all_grounded": False, "grounded": 0}}

    def fake_design(*args, **kwargs):
        return {"subject": spec.subject, "spec_path": None,
                "candidate_spec_path": str(spec_path),
                "report": report, "report_path": None,
                "decision": {"status": "blocked",
                             "reasons": ["verification: 0/1 nodes grounded (unverified=1)"]},
                "errors": []}

    monkeypatch.setattr(server_module, "design_curriculum", fake_design)
    job = client.post("/api/design", json={"topic": "integration subject"}).json()
    final = _wait(client, job["job_id"])
    assert final["status"] == "completed", final
    assert final["result"]["decision"]["status"] == "blocked"
    assert final["result"]["report"]["summary"]["all_grounded"] is False
    subjects = client.get("/api/subjects").json()["subjects"]
    row = next(item for item in subjects if item["subject"] == "integration-subject")
    assert row["candidate"]["status"] == "blocked"
    assert row["candidate"]["reasons"] == [
        "verification: 0/1 nodes grounded (unverified=1)"]
    detail = client.get("/api/subjects/integration-subject").json()
    assert detail["candidate"]["spec"]["title"] == "Blocked candidate"
    app.state.storage.close()


def test_candidate_is_visible_without_replacing_active_subject(tmp_path):
    app, client = _client(tmp_path)
    candidate = _spec()
    candidate.title = "Paused candidate"
    fingerprint = spec_content_hash(candidate)
    app.state.storage.put_candidate_bundle(
        candidate, {**_report(candidate), "summary": {**_report(candidate)["summary"],
                                                        "all_grounded": False}},
        {"status": "paused", "reasons": ["review required"]}, fingerprint,
    )
    subjects = client.get("/api/subjects").json()["subjects"]
    row = next(item for item in subjects if item["subject"] == "integration-subject")
    assert row["candidate"]["status"] == "paused"
    detail = client.get("/api/subjects/integration-subject").json()
    assert detail["spec"]["title"] == "Integration Subject"
    assert detail["candidate"]["spec"]["title"] == "Paused candidate"
    assert detail["candidate"]["fingerprint"] == fingerprint
    app.state.storage.close()


def test_verified_artifacts_allow_unused_thin_source_but_require_each_node(tmp_path):
    app, _ = _client(tmp_path)
    raw = _report(_spec())
    raw["corpus"].append({"id": "unused-thin", "status": "needs-render",
                           "http_status": 200, "text_len": 688,
                           "method": "none", "needs_render": True})
    spec = _spec()
    spec.corpus.append(CorpusSource(id="unused-thin", name="Thin SPA",
                                    url="https://example.test/spa"))
    raw["summary"]["corpus_total"] = 2
    from open_tutor.server import _verified_artifacts

    assert _verified_artifacts(spec, raw, {"status": "compiled"}) == (True, "ok")
    app.state.storage.close()


def test_research_endpoint_normalizes_adapter_mapping(monkeypatch, tmp_path):
    app = create_app(data_dir=tmp_path)
    from open_tutor import source_adapters
    monkeypatch.setattr(source_adapters, "discover_sources", lambda query: {
        "sources": [{"id": "x", "name": "X", "url": "https://example.test/x",
                     "adapter": "test"}], "errors": ["visible adapter warning"]
    })
    response = TestClient(app).get("/api/research", params={"query": "x"})
    assert response.status_code == 200
    assert response.json()["sources"][0]["adapter"] == "test"
    assert response.json()["errors"] == ["visible adapter warning"]
    app.state.storage.close()
