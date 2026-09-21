"""P2.1 — Durable learner state + mastery tracing (BKT).

Deterministic and offline. The persistence/tracing boundary
(learner_state.py) is exercised with synthetic specs and, for the
end-to-end path, the grounded engine (injected corpus text + monkeypatched
oracle) — no model, no network, no clock (timestamps are caller-supplied).

Covered, per the card:
  - round-trip           -> save -> load is lossless (events, projections,
                            mastery, audit trail, provenance)
  - deterministic        -> same stream + same now -> byte-identical file;
                            BKT fold is a pure function of the stream
  - BKT tracer           -> bounded in [0,1]; verified raises mastery;
                            unverified contributes NO signal (honesty);
                            monotone in verified evidence
  - migration            -> a v0 (P1.3 dump) file migrates to v1, mastery
                            recomputed by BKT, events preserved
  - version guard        -> future version rejected (version-too-new),
                            unknown version rejected (version-unknown)
  - subject/spec compat  -> state bound to another subject or to an unknown
                            node is rejected, named, on load and on save
  - atomic writes        -> corrupt/empty file is a named first-class error,
                            never a silent drop; atomic replace, no temp litter
  - restart              -> a fresh store loads prior state, continues,
                            created_at preserved, mastery accumulates
  - concurrency-safe     -> racing threads and racing processes both converge
                            to the exact expected total; duplicate replay is
                            a no-op under race
  - honesty              -> ungrounded/unverified answers never credit
                            mastery; rejected events are audited durably
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import threading

import pytest

from open_tutor import engine
from open_tutor import learner_events as le
from open_tutor import learner_state as ls
from open_tutor.spec import CorpusSource, CurriculumSpec, Misconception, Node

NOW = "2026-09-05T00:00:00Z"          # caller-supplied: deterministic tests
NOW2 = "2026-09-05T00:05:00Z"

QUBIT_M1_TEXT = "A qubit is just a bit that is either zero or one that we don't know which."
SUPER_M1_TEXT = "Superposition means the qubit is secretly zero or one and we just haven't looked."


def make_spec(subject: str = "test-subject") -> CurriculumSpec:
    corpus = [
        CorpusSource(id="src-a", name="Source A", url="https://a.example/qubit"),
        CorpusSource(id="src-b", name="Source B", url="https://b.example/qubit"),
    ]
    node = Node(
        id="qubit", title="The Qubit",
        defn="A qubit is a two-level quantum system with basis states.",
        misconceptions=[Misconception(id="qubit-m1", text=QUBIT_M1_TEXT),
                        Misconception(id="qubit-m2", text="A qubit is a tiny spinning magnet.")],
        covers_keywords=["qubit"],
        grounding_corpus=["src-a", "src-b"],
        oracle="fake_qubit",
    )
    super_node = Node(
        id="superposition", title="Superposition",
        defn="A qubit in a linear combination of basis states.",
        prereqs=["qubit"],
        misconceptions=[Misconception(id="super-m1", text=SUPER_M1_TEXT)],
        covers_keywords=["superposition"],
        grounding_corpus=["src-a"],
    )
    return CurriculumSpec(subject=subject, title="Test", corpus=corpus,
                          nodes=[node, super_node])


QUBIT_TEXT = "\n\n".join(
    f"A qubit is the fundamental two-level quantum unit, the quantum analogue of "
    f"the classical bit, realised by a pair of basis states labelled zero and one. "
    f"Paragraph {i} develops the qubit with further context so the span stays long."
    for i in range(8)
)


@pytest.fixture
def good_oracle(monkeypatch):
    monkeypatch.setattr(
        engine, "run_oracle",
        lambda name: {"ok": True, "result": {"P(|0>)": 0.5, "P(|1>)": 0.5,
                                             "normalized": True}})


def _stub_draft(question, resolution, node, retrieval, oracle):
    return ("## Claim\n"
            "A qubit is described as: “A qubit is a two-level quantum system.” [1].\n"
            "\n"
            "## Citations\n"
            "[1] Source A — https://a.example/qubit\n")


def attempt_event(node_id: str = "qubit", grounded: bool = True,
                  subject: str = "test-subject", **over) -> dict:
    e = {
        "v": le.SCHEMA_VERSION,
        "kind": le.KIND_ATTEMPT,
        "subject": subject,
        "node_id": node_id,
        "question": "What is a qubit?",
        "answer_status": "grounded" if grounded else "unverified",
        "grounded": grounded,
        "quantitative": False,
        "citations_used": [1, 2],
        "oracle_status": "not-required",
    }
    e.update(over)
    return e


def misconception_event(mid: str = "qubit-m1", subject: str = "test-subject", **over) -> dict:
    e = {
        "v": le.SCHEMA_VERSION,
        "kind": le.KIND_MISCONCEPTION,
        "subject": subject,
        "node_id": "qubit",
        "misconception_id": mid,
        "evidence": "A qubit is just a bit we don't know which.",
        "detected_by": le.DETECTOR_KEYWORD_MAP,
    }
    e.update(over)
    return e


def mastery_event(reason: str = le.RULE_GROUNDED_ATTEMPT,
                  node_id: str = "qubit", delta: float = 0.1,
                  subject: str = "test-subject", item_id: str = "item",
                  **over) -> dict:
    verdict = ("correct" if le.RULE_GROUNDED_ATTEMPT in reason else
               "incorrect" if le.RULE_MISCONCEPTION_TRIGGERED in reason else
               "flagged")
    e = {
        "v": le.SCHEMA_VERSION,
        "kind": le.KIND_ASSESSMENT,
        "subject": subject,
        "node_id": node_id,
        "item_id": item_id,
        "verdict": verdict,
        "score": 1.0 if verdict == "correct" else 0.0 if verdict == "incorrect" else None,
        "response_fingerprint": hashlib.sha256(
            f"{subject}:{node_id}:{item_id}:{reason}".encode()).hexdigest(),
    }
    e.update(over)
    return e


def grounded_stream(n: int, subject: str = "test-subject") -> list[dict]:
    """n grounded interactions (attempt + delta per interaction)."""
    out = []
    for i in range(n):
        out.append(attempt_event(question=f"Q{i}", subject=subject))
        out.append(mastery_event(reason=le.RULE_GROUNDED_ATTEMPT, subject=subject,
                                 item_id=f"item-{i}"))
    return out


def unverified_stream(n: int, subject: str = "test-subject") -> list[dict]:
    out = []
    for i in range(n):
        out.append(attempt_event(grounded=False, question=f"Q{i}", subject=subject))
        out.append(mastery_event(reason=le.RULE_UNVERIFIED_ATTEMPT, delta=0.0,
                                 subject=subject, item_id=f"item-{i}"))
    return out


def expected_mastery(n_correct: int, n_incorrect: int = 0) -> float:
    p = 0.0
    for _ in range(n_correct):
        p = ls.bkt_update(p, True)
    for _ in range(n_incorrect):
        p = ls.bkt_update(p, False)
    return p


# ---------- 1. BKT tracer: bounded, monotone, deterministic --------------------
def test_bkt_update_is_bounded_for_all_p():
    for p in (0.0, 0.05, 0.5, 0.99, 1.0):
        for correct in (True, False):
            q = ls.bkt_update(p, correct)
            assert 0.0 <= q <= 1.0


def test_bkt_rejects_non_finite_and_out_of_range_probabilities():
    for bad in (-0.01, 1.01, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="mastery-invalid"):
            ls.bkt_update(bad, True)
        with pytest.raises(ValueError, match="mastery-invalid"):
            ls.trace_mastery([], initial_p=bad)


def test_bkt_correct_raises_mastery():
    assert ls.bkt_update(0.0, True) > 0.0
    assert ls.bkt_update(0.5, True) > ls.bkt_update(0.5, False)


def test_trace_bounded_and_monotone_in_verified_evidence():
    events = [mastery_event(reason=le.RULE_GROUNDED_ATTEMPT)]
    p1 = ls.trace_mastery(events)
    p3 = ls.trace_mastery(events * 3)
    assert 0.0 < p1 < 1.0
    assert p3 > p1 > 0.0


def test_trace_is_pure_and_deterministic():
    events = grounded_stream(3)
    assert ls.trace_mastery(events) == ls.trace_mastery(list(events))


def test_reason_classification_is_named_and_total():
    assert ls.classify_reason(le.RULE_GROUNDED_ATTEMPT) == "correct"
    assert ls.classify_reason(
        f"{le.RULE_GROUNDED_ATTEMPT}; {le.RULE_MISCONCEPTION_TRIGGERED}") == "correct"
    assert ls.classify_reason(le.RULE_UNVERIFIED_ATTEMPT) == "no-signal"
    # the defensive branch: a lone misconception trigger is an incorrect signal
    assert ls.classify_reason(le.RULE_MISCONCEPTION_TRIGGERED) == "incorrect"
    assert ls.classify_reason("some-future-rule") == "no-signal"
    assert ls.classify_reason("") == "no-signal"


def test_lone_misconception_delta_moves_mastery_honestly():
    # defensive branch: an incorrect observation still lowers the posterior
    # (the P1.3 rule set always co-fires the grounded/unverified marker, so
    # this is the tracer's contract for a future rule set, not a live path)
    p = ls.trace_mastery([mastery_event(reason=le.RULE_MISCONCEPTION_TRIGGERED)])
    assert 0.0 < p < 1.0
    assert p == pytest.approx(ls.bkt_update(0.0, False))


# ---------- 2. honesty: unverified answers never credit mastery ---------------
def test_unverified_stream_produces_zero_mastery(tmp_path):
    events = unverified_stream(5)
    assert ls.trace_mastery(events) == 0.0
    # and a store applying them records the attempts but no mastery credit
    spec = make_spec()
    store = ls.LearnerStateStore(str(tmp_path / "s.json"), spec)
    res = store.record_events(events, now=NOW)
    assert all(r.status == "recorded" for r in res)
    assert store.mastery_of("qubit") == 0.0
    assert store.state().attempts == {"qubit": 5}


def test_mixed_stream_mastery_ignores_unverified_steps():
    events = (grounded_stream(2) + unverified_stream(3) + grounded_stream(1))
    got = ls.trace_mastery(events)
    assert got == pytest.approx(expected_mastery(3, 0))


# ---------- 3. round-trip: save -> load is lossless ----------------------------
def test_round_trip_is_lossless(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "state.json")
    store = ls.LearnerStateStore(path, spec)
    # Three complete interactions: two plain grounded (grounded_stream(2)),
    # then a grounded answer that also triggers a validated misconception.
    # Each interaction is an attempt + (misconception) + mastery_delta, so the
    # stream carries exactly three attempt events -> three attempts.
    events = grounded_stream(2) + [
        attempt_event(question="Q2"),
        misconception_event(),
        mastery_event(reason=f"{le.RULE_GROUNDED_ATTEMPT}; "
                             f"{le.RULE_MISCONCEPTION_TRIGGERED}",
                     delta=le.RULE_DELTAS[le.RULE_GROUNDED_ATTEMPT]
                     + le.RULE_DELTAS[le.RULE_MISCONCEPTION_TRIGGERED]),
    ]
    res = store.record_events(events, now=NOW)
    assert all(r.status == "recorded" for r in res)

    loaded = ls.LearnerStateStore(path, spec).load()
    assert loaded is not None
    assert loaded.to_dict() == store.state().to_dict()
    # 3 verified (correct) learning opportunities
    assert loaded.mastery["qubit"] == pytest.approx(expected_mastery(3, 0))
    assert loaded.attempts == {"qubit": 3}
    assert loaded.misconceptions_triggered == {"qubit": ["qubit-m1"]}
    assert len(loaded.events) == len(events)
    assert loaded.nodes[0]["id"] == "qubit"
    assert loaded.nodes[0]["next_review"] is None   # P2.2 owns scheduling
    assert loaded.schema_version == ls.STATE_SCHEMA_VERSION
    assert loaded.tracer == "bkt"


def test_round_trip_via_dict_is_stable(tmp_path):
    spec = make_spec()
    store = ls.LearnerStateStore(str(tmp_path / "s.json"), spec)
    store.record_events(grounded_stream(1), now=NOW)
    d = store.state().to_dict()
    again = ls.LearnerState.from_dict(json.loads(json.dumps(d)))
    assert again.to_dict() == d


# ---------- 4. determinism: same inputs -> byte-identical file ------------------
def test_same_stream_same_now_gives_byte_identical_file(tmp_path):
    spec = make_spec()
    path_a = str(tmp_path / "a.json")
    path_b = str(tmp_path / "b.json")
    stream = grounded_stream(3)
    ls.LearnerStateStore(path_a, spec).record_events(list(stream), now=NOW)
    ls.LearnerStateStore(path_b, spec).record_events(list(stream), now=NOW)
    with open(path_a, "rb") as f:
        a = f.read()
    with open(path_b, "rb") as f:
        b = f.read()
    assert a == b


def test_different_now_only_changes_timestamps(tmp_path):
    spec = make_spec()
    a = ls.LearnerStateStore(str(tmp_path / "a.json"), spec)
    b = ls.LearnerStateStore(str(tmp_path / "b.json"), spec)
    a.record_events(grounded_stream(2), now=NOW)
    b.record_events(grounded_stream(2), now=NOW2)
    da, db = a.state().to_dict(), b.state().to_dict()
    da.pop("created_at"); da.pop("last_updated")
    db.pop("created_at"); db.pop("last_updated")
    assert da == db
    assert a.state().created_at == NOW and b.state().created_at == NOW2


# ---------- 5. migration: v0 (P1.3 dump) -> v1 ----------------------------------
def test_v0_p13_dump_migrates_to_v1(tmp_path):
    spec = make_spec()
    log = le.EventLog(spec)
    log.record_events(grounded_stream(2) + unverified_stream(2))
    v0 = log.summary()
    assert v0["schema_version"] == le.SCHEMA_VERSION   # the P1.3 version ("1")
    # a v0 document is the P1.3 shape: `entries`, raw-sum mastery
    assert "entries" in v0 and "mastery" in v0
    v0["schema_version"] = "0"                          # tag it as the pre-P2.1 shape
    path = str(tmp_path / "state.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(v0, f)

    loaded = ls.LearnerStateStore(path, spec).load()
    assert loaded is not None
    assert loaded.schema_version == "1"
    assert len(loaded.events) == len(v0["entries"])     # events preserved
    assert loaded.attempts == {"qubit": 4}
    # mastery is RECOMPUTED by BKT over the 2 verified steps (unverified = no
    # signal), not the raw rule-delta sum the v0 carried
    assert loaded.mastery["qubit"] == pytest.approx(expected_mastery(2, 0))
    assert v0["mastery"] == {}  # P1.3 no longer treats tutor answers as mastery


def test_migrate_state_rejects_unknown_and_future_versions():
    d = {"subject": "x"}
    with pytest.raises(ValueError, match="version-unknown"):
        ls.migrate_state(d)                              # missing field
    with pytest.raises(ValueError, match="version-unknown"):
        ls.migrate_state({**d, "schema_version": "1.5"})  # not a known version
    with pytest.raises(ValueError, match="version-too-new"):
        ls.migrate_state({**d, "schema_version": "99"})   # future


def test_future_version_file_is_rejected_on_load(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": "99", "subject": "test-subject"}, f)
    with pytest.raises(ValueError, match="version-too-new"):
        ls.LearnerStateStore(path, spec).load()


# ---------- 6. subject/spec compatibility gates ---------------------------------
def test_state_for_another_subject_is_rejected(tmp_path):
    spec = make_spec("test-subject")
    other = make_spec("other-subject")
    path = str(tmp_path / "s.json")
    ls.LearnerStateStore(path, spec).record_events(grounded_stream(1), now=NOW)
    with pytest.raises(ValueError, match="subject-mismatch"):
        ls.LearnerStateStore(path, other).load()


def test_state_with_unknown_node_is_rejected(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    store = ls.LearnerStateStore(path, spec)
    store.record_events(grounded_stream(1), now=NOW)
    st = store.state()
    st.attempts["ghost-node"] = 1
    st.mastery["ghost-node"] = 0.5
    with pytest.raises(ValueError, match="node-unknown"):
        store.save(st, NOW2)
    # and a fresh load of a hand-crafted bad document fails the same way
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1", "tracer": "bkt",
                   "subject": "test-subject", "events": [],
                   "nodes": [], "attempts": {"ghost": 1},
                   "misconceptions_triggered": {}, "mastery": {},
                   "rejections": [], "created_at": NOW, "last_updated": NOW}, f)
    with pytest.raises(ValueError, match="node-unknown"):
        ls.LearnerStateStore(path, spec).load()


def test_subject_isolation_between_two_subjects(tmp_path):
    # two subjects, two files, same machine: neither may touch the other
    spec_a = make_spec("subject-a")
    spec_b = make_spec("subject-b")
    pa, pb = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    sa = ls.LearnerStateStore(pa, spec_a)
    sb = ls.LearnerStateStore(pb, spec_b)
    sa.record_events(grounded_stream(2, subject="subject-a"), now=NOW)
    sb.record_events(grounded_stream(3, subject="subject-b"), now=NOW)
    assert sa.state().mastery["qubit"] == pytest.approx(expected_mastery(2))
    assert sb.state().mastery["qubit"] == pytest.approx(expected_mastery(3))
    assert sa.state().subject == "subject-a"
    assert sb.state().subject == "subject-b"


# ---------- 7. atomic writes & corrupt-file honesty ------------------------------
def test_corrupt_file_is_a_named_first_class_error(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"schema_version": "1", "subject": "test-subject", ')  # torn
    with pytest.raises(ValueError, match="corrupt-state"):
        ls.LearnerStateStore(path, spec).load()
    with open(path, "w", encoding="utf-8") as f:
        f.write("")                                          # empty
    with pytest.raises(ValueError, match="corrupt-state"):
        ls.LearnerStateStore(path, spec).load()


def test_atomic_write_creates_dir_and_leaves_no_temp_litter(tmp_path):
    nested = str(tmp_path / "deep" / "er" / "s.json")
    ls._atomic_write(nested, "hello\n")
    with open(nested, "r", encoding="utf-8") as f:
        assert f.read() == "hello\n"
    litter = [p for p in os.listdir(str(tmp_path / "deep" / "er")) if p.endswith(".tmp")]
    assert litter == []


def test_save_overwrite_is_atomic_replacement(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    store = ls.LearnerStateStore(path, spec)
    store.record_events(grounded_stream(1), now=NOW)
    with open(path, "rb") as f:
        before = f.read()
    store.record_events(grounded_stream(2), now=NOW2)
    with open(path, "rb") as f:
        after = f.read()
    assert before != after
    assert json.loads(after)["last_updated"] == NOW2
    assert json.loads(after)["created_at"] == NOW     # created_at preserved


# ---------- 8. restart: fresh process continues from the file --------------------
def test_restart_loads_and_continues(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    s1 = ls.LearnerStateStore(path, spec)
    s1.record_events(grounded_stream(2), now=NOW)
    created = s1.state().created_at
    del s1   # simulate process exit

    s2 = ls.LearnerStateStore(path, spec)              # a fresh store (restart)
    assert s2.state() is None                          # not loaded yet
    st = s2.load()
    assert st is not None and st.created_at == created
    assert s2.mastery_of("qubit") == pytest.approx(expected_mastery(2))
    # continue learning after the restart: a NEW interaction (distinct question —
    # re-sending Q0 would be a legitimate idempotent replay, not new evidence)
    # -> mastery accumulates monotonically and attempts advances.
    s2.record_events([attempt_event(question="Q2"), mastery_event()], now=NOW2)
    assert s2.state().created_at == created            # not reset by the new save
    assert s2.mastery_of("qubit") == pytest.approx(expected_mastery(3))
    assert s2.state().attempts == {"qubit": 3}


def test_restart_replay_of_same_events_is_a_noop(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    stream = grounded_stream(2)
    ls.LearnerStateStore(path, spec).record_events(list(stream), now=NOW)
    s2 = ls.LearnerStateStore(path, spec)
    res = s2.record_events(list(stream), now=NOW2)
    assert all(r.status == "duplicate" for r in res)
    assert s2.state().attempts == {"qubit": 2}
    assert s2.state().last_updated == NOW              # no-op did not rewrite


# ---------- 9. concurrency: threads and processes --------------------------------
def _thread_worker(path: str, spec: CurriculumSpec, worker: int,
                   barrier: threading.Barrier, out: list) -> None:
    events = grounded_stream(3)
    for i, e in enumerate(events):
        if e["kind"] == le.KIND_ATTEMPT:
            e["question"] = f"w{worker}-q{i}"
        elif e["kind"] == le.KIND_ASSESSMENT:
            e["item_id"] = f"w{worker}-item-{i}"
            e["response_fingerprint"] = hashlib.sha256(
                f"w{worker}:{i}".encode()).hexdigest()
    barrier.wait()
    store = ls.LearnerStateStore(path, spec)
    res = store.record_events(events, now=NOW)
    out.append([r.status for r in res])


def test_racing_threads_converge(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    n_threads, per = 8, 3
    barrier = threading.Barrier(n_threads)
    out: list = []
    threads = [threading.Thread(target=_thread_worker,
                                args=(path, spec, i, barrier, out))
               for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    state = ls.LearnerStateStore(path, spec).load()
    assert state is not None
    assert state.attempts == {"qubit": n_threads * per}
    assert all(s == "recorded" for statuses in out for s in statuses)
    assert state.mastery["qubit"] == pytest.approx(expected_mastery(n_threads * per))


def _proc_worker(path: str, n: int, barrier) -> None:
    events = grounded_stream(n)
    for i, e in enumerate(events):
        if e["kind"] == le.KIND_ATTEMPT:
            e["question"] = f"p{os.getpid()}-q{i}"
        elif e["kind"] == le.KIND_ASSESSMENT:
            e["item_id"] = f"p{os.getpid()}-item-{i}"
            e["response_fingerprint"] = hashlib.sha256(
                f"{os.getpid()}:{i}".encode()).hexdigest()
    barrier.wait()
    store = ls.LearnerStateStore(path, make_spec())
    res = store.record_events(events, now=NOW)
    assert all(r.status == "recorded" for r in res)


def test_racing_processes_converge(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    n_procs, per = 4, 4
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(n_procs)
    procs = [ctx.Process(target=_proc_worker, args=(path, per, barrier))
             for _ in range(n_procs)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
        assert p.exitcode == 0
    state = ls.LearnerStateStore(path, spec).load()
    assert state is not None
    assert state.attempts == {"qubit": n_procs * per}
    assert state.mastery["qubit"] == pytest.approx(expected_mastery(n_procs * per))


def test_duplicate_race_records_exactly_once(tmp_path):
    spec = make_spec()
    path = str(tmp_path / "s.json")
    ev = attempt_event(question="the-one-and-only")
    barrier = threading.Barrier(8)
    out: list = []

    def worker() -> None:
        barrier.wait()
        res = ls.LearnerStateStore(path, spec).record(ev, now=NOW)
        out.append(res.status)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(out) == ["duplicate"] * 7 + ["recorded"] * 1
    assert ls.LearnerStateStore(path, spec).load().attempts == {"qubit": 1}


# ---------- 10. audit: rejections are durable, never silently dropped -----------
def test_rejected_event_is_audited_in_the_file(tmp_path):
    spec = make_spec()
    store = ls.LearnerStateStore(str(tmp_path / "s.json"), spec)
    res = store.record(attempt_event(node_id="does-not-exist"), now=NOW)
    assert res.status == "rejected"
    state = store.state()
    assert len(state.rejections) == 1
    assert any(i.startswith("node-unknown") for i in state.rejections[0]["issues"])
    assert state.attempts == {}                          # nothing applied
    # and it survives a restart
    again = ls.LearnerStateStore(store.path, spec).load()
    assert len(again.rejections) == 1


# ---------- 11. end-to-end: engine answer -> durable state -----------------------
def test_end_to_end_grounded_answer_traces_mastery(good_oracle, tmp_path):
    spec = make_spec()
    store = ls.LearnerStateStore(str(tmp_path / "s.json"), spec)
    a = engine.tutor(spec, "What is a qubit?",
                     corpus_text={"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT},
                     draft_fn=_stub_draft)
    assert a.grounded is True
    utter = ("I thought a qubit is just a bit that is either zero or one "
             "and we don't know which one it is.")
    out = ls.process_answer(a, spec, store, learner_utterance=utter, now=NOW)
    assert out["node_id"] == "qubit"
    assert all(r.status == "recorded" for r in out["results"])
    st = store.state()
    assert st.misconceptions_triggered == {"qubit": ["qubit-m1"]}
    assert st.attempts == {"qubit": 1}
    assert out["mastery"] == 0.0
    assert st.mastery["qubit"] == 0.0


def test_end_to_end_ungrounded_answer_credits_nothing(good_oracle, tmp_path):
    spec = make_spec()
    store = ls.LearnerStateStore(str(tmp_path / "s.json"), spec)
    a = engine.tutor(spec, "What is a qubit?",
                     corpus_text={"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT},
                     draft_fn=lambda q, r, n, ret, o: "I think a qubit is magic. [7]")
    assert a.grounded is False
    ls.process_answer(a, spec, store, now=NOW)
    st = store.state()
    assert st.attempts == {"qubit": 1}     # the interaction is honestly counted
    assert st.mastery["qubit"] == 0.0      # but never credited


def test_end_to_end_no_node_answer_leaves_no_state(good_oracle, tmp_path):
    spec = make_spec()
    store = ls.LearnerStateStore(str(tmp_path / "s.json"), spec)
    a = engine.tutor(spec, "What is photosynthesis?",
                     corpus_text={"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT})
    assert a.status == "no-node"
    out = ls.process_answer(a, spec, store, now=NOW)
    assert out["events"] == []
    assert store.state() is None           # nothing to write: no validated node
    assert not os.path.exists(store.path)
