# Open Tutor — Architecture & Design Spec

**A self-hosted, AI-first learning system for *any* subject, where "grounded" is a
measurable property and the curriculum is *generated + verified* from a topic string.**

> **Current scope:** single-learner, custom Aurora React UI; no
> login/registration/multi-user auth; private-network deployment; automatic
> verified compilation with optional review; registry-only trusted oracles (no
> generated-code execution). See the public [README](README.md),
> [deployment guide](docs/DEPLOYMENT.md), and [adaptive-teaching contract](docs/ADAPTIVE-TEACHING.md).
> Older PoC examples below remain architectural history where explicitly marked.
>
> Original PoC status (historical): design spec + quantum-computing reference subject.
> The PoC exercises the hardest, most load-bearing parts: **source extraction**
> (a real scraper tier, not a fetch), **grounding verification** (live source
> checks with a *substantive-text* rule), **executable oracles** (real Qiskit
> runs), and the **verifier gate** that separates "grounded" from "confident."

---

## 0. Problem & thesis

Open Tutor exists to avoid a common failure mode: AI-generated content without
real instructional oversight. The lesson is not "don't use AI." It is:

> **The trust lives in the verification layer, not the generation layer.**

Every "AI tutor" repo (OpenTutor, Studyield, etc.) treats the LLM as the source
of truth. That is the exact failure mode. A tutor is only trustworthy when:

1. every factual claim is **traceable to a real, live source** — and that source
   actually *contains* the claim (not just returns HTTP 200),
2. every quantitative claim is **reproducible by execution** (the code *is* the
   answer key),
3. every assessment grades against a **verified answer key / rubric**, and
4. every misconception maps to a **structured knowledge node**, so the tutor
   knows *what* the learner got wrong — not just *that* they got it wrong.

This spec makes those four guarantees the **primary** architectural property, not
a RAG afterthought.

---

## 1. Goals / non-goals

### Goals
- **Any-subject:** type a topic → get a *verified* curriculum. Subject-agnostic engine.
- **Grounded by construction:** ungrounded claims are *rejected or flagged*, never silently emitted.
- **Self-hosted / local-first:** operator-configured local models for tutoring; local oracle execution.
- **Honest degradation:** when a subject has no executable layer or thin sources, say so.
- **Optional review:** verified curricula auto-compile; users may request review. Approval never overrides failed verification.

### Scope boundaries
- Multi-user auth, public hosting, cloud inference/sync and generated-code execution remain out of scope.
- The custom Aurora UI, streaming/persistence shell, BKT and FSRS are implemented; EKT is not claimed.

---

## 2. The four grounding tiers

"Ground truth" is a stack, not a single DB.

| Tier | Name | Contents | Job | Quantum example |
|---|---|---|---|---|
| **T1** | Canonical KB | concept DAG: nodes, defs, prereqs, misconceptions | *what is true*, machine-checkable | hand-built node graph + Wikidata refs |
| **T2** | Authoritative corpus | vetted prose, **extracted** (not just fetched) | *where truth is documented* (citation target) | Preskill Phys 229, IBM Quantum Learning, Nielsen & Chuang |
| **T3** | Executable oracle | code / simulator | *truth you can run* — the answer key | Qiskit / qiskit-aer simulator |
| **T4** | Verified assessment | worked solutions, rubrics, answer keys | *how you know you know* | Nielsen–Chuang solutions, generated rubrics |

> Repos in the wild have **zero** of T1/T3 and a weak T2. **That is the gap this spec fills.**

### Tier-agnostic contract
A *subject* declares which tiers it has. A subject with no T3 (e.g. history)
**degrades** to "worked examples + citations" and must **not** fake a runnable
answer key. A subject with thin T2 (obscure/fast-moving) gets nodes flagged
`thin: true` and a **tighter verifier**.

### 2a. The extraction ladder (T2 is not a fetch — it's a pipeline)

A URL that returns HTTP 200 is **not** a grounded source. A SPA landing page can
return 200 with 688 characters of marketing copy and *no* concept text. So T2 has
an internal extraction ladder, and the verifier measures the *extracted* result:

```
  URL ──► 1. trafilatura        (article-scoped text + title/author/date)
        ──► 2. raw-HTML strip    (fallback; flags needs_render when body is thin)
        ──► 3. browser render    (SPA fallback — BrowserOS neo / headless Chromium)
        ──► verdict:  body_len  +  needs_render  +  http_status
```

The verdict is what the grounding check consumes (see §4.2). The ladder exists
because the PoC *proved* a plain fetch is not enough: IBM Quantum Learning is a
client-side SPA, so its server HTML has almost no concept text.

---

## 3. System architecture

