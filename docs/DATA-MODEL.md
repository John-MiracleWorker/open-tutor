# Data Model

Two YAML artifacts, both subject-agnostic. **Swapping the file = a new subject.**

---

## 1. `CurriculumSpec` — the "anything" property

The artifact the **Curriculum Designer** produces (stages 1–6) and the **Learner
Engine** consumes. Defined in `open_tutor/spec.py` (dataclasses) and serialized
by `spec.py:CurriculumSpec.to_yaml()`.

```yaml
subject: quantum-computing          # slug
title: Quantum Computing — foundations to a first algorithm
scope:
  depth: foundations + one algorithm (Grover)
  level: curious non-physicist, math-comfortable
  assumed_prereqs:
    - linear algebra (vectors, complex numbers)
    - probability basics
  goal: a working mental model of qubits, gates, measurement, entanglement,
        and amplitude amplification, with every claim grounded or runnable
tiers:
  canonical: t1                     # T1 — concept DAG
  corpus: t2                        # T2 — extracted authoritative prose
  oracle: t3 (qiskit/qiskit-aer)    # T3 — executable answer key
  assessment: t4 (generated rubrics + teach-back)

corpus:                             # T2 sources
  - id: preskill-ph229
    name: "S. Preskill, Phys 229 — Caltech"
    url: https://preskill.caltech.edu/ph229/
    tier: 2
    status: live                    # live | dead | needs-render | unknown (verifier)
    http_status: 200                # verifier
    method: trafilatura             # verifier (extraction ladder)
    text_len: 5437                  # verifier (extracted chars)
    needs_render: false             # verifier
    author: null                    # verifier (from <meta>)

oracle:
  id: qiskit
  sandbox: python + qiskit + qiskit-aer
  notes: statevector-based for deterministic, reproducible answer keys

nodes:
  - id: qubit
    title: The Qubit
    def: A two-level quantum system. The state is a unit vector in a 2-D complex
         Hilbert space, with |0> and |1> as the computational basis.
    prereqs: []
    misconceptions:
      - id: qubit-m1
        text: A qubit is just a bit that is either 0 or 1 that we don't know which.
    grounding_corpus: [wiki-qubit, nielsen-chuang, ibm-quantum-learning]
    oracle: qubit_state             # T3 id or null
    covers_keywords: [qubit]        # for the coverage check
    status: grounded                # grounded | thin | unverified (verifier)
    verification:                   # populated by the verifier
      structural_ok: true
      grounding:
        cited_sources: [wiki-qubit, nielsen-chuang, ibm-quantum-learning]
        live_sources: [wiki-qubit, nielsen-chuang]
        coverage_hits: [wiki-qubit]
        ok: true
      oracle:
        name: qubit_state
        ok: true
        output: out/oracle_outputs/qubit_state.json
```

### Field ownership

| Field | Written by |
|---|---|
| `subject`, `title`, `scope`, `tiers`, `corpus[].url`, `nodes[].def/prereqs/misconceptions/grounding_corpus/oracle/covers_keywords` | **Generator** (Designer stages 1–5) |
| `corpus[].status/http_status/method/text_len/needs_render/author` | **Verifier** (extraction ladder) |
| `nodes[].status`, `nodes[].verification.*` | **Verifier** (the honesty gate) |

The generator **deliberately does not self-declare** groundedness — that is the
verifier's job. This separation is what keeps the system honest.

---

## 2. `LearnerState` — per-subject, accumulates (P2)

**Implemented (P2.1)** in `open_tutor/learner_state.py` — serialized per subject
(next to the spec by convention, `out/curriculum/<subject>.learnerstate.json`),
written atomically, and loaded with subject/schema-version/spec compatibility
enforced. The durable event records are the source of truth; the per-node
projections below are derived from them.

The documented knowledge-tracing choice is **BKT** (Bayesian Knowledge
Tracing, fixed parameters → deterministic, bounded in `[0, 1]`, no learning
step). EKT was rejected for its neural-network learning step (conflicts with
the deterministic/non-LLM invariant); **FSRS scheduling is deliberately kept
separate for P2.2**, so `next_review` stays `null` until then. An unverified
response is not a learning signal: it contributes no mastery either way.

```yaml
subject: quantum-computing
nodes:
  - id: qubit
    mastery: 0.42                   # from knowledge tracing (BKT — see note)
    attempts: 7
    misconceptions_triggered: [qubit-m1]
    next_review: null               # FSRS — P2.2, deliberately not set yet
last_updated: 2026-09-02T14:00:00Z
```

Mastery drives **concept gating**: node N unlocks when prereq mastery crosses the
threshold (P2.2). `misconceptions_triggered` feeds the tutor's scaffolded hints.

---

## 3. Invariants

- **`def` is mandatory** on every node (structural check).
- **`prereqs` must reference existing nodes** and form a **DAG**.
- **`grounding_corpus` entries must be ids present in `corpus`** (coverage check).
- **`oracle` must be a key in `oracles.REGISTRY`** (exec check) or null.
- The spec round-trips: `CurriculumSpec.from_dict(spec.to_dict())` == spec.
