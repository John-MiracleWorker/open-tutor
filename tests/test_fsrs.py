"""P2.2 — FSRS-4 review scheduling (fsrs.py) — pure, deterministic, offline.

Covers the card:
  - initial states       -> S0(G) = w[G-1], D0(G) = w4 - (G-3) w5 (clamped)
  - recall update        -> stability_after_recall (grades 2/3/4) vs hand-computed
                            FSRS-4 values; hard < good < easy interval ordering
  - lapse update         -> stability_after_lapse (grade 1) vs hand-computed;
                            a lapse always shrinks the interval
  - difficulty           -> next_difficulty mean-reverts and stays in [1, 10]
  - retrievability/interval -> R(S, S) = 0.9 and I(0.9, S) = S by construction
  - clock injection      -> the module never reads the clock: the same
                            (state, grade, review_time) inputs give a byte-identical
                            state; the due time is derived purely from the
                            injected review_time
  - early review         -> elapsed < 0 clamps to 0 (always a recall, never a lapse)
  - replay               -> replay_reviews is a pure fold of the event stream;
                            None for an empty stream; deterministic; order-sensitive
  - grade validation     -> unknown grades are named FSRSValueError, never silent
  - retention            -> out-of-range retention is named, in-range changes I
  - MemoryState          -> to_dict/from_dict round-trips losslessly
"""
from __future__ import annotations

import math
from datetime import datetime

import pytest

from open_tutor import fsrs as f


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d") + "T00:00:00Z"


# ---------- 1. initial states ----------------------------------------------------
def test_initial_states_match_official_weights():
    # S0(G) = w[G-1] with the official FSRS-4 default weights (W[0..3]).
    assert f.initial_stability(1) == 0.4
    assert f.initial_stability(2) == 0.6
    assert f.initial_stability(3) == 2.4
    assert f.initial_stability(4) == 5.8
    # D0(G) = w4 - (G-3) w5, clamped to [1, 10].
    assert f.initial_difficulty(1) == pytest.approx(6.81)
    assert f.initial_difficulty(2) == pytest.approx(5.87)
    assert f.initial_difficulty(3) == pytest.approx(4.93)
    assert f.initial_difficulty(4) == pytest.approx(3.99)


def test_schedule_node_initial_state_deterministic():
    s = f.schedule_node(None, 3, "2026-01-01T00:00:00Z")
    assert s.reviews == 1
    assert s.stability == pytest.approx(2.4)
    assert s.difficulty == pytest.approx(4.93)
    assert s.last_review == "2026-01-01T00:00:00Z"
    # I(0.9, 2.4) = 2.4 days -> due 2026-01-03.
    assert s.next_review == "2026-01-03T00:00:00Z"
    # byte-identical on re-run (no clock, no randomness).
    s2 = f.schedule_node(None, 3, "2026-01-01T00:00:00Z")
    assert s.to_dict() == s2.to_dict()


# ---------- 2. the pure math vs hand-computed values ------------------------------
def test_stability_after_recall_grade3_hand_computed():
    # FSRS v4: D0(3) = 4.93 and the recall equation uses w8..w10.
    expected = 2.4 * (
        1.0
        + math.exp(1.49) * (11.0 - f.next_difficulty(5.8, 3))
        * 2.4 ** (-0.14)
        * (math.exp(0.94 * (1.0 - 0.9)) - 1.0)
    )
    got = f.stability_after_recall(2.4, 5.8, 0.9, 3)
    assert got == pytest.approx(expected, rel=1e-9)


def test_stability_after_recall_grade1_rejected():
    with pytest.raises(f.FSRSValueError) as e:
        f.stability_after_recall(2.4, 5.8, 0.9, 1)
    assert "grade-not-recall" in str(e.value)


def test_stability_after_lapse_hand_computed():
    # FSRS v4 lapse uses the pre-review D, not D', and is multiplicative.
    expected = (2.18 * 5.8 ** (-0.05) * ((2.4 + 1.0) ** 0.34 - 1.0)
                * math.exp(1.26 * (1.0 - 0.9)))
    got = f.stability_after_lapse(2.4, 5.8, 0.9)
    assert got == pytest.approx(expected, rel=1e-9)
    # a lapse must shrink stability relative to the pre-lapse value
    assert got < 2.4


def test_next_difficulty_mean_reverts_and_bounded():
    d0 = f.initial_difficulty(3)
    # easy nudges difficulty down, again nudges it up, both toward D0(3)
    assert f.next_difficulty(d0, 4) < d0
    assert f.next_difficulty(d0, 1) > d0
    # always in [1, 10] even from extreme inputs
    assert 1.0 <= f.next_difficulty(1.0, 1) <= 10.0
    assert 1.0 <= f.next_difficulty(10.0, 4) <= 10.0


