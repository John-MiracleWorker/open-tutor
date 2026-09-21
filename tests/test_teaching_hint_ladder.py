"""Hints on a real pending task are host-composed pointers, never model prose.

The reproduced defect class is answer leakage in model-authored hint titles and
explanations.  The fix is structural: with a pending task, a hint turn is
assembled by the host from a fixed cue, checked source names, and verbatim
question-free fragments of those checked passages, grown by hint level.  No
model inference runs for such a turn.
"""
import pytest

from open_tutor.teaching import (
    TeachingState,
    _hint_sentences,
    compose_hint,
    select_plan,
    validate_teaching_output,
)
from open_tutor.server import _prior_ready_context
from test_teaching_server import _setup, _wait

PENDING = "In one sentence, distinguish the height f(3) from the slope f'(3) for x^2."
EVIDENCE = [
    {"ref": 1, "source_name": "Calculus notes", "text": "The derivative measures slope. " * 10},
    {"ref": 2, "source_name": "Rates primer",
     "text": "Height is the value f(x). Is slope the same thing? No. Slope is the rate of change. " * 5},
    {"ref": 3, "source_name": "Extra source", "text": "Unrelated filler. " * 20},
]


def _plan(level):
    return select_plan("A hint", action="hint",
                       state=TeachingState(node_id="n", pending_question=PENDING, hint_level=level - 1))


def test_composed_hint_is_valid_and_preserves_the_task_exactly():
    teaching = compose_hint(plan=_plan(1), pending_question=PENDING,
                            evidence=EVIDENCE, prior_refs=[1, 2])
    assert validate_teaching_output(teaching, known_refs={1, 2, 3}) == teaching
    assert teaching["activity"]["prompt"] == PENDING
    assert teaching["steps"] == [] and teaching["diagram"] is None
    assert "?" not in teaching["title"] and "?" not in teaching["explanation"]


def test_hint_level_one_points_at_sources_without_quoting():
    teaching = compose_hint(plan=_plan(1), pending_question=PENDING,
                            evidence=EVIDENCE, prior_refs=[1, 2])
    assert teaching["evidence_refs"] == [1, 2]
    assert "Calculus notes" in teaching["explanation"]
    assert "Rates primer" in teaching["explanation"]
    assert "The derivative measures slope" not in teaching["explanation"]


def test_hint_quote_ladder_grows_with_level():
    levels = {level: compose_hint(plan=_plan(level), pending_question=PENDING,
                                   evidence=EVIDENCE, prior_refs=[1, 2]) for level in (1, 2, 3)}
    assert len(levels[2]["explanation"]) > len(levels[1]["explanation"])
    assert len(levels[3]["explanation"]) >= len(levels[2]["explanation"])
    assert "pointer" in levels[3]["explanation"].casefold()


def test_hint_quotes_are_verbatim_declarative_source_fragments():
    sentences = _hint_sentences(EVIDENCE[1]["text"], 480)
    assert sentences == ["Height is the value f(x).", "Slope is the rate of change."]
    assert all(sentence in EVIDENCE[1]["text"] for sentence in sentences)
    assert all("?" not in sentence for sentence in sentences)
    assert not any("Is slope the same thing" in sentence for sentence in sentences)


def test_composed_hint_cites_only_currently_supplied_references():
    teaching = compose_hint(plan=_plan(2), pending_question=PENDING,
                            evidence=EVIDENCE, prior_refs=[2, 9])
    assert teaching["evidence_refs"] == [2]
    assert compose_hint(plan=_plan(2), pending_question=PENDING,
                        evidence=EVIDENCE, prior_refs=[9])["evidence_refs"] == [1]
    assert compose_hint(plan=_plan(2), pending_question=PENDING,
                        evidence=EVIDENCE, prior_refs=[])["evidence_refs"] == [1]


def test_composed_hint_requires_a_pending_question():
    with pytest.raises(ValueError, match="pending"):
        compose_hint(plan=_plan(1), pending_question="", evidence=EVIDENCE, prior_refs=[1])


def _prior_answer(prompt, refs, kind="predict"):
    return {"grounded": False, "status": "coaching", "verification_scope": "evidence-only",
            "evidence_grounded": True, "evidence": {"grounded": True, "status": "grounded", "citations": []},
            "draft": "prior", "teaching": {"status": "ready",
            "activity": {"kind": kind, "prompt": prompt}, "evidence_refs": refs}}


def test_prior_ready_context_returns_the_task_setting_turn():
    refs, kind = _prior_ready_context([{"role": "assistant", "answer": _prior_answer(PENDING, [2])}], PENDING)
    assert refs == [2] and kind == "predict"
    assert _prior_ready_context([{"role": "assistant", "answer": _prior_answer("Other?", [2])}], PENDING) == ([], None)
    assert _prior_ready_context([], PENDING) == ([], None)


def test_hint_on_pending_task_makes_no_model_call(tmp_path, monkeypatch):
    app, client = _setup(tmp_path)
    thread = client.post("/api/threads", json={"subject": "demo"}).json()["id"]
    app.state.storage.add_message(thread, "assistant", "prior", answer=_prior_answer(PENDING, [2]))
    app.state.storage.put_teaching_state(thread, {"node_id": "node", "pending_question": PENDING,
                                                  "turn_count": 1, "hint_level": 0})
    def forbidden(**kwargs):
        raise AssertionError("a composed hint must not call the teaching model")
    monkeypatch.setattr("open_tutor.server.generate_teaching", forbidden)
    result = _wait(client, client.post(f"/api/threads/{thread}/ask", json={
        "question": "A hint please", "node_id": "node", "teaching": True, "action": "hint"}).json()["job_id"])
    teaching = result["result"]["teaching"]
    assert teaching["status"] == "ready"
    assert teaching["activity"]["prompt"] == PENDING
    assert teaching["model"] is None
    assert teaching["explanation"].strip()
    assert "?" not in teaching["explanation"]
    state = client.get(f"/api/threads/{thread}").json()["teaching_state"]
    assert state["hint_level"] == 1 and state["pending_question"] == PENDING
    assert client.get(f"/api/threads/{thread}").json()["messages"][-1]["answer"] == result["result"]
    app.state.storage.close()


def test_hint_without_pending_task_still_uses_the_model(tmp_path, monkeypatch):
    app, client = _setup(tmp_path)
    app.state.storage.update_settings({"base_url": "http://127.0.0.1:9120", "model": "test-local", "mode": "local-model"})
    thread = client.post("/api/threads", json={"subject": "demo"}).json()["id"]
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return {"title": "Orientation", "explanation": "A brief orientation.", "steps": [], "diagram": None,
                "activity": {"kind": "reflect", "prompt": "Describe the idea."}, "evidence_refs": [1]}
    monkeypatch.setattr("open_tutor.server.generate_teaching", generate)
    result = _wait(client, client.post(f"/api/threads/{thread}/ask", json={
        "question": "A hint please", "node_id": "node", "teaching": True, "action": "hint"}).json()["job_id"])
    assert result["result"]["teaching"]["status"] == "ready"
    assert result["result"]["teaching"]["model"] == "test-local"
    assert len(calls) == 1
    app.state.storage.close()