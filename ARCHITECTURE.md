# ARCHITECTURE.md

Deep-dive architecture for Open Tutor. The spec ([SPEC.md](SPEC.md)) states *what*
and *why*; this file states *how the pieces fit together* and the exact
boundaries between them.

---

## 1. The two engines, one artifact

```
┌──────────────────────────┐        ┌──────────────────────────┐
│   CURRICULUM DESIGNER     │  ───► │     LEARNER ENGINE         │
│   (one-time per subject)  │  YAML │     (runtime, per learner) │
│                          │        │                            │
│  SCOPE → DRAFT DAG →      │        │  intent → resolve node →    │
│  SOURCE FIND → ORACLE     │        │  retrieve T1/T2 + run T3 →  │
│  FIND → MISCONCEPTIONS →  │        │  draft (LLM) → verify gate →│
│  VERIFY → OPTIONAL REVIEW      │        │  log misconception/mastery  │
└──────────────────────────┘        └──────────────────────────┘
       Implemented                     Implemented (P1/P2)
```

The **only** contract between them is the `CurriculumSpec` YAML. The engine never
sees the generator; the generator never sees the engine. That boundary is what
makes the system subject-agnostic and what lets the engine be swapped for a forked
UI without touching the grounding logic.

## 2. Module map

```
open_tutor/
├── spec.py        data model + YAML (de)serialization. Pure. No I/O.
├── generator.py   Designer stages 1–5 for the quantum reference subject.
│                  Emits candidates. Does NOT self-declare groundedness.
├── oracles.py     T3. Pure functions returning computed dicts.
│                  Deterministic (Statevector) so output is reproducible.
├── scraper.py     T2 extraction ladder. trafilatura → html-strip → needs_render.
├── verifier.py    THE load-bearing gate. structural + grounding + exec.
│                  Deterministic, non-LLM. Owns MIN_GROUNDING_CHARS.
├── pipeline.py    Orchestration: design → verify → persist artifacts.
└── cli.py         Entry point: python -m open_tutor.cli "<topic>"
```

**Dependency direction (acyclic):**

```
cli → pipeline → generator → spec
             → verifier → scraper, oracles, spec
```

`spec.py` is the leaf. `verifier.py` never imports `pipeline`. `oracles.py`
imports only qiskit/numpy. No cycles.

## 3. The grounding decision (the crux)

A node's final status is computed **only** in `verifier.py:verify()`:

```
grounded = structural_ok
          AND grounding.ok            # ≥1 cited source: body ≥ 2500 chars AND kw hit
          AND (oracle is None OR oracle.ok)
```

Three independent, deterministic inputs. The generator's opinion is not one of
them. This is the entire architectural point — and it is why the system can be
trusted where a model-only tutor cannot be.

## 4. Why deterministic oracles

Quantum oracles use `qiskit.quantum_info.Statevector`, not the Aer *simulator*.
Reason: a simulator samples — output varies run-to-run. A statevector computes the
exact amplitudes. For an **answer key**, reproducibility is non-negotiable: the
learner (and the verifier) must see the same number every time. Sampling noise
would make a grounded answer look flaky.

## 5. Why the verifier is not an LLM

- **Determinism:** same inputs → same verdict. Testable.
- **Cost:** a cheap pass, not a model call, per node per run.
- **Trust:** an LLM that decides "is this grounded?" would be the LLM trusting the
  LLM — the exact failure mode we exist to prevent. The verifier is code, not a
  model.

## 6. Failure surfaces (all first-class)

| Failure | Where surfaced |
|---|---|
| Dead URL (4xx/5xx) | `corpus[].status = "dead"`, real HTTP code in report |
| Thin SPA shell | `corpus[].status = "needs-render"` + `needs_render: true` |
| Source thin but live | still `live` but fails `MIN_GROUNDING_CHARS` → node not grounded via it |
| Oracle exception | `nodes[].verification.oracle.error`, node flagged |
| DAG cycle / missing prereq | `report.structural.errors`, node flagged `thin`/`unverified` |
| No covering source | `nodes[].verification.grounding.ok = false`, node `unverified` |

None of these are exceptions that crash the run — they are **data** in the report.
That is the honesty guarantee.

## 7. Extension points (for P4 any-subject)

| Adapter | Replaces | Example |
|---|---|---|
| `generator.<subject>()` | `generator.py` quantum data | chemistry, music theory, law |
| `oracles.REGISTRY` | `oracles.py` Qiskit oracles | SymPy (math), music21 (theory), rdkit (chem) |
| `scraper.extract` | `scraper.py` trafilatura | API-backed sources (Wikidata, Project Gutenberg) |
| `verifier.verify` | unchanged | the contract stays; only the inputs change |

The verifier is the **stable core**. Everything else is a per-subject adapter that
feeds it. That is the "any subject" property, made concrete.

## 8. Security model

- **Local-first:** no cloud in the PoC. Transcripts + learner state stay on disk.
- **Retrieved content is data:** the scraper's output is never parsed as
  instructions. Prompt-injection from a web page cannot drive the pipeline.
- **Oracle sandboxing:** T3 runs in-process under Qiskit. For untrusted
  user-submitted oracles (P4), run in a subprocess/container with no network.
- **Human gate (stage 7):** generated → approved → compiled. Non-negotiable.
