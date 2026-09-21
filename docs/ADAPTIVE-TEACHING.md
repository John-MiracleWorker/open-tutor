# Adaptive teaching contract

Adaptive teaching is model-led but host-bounded. It may vary explanation style and choose one activity, but it cannot certify facts, award mastery, change prerequisite locks, or override the deterministic evidence gate.

## Model contract

A teaching turn must be one JSON object with:

- a short title and explanatory prose;
- zero or more bounded steps;
- an optional bounded diagram;
- exactly one learner activity with one deliverable;
- evidence references limited to the source excerpts supplied to that call.

The host rejects unsupported fields, multiple questions, compound activities, malformed diagrams, untrusted references, empty prose, or truncation. It permits at most one schema-repair attempt; transport failures and truncated outputs are not retried as schema repairs.

## Evidence and mastery

A ready teaching turn can cite only the bounded source-evidence set that passed the deterministic gate. AI prose is still labelled independently from source groundedness. Asking, reading, or receiving a teaching turn does not change mastery or review scheduling. Only validated server-issued practice can do that.

If retrieval, verification, or the provider fails, Open Tutor returns an explicit blocked/fallback result and preserves the learner's pending activity whenever the engine cannot prove that the concept changed. It does not fabricate a replacement question or silently mark the turn complete.

## Limits

Teaching has bounded request/output budgets and finite transport deadlines. Exact values are implementation details, not a promise of universal latency or pedagogical accuracy. Evaluate each provider with full persisted teaching flows—including a follow-up, a worked example, a hint, and a restart readback—before treating it as production-ready.
