# Mastery and review contract

This document defines the P2 learner-state boundary. Learner data is local JSON;
the durable event list is the source of truth and all projections are rebuilt
deterministically from it.

## Evidence boundary

A grounded tutor answer is not an assessment. `learner_events.events_from_answer`
therefore records the interaction and any validated misconception references, but
it emits neither a mastery event nor an FSRS review event. This prevents a useful
explanation from being mistaken for evidence that the learner can retrieve the
concept.

`learner_events.events_from_assessment` is the only normal event factory that can
produce a mastery signal. It validates the T4 item and calls the deterministic
`assessment.grade` function itself. The resulting `assessment` event stores the
item id, verdict, score, and a SHA-256 fingerprint of the learner response. A
response that is not gate-certified is demoted to `flagged`, even if a rubric
would otherwise match it. The durable tracer accepts only these outcomes:

| T4 verdict | BKT signal | FSRS grade |
| --- | --- | --- |
| `correct` | correct | 3 (`good`) |
| `partial` | no mastery signal | 2 (`hard`) |
| `incorrect` | incorrect | 1 (`again`) |
| `flagged`, `degraded`, `not-scored` | none | no review |

A validated misconception makes a scored assessment `hard` for scheduling. A
misconception from a tutor interaction also places the node in the immediate
review queue, but never increases mastery.

The old `mastery_delta` shape remains readable as a zero-valued audit shape for
P1 compatibility. Non-zero deltas are rejected because they have no verifiable
assessment provenance. P1.3 state migration explicitly neutralizes old non-zero
deltas rather than treating historical tutor groundedness as mastery.

## BKT projection

The tracer is fixed-parameter Bayesian Knowledge Tracing:

```text
P(L | correct)   = (1 - slip) P(L) /
                   ((1 - slip) P(L) + guess (1 - P(L)))
P(L | incorrect) = slip P(L) /
                   (slip P(L) + (1 - guess) (1 - P(L)))
P(L_next)        = P(L | observation) + (1 - P(L | observation)) learn
```

Open Tutor pins `P(L0)=0`, `learn=0.2`, `slip=0.1`, and `guess=0.2`. Scores are
bounded to `[0, 1]`; unknown, partial, and unsupported evidence does not move
the score. The choice is deterministic, dependency-free, and has no model
training step.

## FSRS v4 projection

`open_tutor.fsrs` pins the published FSRS v4 weights and equations from the
[Open Spaced Repetition FSRS algorithm specification](https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm/e6ded59fa6d1d6bb2950a759d53b14575e9e586c).
FSRS-4.5 is not silently substituted: it has different default weights and a
different forgetting curve. No optimizer or third-party scheduler is needed.

The memory state is:

```text
stability   positive days at which retrievability is 90%
difficulty  [1, 10]
reviews     positive count
last_review injected aware ISO-8601 timestamp
next_review UTC midnight due date
```

FSRS v4 uses grades `1=again`, `2=hard`, `3=good`, `4=easy`, the published
forgetting curve `R=(1+t/(9S))^-1`, and interval `I=9S(1/r-1)`. The scheduler
rounds the interval using reference JavaScript `Math.round` semantics, bounds it
to at least one day and at most 36,500 days, and stores the due date at midnight
UTC. Reviews before the previous review timestamp are treated as elapsed zero;
an early review is not accidentally converted into a lapse.

All numeric inputs reject booleans, NaN, infinity, and out-of-range values with a
named `FSRSValueError`. Timestamps must be aware ISO-8601 values. The module
never reads a clock.

## Durable state and replay

State is stored at `<subject>.learnerstate.json`, normally under
`out/curriculum/`. `LearnerStateStore.record` and `record_events` perform:

1. load the latest complete file under a per-path process lock and an exclusive
   `<path>.lock` file lock;
2. validate node, event shape, subject, schema, and derived event id;
3. deduplicate by canonical SHA-256 event id;
4. replay BKT and FSRS from the event list;
5. write a flushed temporary file in the same directory and replace the target
   atomically.

Rejected events are persisted in the rejection audit. Duplicate-only replays do
not rewrite timestamps or the target file. Load rejects corrupt JSON, future
versions, invalid events, invalid schedules, unknown nodes, and projections that
do not equal deterministic replay. The state file never contains secrets or
remote learner data.

## Public worker APIs

```python
gate_report(spec, state, threshold=0.7)
# {node_id: {"unlocked": bool, "blocking_prereqs": [node_id, ...]}}

due_nodes(spec, state, now=None)
# [node_id, ...]

process_assessment(item, response, spec, store, now="")
# {"events": [...], "results": [...], "node_id": str|None,
#  "mastery": float|None, "next_review": str|None}
```

`gate_report` uses traced mastery and treats missing prerequisite evidence as
zero. `due_nodes` includes schedules whose due time is at or before injected
`now`, plus validated misconception-triggered review requests. With `now=None`
it performs no clock read and returns all scheduled nodes plus misconception
requests. Callers that need a time-filtered queue should always pass `now`.
