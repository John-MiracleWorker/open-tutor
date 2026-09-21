"""RED regressions for the second independent review round, batch 2.

1. When the evidence result resolves NO node (node_from_evidence is None), a
   blocked or fallback teaching turn must not wipe the learner's durable
   teaching state: the prior pending question and counters live on a real
   concept, and an unresolved-evidence failure has no authority to reset it.
   (integration findings at server.py:862/:884 and frontend finding at 842)
2. After /ask is accepted, a failed thread readback must not clear the running
   job indicator: the accepted job is still running and monitor() owns it.
   (frontend finding, App.tsx:222 catch block)
"""
from __future__ import annotations

from open_tutor.spec import CorpusSource, CurriculumSpec, Node
from test_teaching_server import _setup, _wait

SOURCE = ("A node is a useful concept in a learning graph. "
          "It has a definition and can be studied in context. ") * 80


class _FakeAnswer:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


def test_blocked_turn_with_unresolved_evidence_keeps_prior_state(tmp_path, monkeypatch):
    """node_from_evidence=None must not replace prior state on a real node."""
    app, client = _setup(tmp_path)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()
    prior = {"node_id": "node", "turn_count": 4, "hint_level": 1,
             "pending_question": "Why does it superpose?", "last_action": "respond"}
    app.state.storage.put_teaching_state(thread["id"], prior)

    def ungrounded_unresolved(*args, **kwargs):
        return _FakeAnswer({"answer": "", "grounded": False, "status": "not-grounded",
                             "citations": [], "resolution": {"node_id": None}})

    monkeypatch.setattr("open_tutor.server.tutor", ungrounded_unresolved)
    result = _wait(client, client.post(
        f"/api/threads/{thread['id']}/ask",
        json={"question": "Tell me more", "node_id": "node", "teaching": True,
              "action": "respond", "approach": "plain"}).json()["job_id"])
    assert result["status"] == "completed"
    assert result["result"]["status"] == "coaching-blocked"
    after = app.state.storage.get_teaching_state(thread["id"])
    assert after["pending_question"] == "Why does it superpose?"
    assert after["node_id"] == "node"
    assert after["hint_level"] == 1
    app.state.storage.close()


def test_fallback_turn_with_unresolved_evidence_keeps_prior_state(tmp_path, monkeypatch):
    """Grounded evidence but no resolved node + model failure keeps prior state."""
    app, client = _setup(tmp_path)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()
    prior = {"node_id": "node", "turn_count": 4, "hint_level": 1,
             "pending_question": "Why does it superpose?", "last_action": "respond"}
    app.state.storage.put_teaching_state(thread["id"], prior)

    def grounded_unresolved(*args, **kwargs):
        return _FakeAnswer({"answer": "Evidence.", "grounded": True, "status": "grounded",
                            "citations": [{"source_name": "src", "text": SOURCE}],
                            "resolution": {"node_id": None}})

    def offline(**kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr("open_tutor.server.tutor", grounded_unresolved)
    monkeypatch.setattr("open_tutor.server.generate_teaching", offline)
    result = _wait(client, client.post(
        f"/api/threads/{thread['id']}/ask",
        json={"question": "Tell me more", "node_id": "node", "teaching": True,
              "action": "respond", "approach": "plain"}).json()["job_id"])
    assert result["status"] == "completed"
    assert result["result"]["status"] == "coaching-fallback"
    after = app.state.storage.get_teaching_state(thread["id"])
    assert after["pending_question"] == "Why does it superpose?"
    assert after["node_id"] == "node"
    app.state.storage.close()