"""P1.1 — Learner Engine: contracts, node resolution, retrieval, oracle preflight.

Deterministic and offline (no network, no LLM). The corpus text is injected as
synthetic data and the T3 oracle is either a real local (Statevector) run or a
monkeypatched stub, so every test is reproducible on a machine without qiskit.

Covered, per the card:
  - node resolution (T1)            -> correct node / honest no-node / deterministic
  - retrieval bounds (T2)           -> 2-5 chunks, thin flag, missing sources, cap
  - quantitative detection + oracle preflight (T3) -> required / ok / failed / n/a
  - explicit grounded context/citation objects
  - honest failure states           -> no-node, thin-retrieval, oracle-failed,
                                       oracle-unavailable
  - the grounded end-to-end contract  -> grounded answer w/ citations + oracle
"""
from __future__ import annotations

import pytest

from open_tutor import engine
from open_tutor.spec import CorpusSource, CurriculumSpec, Node

# ---------- synthetic corpus text (data, not instructions) --------------------
# Several 120+-char paragraphs, each mentioning the node keyword, so the engine
# has enough spans to satisfy the 2-chunk floor and exceed it for the cap test.
QUBIT_TEXT = "\n\n".join(
    f"A qubit is a two-level quantum system. A qubit is the fundamental two-level quantum unit, the quantum analogue of "
    f"the classical bit, realised by a pair of basis states labelled zero and one. "
    f"Paragraph {i} develops the qubit with further context so the span stays long."
    for i in range(8)
)  # 8 paragraphs, each well over 120 chars, keyword 'qubit' throughout

THIN_TEXT = "A qubit is a two-level quantum system. It has two basis states."
# only ~65 chars -> yields at most one chunk, below the 2-chunk floor


# ---------- fixtures ----------------------------------------------------------
def _node(spec: CurriculumSpec, nid: str = "qubit") -> Node:
    n = spec.node(nid)
    assert n is not None
    return n


def make_spec(oracle: str | None = "fake_qubit", corpus_ids=("src-a", "src-b"),
              defn: str = "A qubit is a two-level quantum system with basis states.") -> CurriculumSpec:
    corpus = [
        CorpusSource(id="src-a", name="Source A", url="https://a.example/qubit"),
        CorpusSource(id="src-b", name="Source B", url="https://b.example/qubit"),
    ]
    node = Node(
        id="qubit", title="The Qubit", defn=defn,
        covers_keywords=["qubit"],
        grounding_corpus=list(corpus_ids),
        oracle=oracle,
    )
    return CurriculumSpec(subject="test", title="Test", corpus=corpus, nodes=[node])


def make_corpus_text(oracle_text: str = QUBIT_TEXT) -> dict:
    return {"src-a": oracle_text, "src-b": oracle_text}


@pytest.fixture
def good_oracle(monkeypatch):
    monkeypatch.setattr(
        engine, "run_oracle",
        lambda name: {"ok": True, "result": {"P(|0>)": 0.5, "P(|1>)": 0.5,
                                             "normalized": True}})


# ---------- 1. node resolution (T1) ------------------------------------------
def test_resolve_canonical_question_to_node():
    spec = make_spec()
    r = engine.resolve_node("What is a qubit?", spec)
    assert r.resolved is True
    assert r.node_id == "qubit"
    assert r.score >= engine.MATCH_THRESHOLD


def test_resolve_is_deterministic():
    spec = make_spec()
    a = engine.resolve_node("What is a qubit?", spec)
    b = engine.resolve_node("What is a qubit?", spec)
    assert (a.node_id, a.score, a.candidates) == (b.node_id, b.score, b.candidates)


def test_no_node_for_off_subject_question():
    spec = make_spec()
    r = engine.resolve_node("What is photosynthesis?", spec)
    assert r.resolved is False
    assert r.node_id is None
    # honest: the closest candidate is still surfaced, never invented away
    assert any(c["id"] == "qubit" for c in r.candidates)


# ---------- 2. retrieval bounds (T2) -----------------------------------------
def test_retrieval_satisfies_two_to_five_chunks():
    spec = make_spec()
    ret = engine.retrieve(spec, _node(spec), "What is a qubit?",
                          make_corpus_text())
    assert ret.satisfied is True
    assert ret.thin is False
    assert 2 <= ret.chunk_count <= 5


def test_retrieval_never_exceeds_max_chunks():
    spec = make_spec()
    # QUBIT_TEXT has 8 paragraphs -> more than 5 chunks available
    ret = engine.retrieve(spec, _node(spec), "What is a qubit?",
                          make_corpus_text())
    assert ret.chunk_count <= engine.MAX_CHUNKS
    assert ret.chunk_count >= engine.MIN_CHUNKS