def test_retrievability_and_interval_invariants():
    # R(S, S) = (1 + S/(9S))^-1 = 0.9 by construction.
    assert f.retrievability(2.4, 2.4) == pytest.approx(0.9)
    # I(0.9, S) = 9*S*(1/0.9 - 1) = S by construction.
    assert f.next_interval(2.4, 0.9) == pytest.approx(2.4)
    # retrievability is 1.0 at t=0 and monotone decreasing in t.
    assert f.retrievability(0.0, 2.4) == pytest.approx(1.0)
    assert f.retrievability(1.0, 2.4) > f.retrievability(2.0, 2.4)
    # negative elapsed (early review) is treated as "just reviewed".
    assert f.retrievability(-5.0, 2.4) == 1.0


def test_retention_range_and_effect():
    with pytest.raises(f.FSRSValueError) as e:
        f.next_interval(2.4, 0.0)
    assert "retention-out-of-range" in str(e.value)
    with pytest.raises(f.FSRSValueError):
        f.next_interval(2.4, 1.0)
    # a higher requested recall probability -> a shorter interval.
    assert f.next_interval(2.4, 0.95) < f.next_interval(2.4, 0.9) < f.next_interval(2.4, 0.8)


# ---------- 3. grade validation (named errors, never silent) ----------------------
def test_grade_update_rejects_unknown_grade():
    with pytest.raises(f.FSRSValueError) as e:
        f.grade_update(None, 5, 0.0)
    assert "grade-unknown" in str(e.value)
    with pytest.raises(f.FSRSValueError):
        f.grade_update(None, 0, 0.0)


def test_schedule_node_requires_review_time():
    with pytest.raises(f.FSRSValueError) as e:
        f.schedule_node(None, 3, "")
    assert "review-time-missing" in str(e.value)
    with pytest.raises(f.FSRSValueError):
        f.schedule_node(None, 3, "   ")


def test_replay_reviews_rejects_bad_payloads():
    with pytest.raises(f.FSRSValueError) as e:
        f.replay_reviews([{"grade": 9, "review_time": "2026-01-01T00:00:00Z"}])
    assert "grade-unknown" in str(e.value)
    with pytest.raises(f.FSRSValueError) as e:
        f.replay_reviews([{"grade": 3, "review_time": ""}])
    assert "review-time-missing" in str(e.value)
    # bool is not an int grade (isinstance(True, int) is True in Python).
    with pytest.raises(f.FSRSValueError):
        f.replay_reviews([{"grade": True, "review_time": "2026-01-01T00:00:00Z"}])


# ---------- 4. the two-review trajectory (hand-computed end to end) ----------------
def test_two_review_trajectory_matches_hand_computation():
    # Review 1 is a new card: its memory state is S0/D0.
    r1 = f.schedule_node(None, 3, "2026-01-01T00:00:00Z")
    d1 = 4.93
    s1 = 2.4
    assert r1.stability == pytest.approx(s1, rel=1e-6)
    assert r1.difficulty == pytest.approx(d1)
    assert r1.next_review == "2026-01-03T00:00:00Z"

    # Review 2 (easy, 2026-01-03, at the rounded due date).
    d2 = f.next_difficulty(d1, 4)
    r = f.retrievability(2.0, s1)
    s2 = (s1
          * (1.0
             + math.exp(1.49) * (11.0 - d2)
             * s1 ** (-0.14)
             * (math.exp(0.94 * (1.0 - r)) - 1.0)
             * 2.61))                             # w16 easy multiplier
    r2 = f.schedule_node(r1, 4, "2026-01-03T00:00:00Z")
    assert r2.reviews == 2
    assert r2.stability == pytest.approx(s2, rel=1e-6)
    assert r2.difficulty == pytest.approx(d2, rel=1e-6)
    # I(0.9, S2) = S2 -> due is the injected review date plus rounded days.
    assert r2.next_review == "2026-01-20T00:00:00Z"


def test_grade_ordering_easy_longer_than_good_longer_than_hard():
    # Same prior state, same elapsed time: easy interval > good > hard.
    base = f.MemoryState(2.4, 5.8, 1, "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z")
    t = "2026-01-03T00:00:00Z"  # at the due time
    hard = f.schedule_node(base, 2, t)
    good = f.schedule_node(base, 3, t)
    easy = f.schedule_node(base, 4, t)
    def _days(s: f.MemoryState) -> float:
        a = datetime.fromisoformat(s.last_review.replace("Z", "+00:00"))
        b = datetime.fromisoformat(s.next_review.replace("Z", "+00:00"))
        return (b - a).total_seconds() / 86400.0
    assert _days(easy) > _days(good) > _days(hard)


