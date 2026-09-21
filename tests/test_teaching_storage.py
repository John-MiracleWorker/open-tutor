from __future__ import annotations

import threading

import pytest

from open_tutor.storage import AskAdmissionConflict, Database


def test_preferences_default_and_roundtrip_across_instances(tmp_path):
    db = Database(path=tmp_path / "data.sqlite3")
    assert db.get_preferences()["approach"] == "auto"
    saved = db.put_preferences({"approach": "visual", "pace": "gentle", "goal": "learn"})
    assert saved["approach"] == "visual"
    reopened = Database(path=tmp_path / "data.sqlite3")
    assert reopened.get_preferences() == saved


def test_atomic_admission_allows_one_inflight_ask(tmp_path):
    db = Database(path=tmp_path / "data.sqlite3")
    thread = db.create_thread("demo")
    barrier = threading.Barrier(2)
    outcomes = []

    def admit():
        barrier.wait()
        try:
            outcomes.append(db.admit_ask(thread["id"], "demo", "hello", {"question": "hello"}))
        except AskAdmissionConflict:
            outcomes.append(None)

    threads = [threading.Thread(target=admit) for _ in range(2)]
    for worker in threads:
        worker.start()
    for worker in threads:
        worker.join()
    assert sum(value is not None for value in outcomes) == 1
    assert len(db.get_thread(thread["id"])["messages"]) == 1


def test_completion_commits_teaching_state_message_and_events_together(tmp_path):
    db = Database(path=tmp_path / "data.sqlite3")
    thread = db.create_thread("demo")
    admitted = db.admit_ask(thread["id"], "demo", "hello", {"question": "hello"})
    result = {"status": "coaching", "grounded": False}
    db.complete_job(
        admitted["job"]["id"], result,
        assistant=("coaching", result),
        teaching_state={"node_id": "node", "turn_count": 1},
    )
    readback = db.get_thread(thread["id"])
    assert readback["teaching_state"]["turn_count"] == 1
    assert readback["messages"][-1]["answer"] == result
    assert db.get_job_events(admitted["job"]["id"])[-1]["event"] == "done"
    assert db.admit_ask(thread["id"], "demo", "again", {"question": "again"})["job"]


@pytest.mark.parametrize("invalid", [
    {"unknown": "value"}, {"hint_level": 4}, {"turn_count": True},
    None, [], {"approach": "invented"},
])
def test_invalid_teaching_state_write_preserves_last_good_state(tmp_path, invalid):
    db = Database(path=tmp_path / "data.sqlite3")
    thread = db.create_thread("demo")["id"]
    saved = {"node_id": "node", "pending_question": "What is a state?", "turn_count": 1}
    db.put_teaching_state(thread, saved)
    error_type = ValueError if isinstance(invalid, dict) else TypeError
    with pytest.raises(error_type, match="teaching state"):
        db.put_teaching_state(thread, invalid)
    assert db.get_teaching_state(thread) == saved


def test_invalid_completion_state_rolls_back_message_job_events_and_admission(tmp_path):
    db = Database(path=tmp_path / "data.sqlite3")
    thread = db.create_thread("demo")["id"]
    db.put_teaching_state(thread, {"pending_question": "What is a state?"})
    admitted = db.admit_ask(thread, "demo", "A hint", {"question": "A hint"})
    job_id = admitted["job"]["id"]
    before = db.get_thread(thread)
    job_before = db.get_job(job_id)
    events_before = db.get_job_events(job_id)
    result = {"status": "coaching", "grounded": False}
    with pytest.raises(ValueError, match="teaching state"):
        db.complete_job(job_id, result, assistant=("not delivered", result),
                        teaching_state={"hint_level": 4})
    assert db.get_thread(thread) == before
    assert db.get_job(job_id) == job_before
    assert db.get_job_events(job_id) == events_before
    with pytest.raises(AskAdmissionConflict):
        db.admit_ask(thread, "demo", "again", {"question": "again"})
