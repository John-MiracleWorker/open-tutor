# Verifier Contract (normative)

The verifier is the **load-bearing, honesty-enforcing** half of Open Tutor. It is a
**deterministic, non-LLM** pass. It does not trust the generator's self-assessment —
the generator emits *candidates*; the verifier decides *groundedness*.

A node is **grounded** iff **all applicable** checks below pass. Any failure
downgrades the node to `thin` or `unverified` and is **reported** — never silently
passed.

---

## 1. Structural checks (T1)

Per node:

| Check | Failure → |
|---|---|
| node has a non-empty `def` | `structural.error = "missing definition"` |
| every `prereqs` entry exists in the node set | `structural.error += "unknown prereq '<id>'"` |
| the prereq graph is a **DAG** (no cycles) | `structural.errors["__dag__"] = "cycle detected"` |

Cycle detection is an explicit 3-color DFS over `node → prereqs`.

---

## 2. Grounding checks (T2) — the heart of the system

For each node, for each source id in `node.grounding_corpus`:

1. **Fetch** the source's URL through the **extraction ladder**
   ([EXTRACTION-LADDER.md](EXTRACTION-LADDER.md)):
   `trafilatura → html-strip → (browser render, P0.5)`.
2. Record `http_status`, `method`, `text_len` (chars of extracted body),
   `needs_render`, `author`.
3. **Substantive-text rule** (the invariant):

   ```
   MIN_GROUNDING_CHARS = 2500
   a source "covers" the node  ⟺  len(extracted_body) ≥ MIN_GROUNDING_CHARS
                                   AND
                                   any(kw in body for kw in node.covers_keywords)
   ```

4. The node's grounding passes ⟺ **≥1** cited source covers it **and** ≥1 cited
   source returned a non-empty body.

### Why `MIN_GROUNDING_CHARS`?

- HTTP 200 ≠ grounded. A SPA landing page returns 200 with 688 chars of marketing
  copy and *no* concept text. That must not ground a node.
- 2500 chars ≈ a short real article section. Wikipedia concept pages yield 13k–40k;
  Preskill's Phys 229 index yields 5.4k; Nielsen & Chuang's arXiv abstract yields
  1.6k (below the bar — correct, an abstract is not a grounding source for a
  concept; the node is still grounded via its Wikipedia source).
- This is the mechanical form of the core lesson: thin content must not pass.

> **The bar is a floor, not a target.** A source can be well over 2500 chars and
> still not cover the node (keyword miss). Both conditions are required.

### Source status vocabulary

| Status | Meaning |
|---|---|
| `live` | HTTP 200 + extracted body ≥ 200 chars |
| `needs-render` | body returned but < 200 chars; SPA suspected → browser fallback (P0.5) |
| `dead` | 4xx/5xx or no body |

`needs-render` sources **cannot** ground a node in P0. In P0.5 they may be
re-fetched via the browser-render step and promoted to `live` if the rendered body
clears the bar.

---

## 3. Executability checks (T3)

If `node.oracle` is set and the subject declares a T3:

1. Look up `node.oracle` in `oracles.REGISTRY`.
2. **Run it** (deterministic statevector-based Qiskit oracles).
3. Capture the real output to `out/oracle_outputs/<oracle>.json`.
4. Node's exec check passes ⟺ the oracle ran without exception.

If the oracle **fails**, the error is captured and the node is flagged — failed code
is **never** presented as an answer.

Subjects with no T3 (e.g. history) skip this check; they must **not** fake a
runnable key.

---

## 4. Decision

```
grounded    = structural_ok
              AND grounding.ok
              AND (node.oracle is None OR oracle.ok)

else if structural_ok and not grounding.ok  ->  "unverified"
else                                        ->  "thin"
```

The report always includes, per node: `cited_sources`, `live_sources`,
`coverage_hits`, `oracle`, `oracle_ok`, and the final `status`.

---

## 4.5 Runtime answer gate (P1.2)

`verify_answer(answer, spec)` is the runtime form of this contract: the same
deterministic, non-LLM standard applied to a *drafted answer* before it is
served.

| Rule | Failure → |
|---|---|
| draft is non-empty and contains a `## Claim` section | `empty-draft` / ungrounded |
| every [n] inline marker in the claim section indexes a retrieved T2 span in this answer | `invalid-citation` |
| every factual sentence in the claim section carries an inline [n] citation | `missing-citation` |
| the answer carries ≥1 retrieved T2 span | `no-citations` |
| quantitative claims: the T3 oracle ran ok and every numeric token in the claim appears in the oracle result (key labels or values) | `oracle-mismatch` / `quant-mismatch` |

The engine runs the loop **draft → gate → (bounded) retry** (≤ 3 attempts,
`engine.MAX_DRAFT_ATTEMPTS`), feeding the gate's issue list back into the next
draft as feedback. After the last failed attempt the draft is still returned —
explicitly labelled `UNVERIFIED ANSWER` — so an unsupported claim is never
silently emitted as grounded. The gate can only **demote** an answer, never
promote one; it inspects strings and the preflight result only, re-runs
nothing, calls no model, and does not touch `MIN_GROUNDING_CHARS`.

---

## 5. Honesty guarantees

1. **No silent passes.** Every failure is a named status + reason in the report.
2. **Real HTTP codes.** A 404 is reported as 404, never masked as 0.
3. **Extraction is measured, not assumed.** `text_len` is the *extracted* char
   count, not the HTTP content-length.
4. **Reproducible oracles.** Statevector-based (no sampling noise), so captured
   output is stable and inspectable.
5. **Deterministic.** Same inputs → same verdict. No LLM in the loop.

---

## 6. Current invariant (do not weaken in PRs)

```
MIN_GROUNDING_CHARS = 2500
```

A PR that lowers this, or that makes a thin source count as coverage, **fails
review.** The whole value of the system is in this number being strict.