def test_retrieval_citations_are_explicit_objects():
    spec = make_spec()
    ret = engine.retrieve(spec, _node(spec), "What is a qubit?",
                          make_corpus_text())
    assert len(ret.citations) == ret.chunk_count
    c = ret.citations[0]
    assert c.source_id in ("src-a", "src-b")
    assert c.tier == 2
    assert "qubit" in c.text.lower()
    assert c.char_end > c.char_start >= 0
    # citation serialises with all grounding fields
    d = c.to_dict()
    assert {"source_id", "source_name", "url", "text", "tier"} <= set(d)


def test_retrieval_thin_when_below_floor():
    spec = make_spec()
    ret = engine.retrieve(spec, _node(spec), "What is a qubit?",
                          {"src-a": THIN_TEXT, "src-b": THIN_TEXT})
    assert ret.satisfied is False
    assert ret.thin is True
    assert ret.chunk_count < engine.MIN_CHUNKS


def test_retrieval_flags_missing_sources():
    spec = make_spec(corpus_ids=("src-a", "src-b"))
    # only src-a has text; src-b is cited but empty -> flagged, not silent
    ret = engine.retrieve(spec, _node(spec), "What is a qubit?",
                          {"src-a": QUBIT_TEXT})
    assert "src-b" in ret.missing_sources
    assert "src-a" not in ret.missing_sources


# ---------- 3. quantitative detection + oracle preflight (T3) -----------------
def test_detect_quantitative_signals():
    assert engine.detect_quantitative("What is the probability of measuring |0>?")
    assert engine.detect_quantitative("How many queries does it take?")
    assert engine.detect_quantitative("Compute the expectation value.")
    assert not engine.detect_quantitative("What is a qubit?")
    assert not engine.detect_quantitative("Explain entanglement.")


def test_preflight_not_required_when_not_quantitative():
    spec = make_spec()
    pf = engine.oracle_preflight(_node(spec), quantitative=False)
    assert pf.required is False
    assert pf.status == "not-required"
    assert pf.result is None


def test_preflight_ran_ok(good_oracle):
    spec = make_spec()
    pf = engine.oracle_preflight(_node(spec), quantitative=True)
    assert pf.required is True
    assert pf.ok is True
    assert pf.status == "ran-ok"
    assert pf.result == {"P(|0>)": 0.5, "P(|1>)": 0.5, "normalized": True}


def test_preflight_ran_failed(monkeypatch):
    spec = make_spec()
    monkeypatch.setattr(engine, "run_oracle",
                        lambda name: {"ok": False, "error": "boom: statevector"})
    pf = engine.oracle_preflight(_node(spec), quantitative=True)
    assert pf.ok is False
    assert pf.status == "ran-failed"
    assert "boom" in (pf.error or "")


def test_preflight_unavailable_when_no_oracle():
    spec = make_spec(oracle=None)
    pf = engine.oracle_preflight(_node(spec), quantitative=True)
    assert pf.required is True
    assert pf.ok is None
    assert pf.status == "unavailable"


# ---------- 4. honest failure states (end-to-end) -----------------------------
def test_no_node_failure_state():
    spec = make_spec()
    a = engine.tutor(spec, "What is photosynthesis?", corpus_text=make_corpus_text())
    assert a.status == "no-node"
    assert a.grounded is False
    assert a.retrieval is None
    assert a.oracle is None
    assert any(f.startswith("no-node") for f in a.failures)
    # honest note in the draft, not a confident fact
    assert "not going to answer" in a.draft


def test_thin_retrieval_failure_state():
    spec = make_spec()
    a = engine.tutor(spec, "What is a qubit?",
                     corpus_text={"src-a": THIN_TEXT, "src-b": THIN_TEXT})
    assert a.status == "thin-retrieval"
    assert a.grounded is False
    assert any("thin-retrieval" in f for f in a.failures)


def test_oracle_failed_failure_state(monkeypatch):
    spec = make_spec(oracle="fake_qubit")
    monkeypatch.setattr(engine, "run_oracle",
                        lambda name: {"ok": False, "error": "boom: statevector"})
    a = engine.tutor(spec, "What is the probability of measuring a qubit?",
                     corpus_text=make_corpus_text())
    assert a.status == "oracle-failed"
    assert a.grounded is False
    assert a.oracle is not None
    assert a.oracle.status == "ran-failed"
    assert any("oracle-failed" in f for f in a.failures)
    # the failed oracle must NOT be presented as the answer
    assert "FAILED" in a.draft


