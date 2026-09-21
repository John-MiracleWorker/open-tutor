"""Deterministic FSRS v4 scheduling.

This module implements the published FSRS v4 equations with the published
fixed default weights.  It intentionally does not include the optimizer: a
review event is folded by a pure function of the previous memory state, grade,
and caller-supplied review timestamp.

The reference is the ``FSRS v4`` section of the Open Spaced Repetition
``The Algorithm`` specification.  FSRS-4.5 changes the default parameters and
the forgetting curve; this project pins v4 so persisted schedules remain
stable.  The scheduler rounds the solved interval to the nearest whole day,
matching the reference scheduler, and stores due dates at midnight UTC.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Published FSRS v4 defaults, w0 .. w16.
FSRS_VERSION = "4"
FSRS_WEIGHTS: tuple[float, ...] = (
    0.4, 0.6, 2.4, 5.8, 4.93, 0.94, 0.86, 0.01, 1.49,
    0.14, 0.94, 2.18, 0.05, 0.34, 1.26, 0.29, 2.61,
)
W = FSRS_WEIGHTS

DEFAULT_RETENTION = 0.9
GRADE_AGAIN = 1
GRADE_HARD = 2
GRADE_GOOD = 3
GRADE_EASY = 4
VALID_GRADES = (GRADE_AGAIN, GRADE_HARD, GRADE_GOOD, GRADE_EASY)

MIN_STABILITY = 0.01
MAX_STABILITY = 36500.0
MIN_DIFFICULTY = 1.0
MAX_DIFFICULTY = 10.0
MAX_INTERVAL = 36500
SECONDS_PER_DAY = 86400.0


class FSRSValueError(ValueError):
    """A named validation failure in the scheduler input or memory state."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FSRSValueError(f"{name}-not-number: {value!r}")
    try:
        result = float(value)
    except (OverflowError, TypeError) as exc:
        raise FSRSValueError(f"{name}-not-finite: {value!r}") from exc
    if not math.isfinite(result):
        raise FSRSValueError(f"{name}-not-finite: {value!r}")
    return result


def _grade(grade: Any) -> int:
    if isinstance(grade, bool) or not isinstance(grade, int) or grade not in VALID_GRADES:
        raise FSRSValueError(f"grade-unknown: {grade!r} (known: {list(VALID_GRADES)})")
    return grade


def _bounded(value: Any, name: str, low: float, high: float) -> float:
    result = _finite(value, name)
    if not low <= result <= high:
        raise FSRSValueError(f"{name}-out-of-range: {result!r} not in [{low}, {high}]")
    return result


def _parse_timestamp(value: Any, name: str = "review-time"):
    """Parse an aware ISO-8601 timestamp and return it in UTC.

    The scheduler never calls a clock.  Requiring an offset avoids silently
    interpreting a caller's local time differently after a restart.
    """
    from datetime import datetime, timezone

    if not isinstance(value, str) or not value.strip():
        raise FSRSValueError(f"{name}-missing: {name} is required")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise FSRSValueError(f"{name}-invalid: {value!r} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FSRSValueError(f"{name}-timezone-missing: an aware UTC timestamp is required")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class MemoryState:
    """The persisted FSRS memory state for one concept."""

    stability: float
    difficulty: float
    reviews: int
    last_review: str
    next_review: str

    def __post_init__(self) -> None:
        _bounded(self.stability, "stability", MIN_STABILITY, MAX_STABILITY)
        _bounded(self.difficulty, "difficulty", MIN_DIFFICULTY, MAX_DIFFICULTY)
        if isinstance(self.reviews, bool) or not isinstance(self.reviews, int) or self.reviews < 1:
            raise FSRSValueError(f"reviews-out-of-range: {self.reviews!r} must be a positive int")
        _parse_timestamp(self.last_review, "last-review")
        _parse_timestamp(self.next_review, "next-review")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stability": self.stability,
            "difficulty": self.difficulty,
            "reviews": self.reviews,
            "last_review": self.last_review,
            "next_review": self.next_review,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MemoryState:
        if not isinstance(d, dict):
            raise FSRSValueError("memory-state-not-mapping")
        try:
            return cls(
                stability=d["stability"], difficulty=d["difficulty"],
                reviews=d["reviews"], last_review=d["last_review"],
                next_review=d["next_review"],
            )
        except KeyError as exc:
            raise FSRSValueError(f"memory-state-field-missing: {exc.args[0]}") from exc


def initial_difficulty(grade: int) -> float:
    """FSRS v4 ``D0(G) = w4 - (G - 3) * w5``."""
    g = _grade(grade)
    return max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, W[4] - (g - 3) * W[5]))


