from __future__ import annotations

import json

from open_tutor.spec import CorpusSource, CurriculumSpec, Node
from open_tutor.storage import Database


def spec(subject: str = "demo") -> CurriculumSpec:
    return CurriculumSpec(
        subject=subject,
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


def test_sqlite_threads_messages_jobs_and_settings_survive_restart(tmp_path):
    db = Database(path=tmp_path / "tutor.sqlite3")
    thread = db.create_thread("demo", "A thread")
    db.add_message(thread["id"], "user", "hello")
    job = db.create_job("ask", "demo", thread_id=thread["id"], input_data={"q": "hello"})
    db.update_settings(
        {"base_url": "http://127.0.0.1:1234/v1", "model": "local", "mode": "extractive"}
    )
    db.close()

    reopened = Database(path=tmp_path / "tutor.sqlite3")
    got = reopened.get_thread(thread["id"])
    assert got["thread"]["subject"] == "demo"
    assert got["messages"][0]["content"] == "hello"
    assert reopened.get_job(job["id"])["status"] == "interrupted"
    assert reopened.get_settings()["model"] == "local"


def test_recovery_marks_inflight_jobs_interrupted(tmp_path):
    db = Database(path=tmp_path / "tutor.sqlite3")
    job = db.create_job("ask", "demo")
    db.set_job_stage(job["id"], "running")
    db.close()

    reopened = Database(path=tmp_path / "tutor.sqlite3")
    recovered = reopened.get_job(job["id"])
    assert recovered["status"] == "interrupted"
    assert "interrupted" in recovered["error"]


def test_subject_state_isolated_and_events_are_durable(tmp_path):
    db = Database(path=tmp_path / "tutor.sqlite3")
    db.put_subject_state("demo", {"subject": "demo", "events": [], "mastery": {"node": 0.2}})
    db.put_subject_state("other", {"subject": "other", "events": [], "mastery": {"node": 0.8}})
    db.close()
    reopened = Database(path=tmp_path / "tutor.sqlite3")
    assert reopened.get_subject_state("demo")["mastery"]["node"] == 0.2
    assert reopened.get_subject_state("other")["mastery"]["node"] == 0.8


def test_candidate_never_replaces_active_bundle(tmp_path):
    db = Database(path=tmp_path / "tutor.sqlite3")
    active = spec()
    report = {"subject": "demo", "summary": {"all_grounded": True}}
    decision = {"status": "compiled"}
    db.put_active_bundle(active, report, decision, "active-fingerprint")
    candidate = spec()
    candidate.title = "Candidate"
    db.put_candidate_bundle(
        candidate,
        {"subject": "demo", "summary": {"all_grounded": False}},
        {"status": "blocked"},
        "candidate-fingerprint",
    )
    assert db.get_active_bundle("demo")["spec"]["title"] == "Demo"
    assert db.get_candidate_bundle("demo")["spec"]["title"] == "Candidate"