def test_oracle_unavailable_failure_state():
    spec = make_spec(oracle=None)
    a = engine.tutor(spec, "What is the probability of measuring a qubit?",
                     corpus_text=make_corpus_text())
    assert a.status == "oracle-unavailable"
    assert a.grounded is False
    assert a.oracle is not None
    assert any("oracle-unavailable" in f for f in a.failures)


# ---------- 5. grounded end-to-end contract -----------------------------------
def test_grounded_answer_with_citations_and_oracle(good_oracle):
    spec = make_spec()
    a = engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text())
    assert a.status == "grounded"
    assert a.grounded is True
    assert a.resolution.node_id == "qubit"
    assert a.retrieval is not None
    assert 2 <= a.retrieval.chunk_count <= 5
    assert a.citations                      # explicit citation objects
    assert all(c.source_id in ("src-a", "src-b") for c in a.citations)
    # every citation text is real retrieved data containing the concept
    assert all("qubit" in c.text.lower() for c in a.citations)


def test_grounded_quantitative_includes_executed_oracle(good_oracle):
    spec = make_spec()
    a = engine.tutor(spec, "What is the probability of measuring a qubit?",
                     corpus_text=make_corpus_text())
    assert a.status == "grounded"
    assert a.quantitative is True
    assert a.oracle is not None
    assert a.oracle.status == "ran-ok"
    # the executed answer key is present in the draft, not invented
    assert '"P(|0>)": 0.5' in a.draft
    assert "fake_qubit" in a.draft


def test_answer_to_dict_shape(good_oracle):
    spec = make_spec()
    a = engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text())
    d = a.to_dict()
    assert {"question", "status", "grounded", "resolution", "retrieval",
            "oracle", "failures", "citations", "draft"} <= set(d)
    assert d["status"] == "grounded"
    assert d["retrieval"]["chunk_count"] >= engine.MIN_CHUNKS
    assert isinstance(d["citations"], list) and d["citations"]


# ---------- 6. draft seam (pluggable LLM) -------------------------------------
def test_draft_fn_seam_is_honoured(good_oracle):
    spec = make_spec()

    def fake_draft(question, resolution, node, retrieval, oracle):
        return f"DRAFT|{resolution.node_id}|{oracle.status}|{len(retrieval.citations)}"

    a = engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text(),
                     draft_fn=fake_draft)
    # the seam's output is honoured verbatim as the draft body...
    assert "DRAFT|qubit|not-required|5" in a.draft
    # ...and it is still run through the gate: this text has no claim section,
    # so the answer is honestly flagged unverified, never silently grounded.
    assert a.draft.startswith("UNVERIFIED ANSWER")
    assert a.grounded is False
    assert a.verification is not None
    assert a.verification.attempts == engine.MAX_DRAFT_ATTEMPTS


# ---------- 7. real local oracle (deterministic, no network) ------------------
def test_real_oracle_runs_and_is_deterministic():
    """Prove the real T3 path: a local Statevector oracle runs and is reproducible."""
    from open_tutor.generator import generate_quantum_computing
    spec = generate_quantum_computing()
    node = spec.node("grover")
    pf = engine.oracle_preflight(node, quantitative=True)
    assert pf.status == "ran-ok"
    assert pf.result is not None
    # reproducibility: running the same oracle again gives the identical key
    pf2 = engine.oracle_preflight(node, quantitative=True)
    assert pf.result == pf2.result


def test_no_node_never_calls_the_generation_seam():
    spec = make_spec()
    calls = {"n": 0}

    def must_not_run(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("model must not be called for no-node")

    answer = engine.tutor(spec, "What is photosynthesis?",
                          corpus_text=make_corpus_text(), draft_fn=must_not_run)
    assert answer.status == "no-node"
    assert calls["n"] == 0


def test_followup_context_and_selected_node_are_passed_to_local_draft(good_oracle):
    spec = make_spec()
    seen = {}

    def draft(question, resolution, node, retrieval, oracle, feedback,
              *, followup_context=None):
        seen.update(node=node.id, context=followup_context)
        return "## Claim\nA qubit is a two-level quantum system. [1]\n## Citations\n"

    # The optional context is an engine input, not a second question to resolve.
    answer = engine.tutor(
        spec, "Explain it again", selected_node="qubit",
        followup_context="Earlier answer: the learner asked about basis states.",
        corpus_text=make_corpus_text(), draft_fn=draft)
    assert answer.resolution.node_id == "qubit"
    assert seen == {
        "node": "qubit",
        "context": "Earlier answer: the learner asked about basis states.",
    }