def initial_stability(grade: int) -> float:
    """FSRS v4 ``S0(G) = w[G - 1]``."""
    g = _grade(grade)
    return max(MIN_STABILITY, min(MAX_STABILITY, W[g - 1]))


def next_difficulty(difficulty: float, grade: int) -> float:
    """Apply FSRS v4's difficulty update and mean reversion."""
    d = _bounded(difficulty, "difficulty", MIN_DIFFICULTY, MAX_DIFFICULTY)
    g = _grade(grade)
    updated = W[7] * initial_difficulty(GRADE_GOOD) + (1.0 - W[7]) * (
        d - W[6] * (g - 3))
    return max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, updated))


def retrievability(elapsed_days: float, stability: float) -> float:
    """FSRS v4 forgetting curve ``R(t,S) = (1 + t/(9S)) ** -1``."""
    elapsed = _finite(elapsed_days, "elapsed-days")
    s = _bounded(stability, "stability", MIN_STABILITY, MAX_STABILITY)
    if elapsed < 0:
        elapsed = 0.0
    result = (1.0 + elapsed / (9.0 * s)) ** -1
    if not math.isfinite(result):  # defensive: never return a non-finite state
        raise FSRSValueError("retrievability-not-finite")
    return result


def next_interval(stability: float, retention: float = DEFAULT_RETENTION) -> float:
    """Solve the FSRS v4 curve for the requested retention.

    This pure helper returns the real-valued interval.  ``schedule_node``
    applies the reference scheduler's whole-day rounding and bounds.
    """
    s = _bounded(stability, "stability", MIN_STABILITY, MAX_STABILITY)
    r = _finite(retention, "retention")
    if not 0.0 < r < 1.0:
        raise FSRSValueError(f"retention-out-of-range: {retention!r} must be in (0, 1)")
    result = 9.0 * s * (1.0 / r - 1.0)
    if not math.isfinite(result) or result <= 0:
        raise FSRSValueError(f"interval-invalid: {result!r}")
    return result


def stability_after_recall(stability: float, difficulty: float,
                           retrievability: float, grade: int) -> float:
    """FSRS v4 stability update after Hard, Good, or Easy."""
    s = _bounded(stability, "stability", MIN_STABILITY, MAX_STABILITY)
    d = _bounded(difficulty, "difficulty", MIN_DIFFICULTY, MAX_DIFFICULTY)
    r = _bounded(retrievability, "retrievability", 0.0, 1.0)
    g = _grade(grade)
    if g == GRADE_AGAIN:
        raise FSRSValueError("grade-not-recall: grade 1 is a lapse")
    d_new = next_difficulty(d, g)
    hard_penalty = W[15] if g == GRADE_HARD else 1.0
    easy_bonus = W[16] if g == GRADE_EASY else 1.0
    increase = (
        math.exp(W[8]) * (11.0 - d_new) * s ** (-W[9])
        * (math.exp(W[10] * (1.0 - r)) - 1.0)
        * hard_penalty * easy_bonus
    )
    result = s * (1.0 + increase)
    if not math.isfinite(result):
        raise FSRSValueError("stability-not-finite")
    return max(MIN_STABILITY, min(MAX_STABILITY, result))