```
                      ┌────────────────────────────────────────────┐
   "design a         │              CURRICULUM DESIGNER             │
   curriculum for X" │  (agentic pipeline, one-time per subject)    │
   ───────────────►   │                                              │
                      │  1 SCOPE  2 DRAFT DAG  3 SOURCE FIND          │
                      │  4 ORACLE FIND  5 MISCONCEPTIONS              │
                      │  6 VERIFIER (structural + extraction + exec)  │
                      │  7 HUMAN GATE (approve / edit / compile)      │
                      └───────────────┬──────────────────────────────┘
                                      │ approved CurriculumSpec (YAML)
                                      ▼
                      ┌────────────────────────────────────────────┐
                      │              LEARNER ENGINE (per-subject)   │
                      │  retrieval → oracle run → grounded answer  │
                      │  → verifier gate → misconception/mastery log│
                      └────────────────────────────────────────────┘
```

Two distinct concerns:
- **Curriculum Designer** — one-time, produces a *verified* spec. (This is the PoC.)
- **Learner Engine** — runtime, serves grounded tutoring from the approved spec.

The engine is **subject-agnostic**: it reads `curriculum/<subject>.yaml` and applies
the same retrieval → oracle → verify → log loop. Swapping the YAML = new subject.

---

## 4. Curriculum Designer pipeline

### 4.1 Stages

| # | Stage | Model | Output | Verifier gate |
|---|---|---|---|---|
| 1 | **SCOPE** | cheap, structured | topic, depth, assumed prereqs, learner level | — |
| 2 | **DRAFT DAG** | LLM + retrieval (Wikipedia structure, Wikidata, open syllabi/OCW) | concept nodes + prereq edges + misconceptions | structural: valid DAG? |
| 3 | **SOURCE FIND** | LLM + search | per-node authoritative source | **live + substantive**: URL resolves, extraction yields ≥ MIN_GROUNDING_CHARS, text *covers* node |
| 4 | **ORACLE SELECT** | proposed registry name | trusted, repository-owned executable oracle (or no T3) | registry validation, deterministic execution, captured output |
| 5 | **MISCONCEPTIONS** | retrieval over learning-science lit | node ↔ misconception map | coverage |
| 6 | **VERIFIER** | cheap, deterministic | pass/fail per node, `thin` flags, thin-report | **the honesty gate** |
| 7 | **COMPILE / OPTIONAL REVIEW** | — | auto-compile verified candidate, or optional review/edit | deterministic verification remains mandatory; approval cannot bypass it |

### 4.2 Verifier (step 6) — the load-bearing part

A node is **grounded** iff all applicable checks pass:

- **structural:** node has a def; prereqs exist; DAG has no cycles; coverage vs a
  reference syllabus.
- **grounding (T2):** the node cites ≥1 source whose **extracted** text is
  **substantive** (≥ `MIN_GROUNDING_CHARS`, currently **2500** chars) **and**
  contains the node's coverage keywords. A thin landing page *does not count* —
  that is the whole point of the extraction ladder.
- **executability (T3):** if the node is quantitative and the subject has T3, its
  oracle **runs** and the output is captured.
- **honesty:** any node failing a check is marked `thin: true` / `unverified: true`
  and **reported**, never silently passed.

The verifier is a **separate, cheap, deterministic pass** — it does not trust the
generator's self-assessment.

> **The `MIN_GROUNDING_CHARS` rule is the single most important invariant.** It is
> what converts "this URL is up" into "this source actually documents the concept."
> It is the mechanical form of the core lesson: thin AI content must not pass.

---

## 5. Learner Engine — the grounded tutoring loop

```
student question / attempt
        │
        ▼
 intent + concept-node resolution        (T1: which node(s) does this touch?)
        │
        ▼
 retrieve: T1 node + 2–5 T2 chunks       (grounding context)
 + any executable claim → run T3 oracle  (truth check BEFORE generation)
        │
        ▼
 LLM drafts answer, FORCED to cite retrieved spans
        │
        ▼
 VERIFIER GATE: every claim → has T2 citation OR T3 oracle match?
        │  yes → emit, with inline citations
        │  no  → regenerate or flag "unverified"     ← the honesty gate
        ▼
 log misconception + mastery delta → update T1 learner state
```

**The gate is what makes it a tutor, not a chatbot.** Misconception-triggered
feedback: "student wrong → node `entanglement` misconception #3 → pull Preskill
ch6 chunk + run Bell-state oracle → scaffolded hint."

### 5.1 Mastery & assessment (implemented as BKT + FSRS)
- **Knowledge tracing:** BKT/EKT (or FSRS) over interaction logs for per-node mastery.
- **Spaced repetition:** FSRS scheduler for review cadence.
- **Concept gating:** node N unlocks when prereq mastery crosses threshold.
- **Assessment:** generated quizzes graded against T4 (answer key/rubric), plus
  **teach-back** (learner explains to AI, graded on rubric) — retrieval practice,
  measurable.

---

## 6. Data model

