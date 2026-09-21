# Engine and assessment contract

This document describes the owned runtime subsystem. It is intentionally
extractive by default: a model may draft text, but only the deterministic gate
in `open_tutor.verifier` can certify it.

## Local completion API

The reusable provider boundary is available to the curriculum designer and the
learner engine:

```python
from open_tutor.llm import local_completion, LocalCompletion

text = local_completion(
    messages=[{"role": "user", "content": "..."}],
    base_url="http://127.0.0.1:8081",
    model="local-model",
    timeout=20.0,
    max_tokens=800,
)

provider = LocalCompletion("http://127.0.0.1:8081", "local-model")
text = provider(messages)
```

The endpoint is OpenAI-compatible (`POST /v1/chat/completions`) and uses
deterministic temperature `0`. The client accepts only `localhost`, IP literals
in private/loopback/link-local/Tailscale ranges, and HTTP(S); public hostnames,
credentials in URLs, and non-HTTP schemes are rejected before network I/O.
Transport and malformed-response failures raise `LocalCompletionError`. The
client does not return or trust a `grounded` flag.

## Tutor API

```python
answer = tutor(
    spec,
    question,
    corpus_text={"source-id": extracted_text},  # optional offline injection
    cache_dir="out/cache",                     # used when corpus_text is absent
    mode="extractive",                         # default; or "local-model"
    draft_fn=None,                              # optional test/provider seam
    selected_node=None,                        # node id or Node
    followup_context=None,                     # data passed to a draft only
)
```

`selected_node` pins resolution to a validated T1 node. It does not change the
question or oracle: a quantitative follow-up always runs the selected node's
own registered oracle. `followup_context` is context data and is never treated
as a node id or executable instruction. A no-node question returns before any
draft/model call.

The default extraction path retrieves at most five citation spans, requires at
least two, preserves source id and character offsets, and requires at least
2,500 extracted characters across the cited runtime corpus. A missing or thin
cache produces a named `thin-retrieval` status. Curriculum groundedness still
uses the verifier's per-source `MIN_GROUNDING_CHARS = 2500` invariant.

Answer statuses include `grounded`, `unverified`, `no-node`, `thin-retrieval`,
`oracle-failed`, `oracle-unavailable`, and `llm-error`. A failed deterministic
gate demotes an otherwise usable answer to `status="unverified"`; the boolean
`grounded` is never left true when verification failed. Model generation is
bounded by `MAX_DRAFT_ATTEMPTS` (currently three). Local provider failures are
returned as `llm-error` data rather than escaping through a shell/API worker.

## Deterministic answer gate

`verify_answer(answer_like, spec)` accepts an object with `draft`,
`quantitative`, `citations`, and `oracle` fields. It requires:

1. a non-empty `## Claim` section;
2. an inline citation marker on every claim line;
3. every marker to index a citation in this answer;
4. the claim text to be an exact quoted/unquoted excerpt from a cited T2 span,
   or an exact excerpt from a T1 node definition; and
5. for numeric claims, an explicit oracle path/value expression whose path and
   value agree (for example `P(|0>) = 0.5`).

Citation markers alone are not evidence. Semantic paraphrase, unrelated
citations, negation, swapped oracle values, and invented numbers are reported as
`unsupported-claim` or `quant-mismatch`. The gate performs no model call, no
oracle rerun, and never changes `MIN_GROUNDING_CHARS`.

## Assessment API

`assessment.make_item`, `validate_item`, `build_answer_key`, `grade`, and
`assess` form a closed, versioned T4 boundary. Quiz and worked keys bind to the
actual node oracle output; factual keys bind to corpus ids listed by that node.
Dead/needs-render sources, unknown sources, cross-node oracle bindings, missing
nodes, malformed item shapes, and absent keys are named failures. A
quantitative item without a usable node-bound T3 key is `degraded`, never a
fabricated numeric answer.

Teach-back scoring is deterministic over an item-owned rubric. An unverified
attempt cannot award a grade, hedged/ambiguous rubric overlap is flagged, and
misconception candidates are accepted only when they are high-overlap validated
references on the item's node. Asking a question or providing an LLM self-score
does not award mastery; learner-state updates consume the engine's gate verdict.

## Corpus report/cache contract

`verifier.verify` includes the measured extracted body in each `report["corpus"]`
entry under `text`, alongside HTTP status, extraction method, length, render
flag, and error. `pipeline._write_corpus_cache` writes this field to
`out/cache/<source-id>.txt`; the learner engine reads that cache locally and
does not fetch the network.

## Safety boundaries

Retrieved text is marked as source data in model prompts, quoted as evidence in
extractive drafts, and is never parsed as Python, a tool call, or an instruction.
Oracles are resolved only through the trusted registry. No generated code is
executed, no learner data is uploaded, and no model-generated groundedness or
mastery claim is authoritative.