# ---------- 5. early review never lapses ------------------------------------------
def test_early_review_is_a_recall_never_a_lapse():
    # Review at day 0, due in 2.4 days; reviewing at day 1 (before the due
    # time) with grade 3 must still be a recall (stability grows), not a lapse.
    base = f.schedule_node(None, 3, "2026-01-01T00:00:00Z")
    early = f.schedule_node(base, 3, "2026-01-02T00:00:00Z")
    assert early.stability > base.stability
    # and a grade-1 review before the due time clamps elapsed to 0 and
    # applies the lapse formula at R = 1.0 (deterministic, never a crash).
    lapse_early = f.schedule_node(base, 1, "2026-01-02T00:00:00Z")
    assert lapse_early.stability <= base.stability


# ---------- 6. replay: pure fold, None for empty, order-sensitive ------------------
def test_replay_empty_stream_is_none():
    assert f.replay_reviews([]) is None


def test_replay_is_deterministic_and_order_sensitive():
    a = "2026-01-01T00:00:00Z"
    b = "2026-01-06T00:00:00Z"
    stream = [
        {"grade": 3, "review_time": a},
        {"grade": 4, "review_time": b},
    ]
    r1 = f.replay_reviews(stream)
    r2 = f.replay_reviews([dict(e) for e in stream])
    assert r1 is not None and r2 is not None
    assert r1.to_dict() == r2.to_dict()
    # the same grades in the opposite order are a different history.
    flipped = f.replay_reviews([
        {"grade": 4, "review_time": a},
        {"grade": 3, "review_time": b},
    ])
    assert flipped is not None
    assert flipped.to_dict() != r1.to_dict()


def test_replay_matches_incremental_scheduling():
    a = "2026-01-01T00:00:00Z"
    b = "2026-01-06T00:00:00Z"
    c = "2026-03-26T00:00:00Z"
    stream = [
        {"grade": 3, "review_time": a},
        {"grade": 4, "review_time": b},
        {"grade": 3, "review_time": c},
    ]
    folded = f.replay_reviews(stream)
    step = f.schedule_node(None, 3, a)
    step = f.schedule_node(step, 4, b)
    step = f.schedule_node(step, 3, c)
    assert folded is not None
    assert folded.to_dict() == step.to_dict()


# ---------- 7. MemoryState round-trip ---------------------------------------------
def test_memory_state_dict_round_trip():
    ms = f.MemoryState(5.1, 4.952, 2, "2026-01-06T00:00:00Z", "2026-03-26T00:00:00Z")
    d = ms.to_dict()
    back = f.MemoryState.from_dict(dict(d))
    assert back.to_dict() == d
    assert back.stability == 5.1 and back.difficulty == 4.952
    assert back.reviews == 2
    assert back.last_review == "2026-01-06T00:00:00Z"
    assert back.next_review == "2026-03-26T00:00:00Z"


# ---------- 8. the module never reads the clock ------------------------------------
def test_module_is_clock_free(monkeypatch):
    # The module must not import datetime.now usage on the scheduling path:
    # two calls with identical injected times, made at different real times,
    # must agree byte for byte. (A sleep between calls would catch a hidden
    # clock read at day granularity only if we cross a midnight — instead we
    # assert determinism directly and check no module-level clock call.)
    import time
    s1 = f.schedule_node(None, 3, "2026-01-01T00:00:00Z")
    time.sleep(0.01)
    s2 = f.schedule_node(None, 3, "2026-01-01T00:00:00Z")
    assert s1.to_dict() == s2.to_dict()
    # and the source has no free clock reads (now()/utcnow() calls).
    import inspect
    src = inspect.getsource(f)
    assert "utcnow" not in src
    assert "datetime.now(" not in src
    assert "time.time()" not in src
    assert "time.monotonic" not in src


def test_non_finite_and_out_of_range_inputs_are_rejected():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(f.FSRSValueError):
            f.next_interval(bad)
        with pytest.raises(f.FSRSValueError):
            f.retrievability(1.0, bad)
    with pytest.raises(f.FSRSValueError, match="difficulty-out-of-range"):
        f.next_difficulty(0.0, 3)
    with pytest.raises(f.FSRSValueError, match="retention-not-finite"):
        f.schedule_node(None, 3, "2026-01-01T00:00:00Z", float("nan"))
    with pytest.raises(f.FSRSValueError, match="timezone-missing"):
        f.schedule_node(None, 3, "2026-01-01T00:00:00")