### 6.1 `CurriculumSpec` (YAML) — the "anything" property
```yaml
subject: <slug>
title: <human name>
scope: { depth, level, assumed_prereqs: [] }
tiers: { canonical: t1, corpus: t2, oracle: t3|null, assessment: t4|null }
corpus:
  - id: <slug>
    name: <citation name>
    url: <https://...>
    tier: 2
    status: live|dead|needs-render|unknown   # set by verifier
    http_status: <int>                        # set by verifier
    # extraction metadata (set by verifier via the scraper tier):
    method: trafilatura|html-strip|browser|none
    text_len: <int>                           # chars of extracted body
    needs_render: <bool>                      # thin body → SPA suspected
    author: <str|null>
nodes:
  - id: <slug>
    title: <human>
    def: <definition>
    prereqs: [ids]
    misconceptions: [{ id, text }]
    grounding_corpus: [corpus-ids]            # cited sources
    oracle: <oracle-id|null>                  # T3
    covers_keywords: [str]                    # for the coverage check
    status: grounded|thin|unverified          # set by verifier
    verification:
      structural_ok: bool
      grounding: { cited_sources, live_sources, coverage_hits, ok }
      oracle: { name, ok, output|error }
```

### 6.2 `LearnerState` (per-subject, accumulates)
```yaml
subject: <slug>
nodes:
  - id
    mastery: float            # from knowledge tracing
    attempts: int
    misconceptions_triggered: [ids]
    next_review: iso8601      # FSRS
last_updated: iso8601
```

---

## 7. Groundedness contract (normative)

A lesson/answer is **grounded** ⟺:
1. every factual claim has a T1 or T2 citation *whose extracted text is substantive*;
2. every quantitative claim is reproducible in T3 (if T3 exists for the subject);
3. every assessment item grades against T4;
4. every misconception maps to a T1 node.

Violations are **surfaced** (flagged `thin`/`unverified`), never emitted as
confident fact.

---

## 8. Honesty / degradation matrix

| Condition | Behavior |
|---|---|
| No T3 (no executable layer) | degrade to worked-examples + citations; **never** fake a runnable key |
| Thin T2 (obscure subject) | flag node `thin: true`; verifier tightens; tutor states it's extrapolating |
| **Thin landing page (SPA, ≥ MIN_GROUNDING_CHARS not met)** | source marked `needs-render`; does **not** ground a node; browser fallback attempted (T2b); still thin → node flagged |
| Dead source (404) | drop the citation; node falls to `thin`/`unverified`; reported |
| Oracle fails | capture stderr; node flagged; **do not** present failed code as an answer |

---

## 9. Security & trust
- **Local-first:** sensitive data (transcripts, learner state) stays local.
- **Prompt-injection hygiene:** retrieved web content is *data*, never instructions.
- **Optional review (step 7):** verified candidates auto-compile unless review was requested. Failed or unsafe candidates remain blocked.
- **Deterministic verifier:** a cheap, non-LLM pass decides groundedness.

---

## 10. Build phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 — PoC (this repo)** | Designer stages 1–6 + verifier + **extraction ladder (trafilatura + html-strip)** + live sources + executed Qiskit oracles, for quantum computing | `curriculum/quantum-computing.yaml` with every node live-verified; oracle outputs captured; honest thin-report; `MIN_GROUNDING_CHARS` rule enforced |
| **P0.5 — SPA fallback** | browser-render fallback (BrowserOS neo / headless Chromium) wired into the extraction ladder; `needs_render` sources auto-upgraded | a known-SPA source renders to ≥ MIN_GROUNDING_CHARS and grounds its node |
| **P1 — Engine** | Learner loop: retrieval, oracle run, verifier gate, misconception log | grounded answer with inline citations for sample prompts |
| **P2 — Mastery** | BKT/EKT + FSRS + concept gating | measurable mastery per node |
| **P3 — Aurora UI** | custom typed UI, SQLite persistence, reconnectable progress, private runtime | multi-subject, single-user, Tailscale-only |
| **P4 — Any-subject** | subject-agnostic designer + corpus/oracle adapters (SymPy, music21, rdkit, …) | "design a curriculum for X" works end-to-end |

---

## 11. Why this is different from the existing repos

| | Typical AI-tutor pattern | Open Tutor (this spec) |
|---|---|---|
| LLM role | source of truth | *drafts*; verifier decides |
| Ground truth | weak/none | 4-tier stack, enforced |
| Source check | "URL is up" | "URL up **and** extracted text is substantive **and** covers the node" |
| "Grounded" | a vibe | a testable property (live + substantive + executed) |
| Curriculum | hand-authored or unverified-gen | generated **then independently verified**, optional review |
| Honesty | silent confabulation | thin/unverified nodes **reported** |
| Review | no auditable review path | optional evidence/diff review; no bypass of verification |
