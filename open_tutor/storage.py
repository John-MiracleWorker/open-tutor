"""Durable local storage for the HTTP boundary.

The learner engine and assessment modules remain the owners of their data
models.  This module owns only persistence and transaction boundaries: SQLite
rows contain JSON projections of those existing models, never a second model
implementation.  Every method opens its own connection, which keeps background
workers and request threads safe without sharing a connection across threads.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class CorruptAdaptiveState(ValueError):
    """Stored adaptive data is invalid; never replace it with silent defaults."""


class AskAdmissionConflict(RuntimeError):
    """A thread already has a queued or running ask."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class Database:
    """A small SQLite repository, scoped to one injected data directory."""

    def __init__(self, data_dir: str | Path | None = None, path: str | Path | None = None):
        if path is None:
            if data_dir is None:
                data_dir = "out"
            path = Path(data_dir) / "open-tutor.sqlite3"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self.recover_inflight_jobs()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    base_url TEXT,
                    model TEXT,
                    mode TEXT NOT NULL DEFAULT 'extractive',
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO settings(id, mode, updated_at)
                    VALUES (1, 'extractive', CURRENT_TIMESTAMP);

                CREATE TABLE IF NOT EXISTS curricula (
                    subject TEXT PRIMARY KEY,
                    active_spec TEXT,
                    active_report TEXT,
                    active_decision TEXT,
                    active_fingerprint TEXT,
                    candidate_spec TEXT,
                    candidate_report TEXT,
                    candidate_decision TEXT,
                    candidate_fingerprint TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subject_state (
                    subject TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS threads_subject_idx ON threads(subject, updated_at);

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    answer_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_thread_idx ON messages(thread_id, created_at);

                CREATE TABLE IF NOT EXISTS learner_preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    preferences_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS thread_teaching_state (
                    thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ask_admissions (
                    thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    thread_id TEXT REFERENCES threads(id) ON DELETE SET NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    error TEXT,
                    result_json TEXT,
                    input_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_thread_idx ON jobs(thread_id, updated_at);

                CREATE TABLE IF NOT EXISTS job_events (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, seq)
                );

                CREATE TABLE IF NOT EXISTS assessment_items (
                    subject TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(subject, item_id)
                );

                CREATE TABLE IF NOT EXISTS assessment_results (
                    subject TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(subject, item_id)
                );
                """
            )
        finally:
            conn.close()

    def close(self) -> None:
        """Compatibility no-op; connections are deliberately per operation."""

    # ----- settings ---------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT base_url, model, mode FROM settings WHERE id=1").fetchone()
            return {"base_url": row["base_url"], "model": row["model"], "mode": row["mode"]}
        finally:
            conn.close()

    # ----- adaptive teaching preferences/state -----------------------------
    def get_preferences(self) -> dict[str, Any]:
        defaults = {
            "schema_version": "1", "approach": "auto", "pace": "balanced",
            "goal": "", "experience": "", "interests": "",
        }
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT preferences_json FROM learner_preferences WHERE id=1"
            ).fetchone()
            if row is None:
                return defaults
            from .teaching import APPROACHES, PACES
            try:
                stored = json.loads(row["preferences_json"])
                valid = (isinstance(stored, dict) and set(stored) == set(defaults)
                         and stored["schema_version"] == "1" and stored["approach"] in APPROACHES
                         and stored["pace"] in PACES
                         and all(isinstance(stored[key], str) and len(stored[key]) <= 500
                                 for key in ("goal", "experience", "interests")))
            except (ValueError, TypeError, KeyError):
                valid = False
            if not valid:
                raise CorruptAdaptiveState("corrupt learner preferences; restore or explicitly reset local data")
            return stored
        finally:
            conn.close()

    def put_preferences(self, values: dict[str, Any]) -> dict[str, Any]:
        defaults = self.get_preferences()
        merged = {**defaults, **values}
        if set(merged) != set(defaults) or merged["schema_version"] != "1":
            raise ValueError("invalid learner preference schema")
        if merged["approach"] not in {"auto", "plain", "socratic", "worked-example",
                                      "analogy", "visual", "challenge"}:
            raise ValueError("invalid learner approach")
        if merged["pace"] not in {"gentle", "balanced", "brisk"}:
            raise ValueError("invalid learner pace")
        for key in ("goal", "experience", "interests"):
            if not isinstance(merged[key], str) or len(merged[key]) > 500:
                raise ValueError(f"{key} exceeds 500 characters")
        with self._write() as conn:
            conn.execute(
                "INSERT INTO learner_preferences(id, preferences_json, updated_at) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET preferences_json=excluded.preferences_json, "
                "updated_at=excluded.updated_at",
                (_json(merged), utc_now()),
            )
        return merged

    def get_teaching_state(self, thread_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_json FROM thread_teaching_state WHERE thread_id=?", (thread_id,)
            ).fetchone()
            from .teaching import TeachingState
            if row is None:
                return {}
            try:
                value = json.loads(row["state_json"])
                TeachingState.from_dict(value)
                if not isinstance(value, dict):
                    raise TypeError("object required")
            except (ValueError, TypeError):
                raise CorruptAdaptiveState("corrupt thread teaching state; restore local data") from None
            return value
        finally:
            conn.close()

    def put_teaching_state(self, thread_id: str, state: dict[str, Any], *, conn=None) -> None:
        from .teaching import TeachingState

        if not isinstance(state, dict):
            raise TypeError("invalid teaching state: object required")
        TeachingState.from_dict(state)
        own = conn is None
        if own:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO thread_teaching_state(thread_id,state_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(thread_id) DO UPDATE SET state_json=excluded.state_json, "
                "updated_at=excluded.updated_at",
                (thread_id, _json(state), utc_now()),
            )
            if own:
                conn.commit()
        except BaseException:
            if own:
                conn.rollback()
            raise
        finally:
            if own:
                conn.close()

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        current.update({k: values[k] for k in ("base_url", "model", "mode") if k in values})
        with self._write() as conn:
            conn.execute(
                "UPDATE settings SET base_url=?, model=?, mode=?, updated_at=? WHERE id=1",
                (current.get("base_url"), current.get("model"), current["mode"], utc_now()),
            )
        return current

    # ----- threads/messages ------------------------------------------------
    def create_thread(self, subject: str, title: str | None = None) -> dict[str, Any]:
        now = utc_now()
        item = {
            "id": uuid.uuid4().hex,
            "subject": subject,
            "title": (title or "New thread").strip() or "New thread",
            "created_at": now,
            "updated_at": now,
        }
        with self._write() as conn:
            conn.execute(
                "INSERT INTO threads VALUES (?,?,?,?,?)",
                (item["id"], item["subject"], item["title"], now, now),
            )
        return item

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
            if row is None:
                return None
            messages = conn.execute(
                "SELECT id, role, content, answer_json, created_at FROM messages "
                "WHERE thread_id=? ORDER BY created_at, rowid",
                (thread_id,),
            ).fetchall()
            active = conn.execute(
                "SELECT * FROM jobs WHERE thread_id=? AND status IN ('queued','running') "
                "ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            return {
                "thread": self._thread_row(row),
                "messages": [self._message_row(m) for m in messages],
                "active_job": self._job_row(active) if active else None,
                "teaching_state": self.get_teaching_state(thread_id),
            }
        finally:
            conn.close()

    def list_threads(self, subject: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            if subject is None:
                rows = conn.execute("SELECT * FROM threads ORDER BY updated_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM threads WHERE subject=? ORDER BY updated_at DESC", (subject,)
                ).fetchall()
            return [self._thread_row(r) for r in rows]
        finally:
            conn.close()

    def add_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        answer: dict[str, Any] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        own = conn is None
        if own:
            conn = self._connect()
            conn.execute("BEGIN IMMEDIATE")
        now = utc_now()
        item = {
            "id": uuid.uuid4().hex,
            "role": role,
            "content": content,
            "answer": answer,
            "created_at": now,
        }
        try:
            assert conn is not None
            conn.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?)",
                (
                    item["id"],
                    thread_id,
                    role,
                    content,
                    _json(answer) if answer is not None else None,
                    now,
                ),
            )
            conn.execute("UPDATE threads SET updated_at=? WHERE id=?", (now, thread_id))
            if own:
                conn.commit()
            return item
        except BaseException:
            if own:
                conn.rollback()
            raise
        finally:
            if own:
                conn.close()

    # ----- jobs/events ------------------------------------------------------
    def create_job(
        self,
        kind: str,
        subject: str,
        *,
        thread_id: str | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        item = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "subject": subject,
            "thread_id": thread_id,
            "status": "queued",
            "stage": "queued",
            "error": None,
            "result": None,
            "input": input_data or {},
            "created_at": now,
            "updated_at": now,
        }
        with self._write() as conn:
            conn.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item["id"],
                    kind,
                    subject,
                    thread_id,
                    "queued",
                    "queued",
                    None,
                    None,
                    _json(item["input"]),
                    now,
                    now,
                ),
            )
            self._insert_event(conn, item["id"], "stage", {"stage": "queued"}, now)
        return item

    def admit_ask(
        self, thread_id: str, subject: str, question: str,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically reserve a thread and create its user message and job.

        The unique thread key is the admission gate.  There is no check-then-
        write window, and a conflict leaves both messages and jobs untouched.
        """
        now = utc_now()
        job_id = uuid.uuid4().hex
        message = {
            "id": uuid.uuid4().hex, "role": "user", "content": question,
            "answer": None, "created_at": now,
        }
        item = {
            "id": job_id, "kind": "ask", "subject": subject, "thread_id": thread_id,
            "status": "queued", "stage": "queued", "error": None, "result": None,
            "input": input_data or {}, "created_at": now, "updated_at": now,
        }
        with self._write() as conn:
            conn.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?)",
                (message["id"], thread_id, "user", question, None, now),
            )
            conn.execute("UPDATE threads SET updated_at=? WHERE id=?", (now, thread_id))
            conn.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, "ask", subject, thread_id, "queued", "queued", None, None,
                 _json(item["input"]), now, now),
            )
            try:
                conn.execute(
                    "INSERT INTO ask_admissions(thread_id,job_id,created_at) VALUES(?,?,?)",
                    (thread_id, job_id, now),
                )
            except sqlite3.IntegrityError as exc:
                raise AskAdmissionConflict(thread_id) from exc
            self._insert_event(conn, job_id, "stage", {"stage": "queued"}, now)
        return {"job": item, "message": message}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._job_row(row) if row else None
        finally:
            conn.close()

    def set_job_stage(self, job_id: str, stage: str, *, status: str = "running") -> None:
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, stage=?, updated_at=? WHERE id=?",
                (status, stage, now, job_id),
            )
            self._insert_event(conn, job_id, "stage", {"stage": stage}, now)

    def fail_job(self, job_id: str, error: str, *, status: str = "failed") -> None:
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "UPDATE jobs SET status=?, stage=?, error=?, updated_at=? WHERE id=?",
                (status, "error", error[:4000], now, job_id),
            )
            self._insert_event(conn, job_id, "error", {"error": error[:4000]}, now)
            self._insert_event(conn, job_id, "done", {"status": status}, now)
            conn.execute("DELETE FROM ask_admissions WHERE job_id=?", (job_id,))

    def complete_job(
        self,
        job_id: str,
        result: dict[str, Any],
        *,
        assistant: tuple[str, dict[str, Any]] | None = None,
        teaching_state: dict[str, Any] | None = None,
    ) -> None:
        """Commit result/message before emitting answer and done events."""
        now = utc_now()
        with self._write() as conn:
            row = conn.execute("SELECT thread_id FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            conn.execute(
                "UPDATE jobs SET status='completed', stage='completed', result_json=?, "
                "updated_at=? WHERE id=?",
                (_json(result), now, job_id),
            )
            if assistant is not None:
                self.add_message(
                    row["thread_id"], "assistant", assistant[0], assistant[1], conn=conn
                )
                self._insert_event(conn, job_id, "answer", {"answer": assistant[1]}, now)
            if teaching_state is not None:
                self.put_teaching_state(row["thread_id"], teaching_state, conn=conn)
            self._insert_event(conn, job_id, "done", {"status": "completed"}, now)
            conn.execute("DELETE FROM ask_admissions WHERE job_id=?", (job_id,))

    def get_job_events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM job_events WHERE job_id=? AND seq>? ORDER BY seq", (job_id, after)
            ).fetchall()
            return [
                {
                    "seq": r["seq"],
                    "event": r["event_type"],
                    "payload": _loads(r["payload_json"], {}),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def recover_inflight_jobs(self) -> None:
        now = utc_now()
        with self._write() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status IN ('queued','running')"
            ).fetchall()
            for row in rows:
                msg = "interrupted: process restarted before job completed"
                conn.execute(
                    "UPDATE jobs SET status='interrupted', stage='error', error=?, updated_at=? "
                    "WHERE id=?",
                    (msg, now, row["id"]),
                )
                self._insert_event(conn, row["id"], "error", {"error": msg}, now)
                self._insert_event(conn, row["id"], "done", {"status": "interrupted"}, now)
                conn.execute("DELETE FROM ask_admissions WHERE job_id=?", (row["id"],))

    # ----- curricula and learner state -------------------------------------
    def put_active_bundle(
        self, spec: Any, report: dict[str, Any], decision: dict[str, Any], fingerprint: str
    ) -> None:
        self._put_bundle("active", spec, report, decision, fingerprint)

    def put_candidate_bundle(
        self, spec: Any, report: dict[str, Any], decision: dict[str, Any], fingerprint: str
    ) -> None:
        self._put_bundle("candidate", spec, report, decision, fingerprint)

    def _put_bundle(
        self,
        kind: str,
        spec: Any,
        report: dict[str, Any],
        decision: dict[str, Any],
        fingerprint: str,
    ) -> None:
        spec_dict = spec.to_dict() if hasattr(spec, "to_dict") else dict(spec)
        subject = spec_dict["subject"]
        now = utc_now()
        with self._write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO curricula(subject, updated_at) VALUES (?,?)", (subject, now)
            )
            conn.execute(
                f"UPDATE curricula SET {kind}_spec=?, {kind}_report=?, "
                f"{kind}_decision=?, {kind}_fingerprint=?, updated_at=? WHERE subject=?",
                (_json(spec_dict), _json(report), _json(decision), fingerprint, now, subject),
            )

    def get_active_bundle(self, subject: str) -> dict[str, Any] | None:
        return self._get_bundle(subject, "active")

    def get_candidate_bundle(self, subject: str) -> dict[str, Any] | None:
        return self._get_bundle(subject, "candidate")

    def _get_bundle(self, subject: str, kind: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT {kind}_spec, {kind}_report, {kind}_decision, "
                f"{kind}_fingerprint FROM curricula WHERE subject=?",
                (subject,),
            ).fetchone()
            if not row or row[f"{kind}_spec"] is None:
                return None
            return {
                "spec": _loads(row[f"{kind}_spec"], {}),
                "report": _loads(row[f"{kind}_report"], {}),
                "decision": _loads(row[f"{kind}_decision"], {}),
                "fingerprint": row[f"{kind}_fingerprint"],
            }
        finally:
            conn.close()

    def list_active_bundles(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT subject FROM curricula WHERE active_spec IS NOT NULL ORDER BY subject"
            ).fetchall()
            return [self.get_active_bundle(r["subject"]) for r in rows]
        finally:
            conn.close()

    def promote_candidate(self, subject: str, fingerprint: str) -> dict[str, Any] | None:
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM curricula WHERE subject=? AND candidate_fingerprint=?",
                (subject, fingerprint),
            ).fetchone()
            if row is None or row["candidate_spec"] is None:
                return None
            conn.execute(
                "UPDATE curricula SET active_spec=candidate_spec, active_report=candidate_report, "
                "active_decision=candidate_decision, active_fingerprint=candidate_fingerprint, "
                "candidate_spec=NULL, candidate_report=NULL, candidate_decision=NULL, "
                "candidate_fingerprint=NULL, updated_at=? WHERE subject=?",
                (utc_now(), subject),
            )
            return {
                "spec": _loads(row["candidate_spec"], {}),
                "report": _loads(row["candidate_report"], {}),
                "decision": _loads(row["candidate_decision"], {}),
            }

    def reject_candidate(self, subject: str, fingerprint: str) -> bool:
        with self._write() as conn:
            cur = conn.execute(
                "UPDATE curricula SET candidate_spec=NULL, candidate_report=NULL, "
                "candidate_decision=NULL, candidate_fingerprint=NULL, updated_at=? "
                "WHERE subject=? AND candidate_fingerprint=?",
                (utc_now(), subject, fingerprint),
            )
            return cur.rowcount == 1

    def put_subject_state(self, subject: str, state: dict[str, Any]) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO subject_state(subject,state_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(subject) DO UPDATE SET state_json=excluded.state_json, "
                "updated_at=excluded.updated_at",
                (subject, _json(state), utc_now()),
            )

    def get_subject_state(self, subject: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT state_json FROM subject_state WHERE subject=?", (subject,)
            ).fetchone()
            return _loads(row["state_json"], None) if row else None
        finally:
            conn.close()

    # ----- assessment issuance ---------------------------------------------
    def put_assessment_item(self, subject: str, item: dict[str, Any]) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO assessment_items VALUES (?,?,?,?)",
                (subject, item["id"], _json(item), utc_now()),
            )

    def get_assessment_item(self, subject: str, item_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT item_json FROM assessment_items WHERE subject=? AND item_id=?",
                (subject, item_id),
            ).fetchone()
            return _loads(row["item_json"], None) if row else None
        finally:
            conn.close()

    def put_assessment_result(self, subject: str, item_id: str,
                              result: dict[str, Any]) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO assessment_results VALUES (?,?,?,?)",
                (subject, item_id, _json(result), utc_now()),
            )

    def get_assessment_result(self, subject: str, item_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT result_json FROM assessment_results WHERE subject=? AND item_id=?",
                (subject, item_id),
            ).fetchone()
            return _loads(row["result_json"], None) if row else None
        finally:
            conn.close()

    # ----- row projections --------------------------------------------------
    @staticmethod
    def _thread_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "subject": row["subject"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _message_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "answer": _loads(row["answer_json"], None),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _job_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "subject": row["subject"],
            "thread_id": row["thread_id"],
            "status": row["status"],
            "stage": row["stage"],
            "error": row["error"],
            "result": _loads(row["result_json"], None),
            "input": _loads(row["input_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection, job_id: str, event: str, payload: dict[str, Any], now: str
    ) -> None:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM job_events WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO job_events VALUES (?,?,?,?,?)",
            (job_id, row["next"], event, _json(payload), now),
        )