def stability_after_lapse(stability: float, difficulty: float,
                          retrievability: float) -> float:
    """FSRS v4 post-lapse stability update.

    The published equation uses the pre-review difficulty ``D``.  It does not
    use ``D'`` from the successful-recall difficulty transition.
    """
    s = _bounded(stability, "stability", MIN_STABILITY, MAX_STABILITY)
    d = _bounded(difficulty, "difficulty", MIN_DIFFICULTY, MAX_DIFFICULTY)
    r = _bounded(retrievability, "retrievability", 0.0, 1.0)
    result = (
        W[11] * d ** (-W[12]) * ((s + 1.0) ** W[13] - 1.0)
        * math.exp(W[14] * (1.0 - r))
    )
    if not math.isfinite(result):
        raise FSRSValueError("stability-not-finite")
    return max(MIN_STABILITY, min(MAX_STABILITY, result))


def grade_update(state: MemoryState | None, grade: int,
                 elapsed_days: float,
                 retention: float = DEFAULT_RETENTION) -> tuple[float, float]:
    """Return ``(stability, difficulty)`` after one review."""
    g = _grade(grade)
    _finite(elapsed_days, "elapsed-days")
    # Validate retention on every path, including a first review.
    next_interval(initial_stability(g), retention)
    if state is None:
        return initial_stability(g), initial_difficulty(g)
    elapsed = max(0.0, float(elapsed_days))
    r = retrievability(elapsed, state.stability)
    if g == GRADE_AGAIN:
        s = stability_after_lapse(state.stability, state.difficulty, r)
    else:
        s = stability_after_recall(state.stability, state.difficulty, r, g)
    return s, next_difficulty(state.difficulty, g)


def _days_between(t0_iso: str, t1_iso: str) -> float:
    return (_parse_timestamp(t1_iso, "review-time")
            - _parse_timestamp(t0_iso, "last-review")).total_seconds() / SECONDS_PER_DAY


def _round_interval(interval_days: float) -> int:
    interval = _finite(interval_days, "interval-days")
    if interval <= 0:
        raise FSRSValueError(f"interval-invalid: {interval!r}")
    # JS Math.round semantics used by the reference scheduler, without
    # Python's banker's-rounding behavior at x.5.
    return max(1, min(MAX_INTERVAL, math.floor(interval + 0.5)))


def _due_at(last_review_iso: str, interval_days: float) -> str:
    from datetime import timedelta

    reviewed = _parse_timestamp(last_review_iso, "review-time")
    due = reviewed.replace(hour=0, minute=0, second=0, microsecond=0)
    due += timedelta(days=_round_interval(interval_days))
    return due.strftime("%Y-%m-%dT%H:%M:%SZ")


def schedule_node(state: MemoryState | None, grade: int, reviewed_at_iso: str,
                  retention: float = DEFAULT_RETENTION) -> MemoryState:
    """Apply one review at an injected timestamp and return its new memory state."""
    g = _grade(grade)
    _parse_timestamp(reviewed_at_iso, "review-time")
    # Explicitly validate retention before either initial or recurrent math.
    next_interval(initial_stability(g), retention)
    if state is None:
        stability, difficulty = initial_stability(g), initial_difficulty(g)
    else:
        elapsed = _days_between(state.last_review, reviewed_at_iso)
        stability, difficulty = grade_update(state, g, elapsed, retention)
    due = _due_at(reviewed_at_iso, next_interval(stability, retention))
    return MemoryState(
        stability=round(stability, 6), difficulty=round(difficulty, 6),
        reviews=(state.reviews if state is not None else 0) + 1,
        last_review=reviewed_at_iso.strip(), next_review=due,
    )


def replay_reviews(review_events: list[dict[str, Any]],
                   retention: float = DEFAULT_RETENTION) -> MemoryState | None:
    """Purely fold an ordered, validated review-event projection."""
    if not isinstance(review_events, list):
        raise FSRSValueError("reviews-not-list")
    state: MemoryState | None = None
    for event in review_events:
        if not isinstance(event, dict):
            raise FSRSValueError("review-not-mapping")
        if "grade" not in event:
            raise FSRSValueError("grade-missing: review grade is required")
        if "review_time" not in event:
            raise FSRSValueError("review-time-missing: review_time is required")
        state = schedule_node(state, event["grade"], event["review_time"], retention)
    return state
