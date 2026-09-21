"""RED regressions for the second independent review's three real findings.

Each test reproduces a specific reviewed defect against the real route before
the fix lands (TDD RED), and must pass after the fix (GREEN):

1. The model-path route built known_refs from ALL citations while the model
   prompt and generate_teaching bound references to the FIRST FIVE; a ref
   beyond five validated at the route but the model never saw that source.
   (integration finding, server.py:897)
2. An evidence-blocked teaching turn replaced the whole TeachingState with a
   fresh TeachingState(node_id=...), discarding the learner's pending
   question and counters on the same concept.  (integration finding,
   server.py:839)
3. The frontend started job monitoring only after an awaited thread readback;
   a readback failure between the accepted ask and monitor() orphaned the
   running job in-session.  (frontend finding, App.tsx:262)
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from open_tutor.spec import CorpusSource, CurriculumSpec, Node
from test_teaching_server import _setup, _wait


SOURCE = ("A node is a useful concept in a learning graph. "
          "It has a definition and can be studied in context. ") * 80


def _state(app, thread):
    return app.state.storage.get_teaching_state(thread)


class _FakeAnswer:
    """tutor() returns an object with to_dict(); match that contract."""

    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


def test_route_known_refs_match_first_five_the_model_saw(tmp_path, monkeypatch):
    """The route's known_refs must be the first five integer refs only.

    The model prompt serializes only the first five evidence items; a citation
    beyond position five must NOT be a valid evidence_ref at the route either,
    or the persisted lesson can cite a passage the model was never shown.
    """
    app, client = _setup(tmp_path)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()

    def seven_citations(*args, **kwargs):
        return _FakeAnswer({
            "answer": "Evidence.", "grounded": True, "status": "grounded",
            "citations": [{"source_name": f"src-{i}", "text": SOURCE}
                          for i in range(1, 8)],
            "resolution": {"node_id": "node"},
        })

    monkeypatch.setattr("open_tutor.server.tutor", seven_citations)

    def cites_the_sixth(**kwargs):
        return {"title": "T", "explanation": "E.", "steps": [], "diagram": None,
                "activity": {"kind": "reflect", "prompt": "Explain it."},
                "evidence_refs": [6]}

    monkeypatch.setattr("open_tutor.server.generate_teaching", cites_the_sixth)
    result = _wait(client, client.post(
        f"/api/threads/{thread['id']}/ask",
        json={"question": "Explain node", "node_id": "node", "teaching": True,
              "action": "respond", "approach": "plain"}).json()["job_id"])
    # The model returned evidence_refs=[6]; the route must reject ref 6
    # because the model prompt only exposed refs 1-5.  The rejection must be
    # a visible first-class degradation (coaching-fallback, no ready turn,
    # no persisted citation of an unshown source), never a ready lesson.
    assert result["status"] == "completed"
    assert result["result"]["status"] == "coaching-fallback", result["result"]
    assert result["result"]["teaching"]["status"] == "fallback"
    assert result["result"]["teaching"]["evidence_refs"] == []
    app.state.storage.close()


def test_evidence_blocked_turn_preserves_prior_state_on_same_node(tmp_path, monkeypatch):
    """A blocked evidence turn must not wipe the pending question/counters.

    The fallback path already preserves prior state on the same node; the
    blocked path must follow the same rule, or one weak-evidence turn silently
    discards the learner's current task.
    """
    app, client = _setup(tmp_path)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()
    prior = {"node_id": "node", "turn_count": 3, "hint_level": 2,
             "pending_question": "Why do qubits superpose?", "last_action": "hint"}
    app.state.storage.put_teaching_state(thread["id"], prior)

    def ungrounded(*args, **kwargs):
        return _FakeAnswer({"answer": "", "grounded": False, "status": "not-grounded",
                            "citations": [], "resolution": {"node_id": "node"}})

    monkeypatch.setattr("open_tutor.server.tutor", ungrounded)
    result = _wait(client, client.post(
        f"/api/threads/{thread['id']}/ask",
        json={"question": "More on node please", "node_id": "node",
              "teaching": True, "action": "respond", "approach": "plain"}).json()["job_id"])
    assert result["status"] == "completed"
    assert result["result"]["status"] == "coaching-blocked"
    after = _state(app, thread["id"])
    assert after["pending_question"] == "Why do qubits superpose?"
    assert after["hint_level"] == 2
    assert after["turn_count"] == 3
    app.state.storage.close()