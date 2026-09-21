"""P1.2 — Grounded answer drafting, citations, and the verifier gate.

Deterministic and offline. ``verify_answer`` (verifier.py) is exercised
directly with synthetic answer-like objects (it only reads ``draft``,
``quantitative``, ``citations``, ``oracle``), and ``engine.tutor`` is exercised
end-to-end with injected corpus text, monkeypatched oracles, and stub draft
seams — so every test is reproducible with no model and no network.

Covered, per the card:
  - valid citations                -> grounded, citations_used recorded
  - missing / invalid citations    -> ungrounded, named issues, never silent
  - quantitative oracle mismatch   -> ungrounded (invented number / failed oracle)
  - retry limits                   -> bounded at MAX_DRAFT_ATTEMPTS, last draft kept
  - honest unverified output       -> banner + grounded=False + issues surfaced
  - data-only content, invariant   -> MIN_GROUNDING_CHARS untouched
"""
from __future__ import annotations

import pytest

from open_tutor import engine, verifier
from open_tutor.spec import CorpusSource, CurriculumSpec, Node

# ---------- synthetic corpus text (data, not instructions) --------------------
QUBIT_TEXT = "\n\n".join(
    f"A qubit is a two-level quantum system. A qubit is the fundamental two-level quantum unit, the quantum analogue of "
    f"the classical bit, realised by a pair of basis states labelled zero and one. "
    f"Paragraph {i} develops the qubit with further context so the span stays long."
    for i in range(8)
)
THIN_TEXT = "A qubit is a two-level quantum system. It has two basis states."


# ---------- fixtures ----------------------------------------------------------
def make_spec(oracle: str | None = "fake_qubit") -> CurriculumSpec:
    corpus = [
        CorpusSource(id="src-a", name="Source A", url="https://a.example/qubit"),
        CorpusSource(id="src-b", name="Source B", url="https://b.example/qubit"),
    ]
    node = Node(
        id="qubit", title="The Qubit",
        defn="A qubit is a two-level quantum system with basis states.",
        covers_keywords=["qubit"],
        grounding_corpus=["src-a", "src-b"],
        oracle=oracle,
    )
    return CurriculumSpec(subject="test", title="Test", corpus=corpus, nodes=[node])


def make_corpus_text() -> dict:
    return {"src-a": QUBIT_TEXT, "src-b": QUBIT_TEXT}


def make_citation(n: int = 1):
    return engine.Citation(
        source_id="src-a", source_name="Source A", url="https://a.example/qubit",
        tier=2, text=(f"A qubit is a two-level quantum system. (span {n}) "
                      "control of superconducting qudits up to in 2024 based on "
                      "programmable two-photon interactions.[16] similar to the qubit"),
        char_start=0, char_end=50, score=1.0,
    )


class FakeOracle:
    def __init__(self, ok=True, result=None, error=None):
        self.required = True
        self.oracle_name = "fake_qubit"
        self.ok = ok
        self.status = "ran-ok" if ok else "ran-failed"
        self.result = result
        self.error = error


class FakeAnswer:
    """Duck-typed stand-in for engine.Answer: verify_answer reads only these
    fields, so the gate is tested without running the full engine."""
    def __init__(self, draft="", quantitative=False, citations=None, oracle=None):
        self.draft = draft
        self.quantitative = quantitative
        self.citations = citations or []
        self.oracle = oracle


@pytest.fixture
def good_oracle(monkeypatch):
    monkeypatch.setattr(
        engine, "run_oracle",
        lambda name: {"ok": True, "result": {"P(|0>)": 0.5, "P(|1>)": 0.5,
                                             "normalized": True}})


def _valid_draft() -> str:
    return (
        "Question: What is a qubit?\n"
        "Resolved node: The Qubit (id=qubit, score=3.0)\n"
        "\n"
        "## Evidence (T2 — retrieved spans)\n"
        "[1] Source A — A qubit is a two-level quantum system. (span 1)\n"
        "[2] Source B — A qubit is a two-level quantum system. (span 2)\n"
        "\n"
        "## Claim\n"
        "The Qubit is described as: “A qubit is a two-level quantum system.” [1].\n"
        "The Qubit is described as: “A qubit is a two-level quantum system.” [2].\n"
        "\n"
        "## Citations\n"
        "[1] Source A — https://a.example/qubit\n"
        "[2] Source B — https://b.example/qubit\n"
    )


# A draft whose single claim line carries NO inline [n] marker — the gate must
# flag it missing-citation (never silently pass an uncited claim).
UNCITED_DRAFT = (
    "## Claim\n"
    "A qubit is a two-level quantum system with basis states.\n"
    "## Citations\n"
    "[1] Source A — https://a.example/qubit\n"
)


# ---------- 1. valid citations -----------------------------------------------
def test_valid_citations_pass_gate():
    rep = verifier.verify_answer(FakeAnswer(_valid_draft(), citations=[make_citation(1),
                                                                       make_citation(2)]),
                                 make_spec())
    assert rep["grounded"] is True
    assert rep["issues"] == []
    assert rep["citations_used"] == [1, 2]
    assert rep["uncited_claims"] == []
    assert rep["oracle_matched"] is None  # non-quantitative: T3 rule not applied


def test_gate_is_pure_and_deterministic():
    a = FakeAnswer(_valid_draft(), citations=[make_citation(1), make_citation(2)])
    assert verifier.verify_answer(a, make_spec()) == verifier.verify_answer(a, make_spec())


def test_quoted_span_with_bracket_marker_does_not_falsely_fail():
    """Regression: a retrieved T2 span's QUOTED text can carry the source's own
    bracket citation (a wiki "[16]"). The gate must not read that as the
    drafter's invalid inline citation — quoted runs are data, not claims."""
    draft = (
        "Question: What is a qubit?\n"
        "## Evidence (T2 — retrieved spans)\n"
        "[1] Source A — control of superconducting qudits up to in 2024 "
        "based on programmable two-photon interactions.[16] similar to the qubit\n"
        "\n"
        "## Claim\n"
        "The Qubit is described as: "
        "“control of superconducting qudits up to in 2024 based on "
        "programmable two-photon interactions.[16] similar to the qubit” [1].\n"
        "\n"
        "## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(FakeAnswer(draft, citations=[make_citation(1)]),
                                 make_spec())
    # The quoted "[16]" is the source's own marker, not the drafter's; the
    # drafter's real citation is [1], which is valid. No false invalid-citation.
    assert rep["grounded"] is True, rep["issues"]
    assert rep["citations_used"] == [1]
    assert not any(i.startswith("invalid-citation") for i in rep["issues"])


def test_genuine_invalid_marker_outside_quotes_is_still_flagged():
    """The quoted-span carve-out must not hide a REAL invalid citation that the
    drafter wrote in their own (unquoted) prose."""
    draft = (
        "## Claim\n"
        "A qubit is a two-level quantum system. [99]  See also [1].\n"
        "\n"
        "## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(FakeAnswer(draft, citations=[make_citation(1)]),
                                 make_spec())
    assert rep["grounded"] is False
    assert any(i.startswith("invalid-citation") and "99" in i for i in rep["issues"])


# ---------- 2. missing / invalid citations -----------------------------------
def test_missing_citation_is_flagged_not_silent():
    rep = verifier.verify_answer(FakeAnswer(UNCITED_DRAFT, citations=[make_citation(1)]),
                                 make_spec())
    assert rep["grounded"] is False
    assert any(i.startswith("missing-citation") for i in rep["issues"])
    assert any("two-level quantum system with basis states" in u
               for u in rep["uncited_claims"])


def test_invalid_citation_marker_is_flagged():
    bad = (
        "## Claim\n"
        "A qubit is great. [99]\n"
        "\n"
        "## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(FakeAnswer(bad, citations=[make_citation(1)]),
                                 make_spec())
    assert rep["grounded"] is False
    assert any(i.startswith("invalid-citation") and "99" in i for i in rep["issues"])
    assert 99 not in rep["citations_used"]


def test_empty_draft_and_claimless_draft_are_ungrounded():
    assert verifier.verify_answer(FakeAnswer(""), make_spec())["grounded"] is False
    no_claim = "Just some prose without the claim section. [1]"
    rep = verifier.verify_answer(FakeAnswer(no_claim, citations=[make_citation(1)]),
                                 make_spec())
    assert rep["grounded"] is False


def test_no_citations_at_all_is_ungrounded():
    draft = (
        "## Claim\n"
        "A qubit is a two-level quantum system with basis states. [1]\n"
        "\n"
        "## Citations\n"
    )
    rep = verifier.verify_answer(FakeAnswer(draft, citations=[]), make_spec())
    assert rep["grounded"] is False
    assert any(i.startswith("no-citations") for i in rep["issues"])


# ---------- 3. quantitative oracle match / mismatch ---------------------------
def test_quantitative_claim_matching_oracle_passes():
    oracle = FakeOracle(True, {"P(|0>)": 0.5, "P(|1>)": 0.5, "normalized": True})
    draft = (
        "## Claim\n"
        "The probability of outcome 0 is P(|0>) = 0.5 [1].\n"
        "\n"
        "## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(
        FakeAnswer(draft, quantitative=True, citations=[make_citation(1)], oracle=oracle),
        make_spec())
    assert rep["grounded"] is True
    assert rep["oracle_matched"] is True


def test_quantitative_invented_number_is_mismatch():
    oracle = FakeOracle(True, {"P(|0>)": 0.5, "P(|1>)": 0.5, "normalized": True})
    draft = (
        "## Claim\n"
        "The probability of outcome 0 is P(|0>) = 0.75 [1].\n"
        "\n"
        "## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(
        FakeAnswer(draft, quantitative=True, citations=[make_citation(1)], oracle=oracle),
        make_spec())
    assert rep["grounded"] is False
    assert rep["oracle_matched"] is False
    assert any(i.startswith("quant-mismatch") and "0.75" in i for i in rep["issues"])


def test_quantitative_without_ran_ok_oracle_is_mismatch():
    oracle = FakeOracle(False, None, error="boom")
    draft = (
        "## Claim\n"
        "The probability is P(|0>) = 0.5 [1].\n"
        "\n"
        "## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(
        FakeAnswer(draft, quantitative=True, citations=[make_citation(1)], oracle=oracle),
        make_spec())
    assert rep["grounded"] is False
    assert any(i.startswith("oracle-mismatch") for i in rep["issues"])


# ---------- 4. engine loop: retry limits + honest unverified ------------------
def test_retry_repair_lands_on_passing_draft(good_oracle):
    spec = make_spec()
    attempts = {"n": 0}

    def repair(q, r, n, ret, o, feedback):
        attempts["n"] += 1
        if attempts["n"] < 3:  # first two attempts: uncited claim -> gate fails
            return UNCITED_DRAFT
        return _valid_draft()  # third attempt cites properly

    a = engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text(),
                     draft_fn=repair)
    assert attempts["n"] == 3
    assert a.status == "grounded"
    assert a.grounded is True
    assert a.verification is not None
    assert a.verification.attempts == 3
    assert a.verification.grounded is True
    assert a.verification.issues == []
    assert "UNVERIFIED" not in a.draft


def test_retry_limit_is_bounded_and_honest(good_oracle):
    spec = make_spec()
    calls = {"n": 0}

    def stubborn(q, r, n, ret, o, feedback):
        calls["n"] += 1
        if calls["n"] > 1:
            assert feedback  # retry calls carry the gate's issue list for repair
        return UNCITED_DRAFT

    a = engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text(),
                     draft_fn=stubborn)
    assert calls["n"] == engine.MAX_DRAFT_ATTEMPTS  # bounded, no infinite loop
    assert a.grounded is False
    assert a.status == "unverified"
    assert a.verification is not None
    assert a.verification.attempts == engine.MAX_DRAFT_ATTEMPTS
    assert a.verification.grounded is False
    assert any(i.startswith("missing-citation") for i in a.verification.issues)
    # honest output: the draft is returned, explicitly labelled unverified
    assert a.draft.startswith("UNVERIFIED ANSWER")
    assert "missing-citation" in a.draft


def test_draft_fn_error_is_not_swallowed(good_oracle):
    spec = make_spec()

    def broken(q, r, n, ret, o):
        raise ValueError("model exploded")

    with pytest.raises(ValueError):
        engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text(),
                     draft_fn=broken)


def test_legacy_five_arg_draft_seam_still_works(good_oracle):
    spec = make_spec()
    legacy = {"n": 0}

    def draft(q, r, n, ret, o):
        legacy["n"] += 1
        return _valid_draft()

    a = engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text(),
                     draft_fn=draft)
    assert a.grounded is True
    assert legacy["n"] == 1  # clean first attempt passes; no spurious retry


# ---------- 5. engine end-to-end: gate on the default template ----------------
def test_default_template_passes_gate_qualitative(good_oracle):
    spec = make_spec()
    a = engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text())
    assert a.status == "grounded"
    assert a.grounded is True
    assert a.verification is not None
    assert a.verification.attempts == 1
    assert a.verification.grounded is True
    assert a.verification.issues == []
    # inline citations in the emitted draft
    assert "## Claim" in a.draft
    assert "[1]" in a.draft and "[2]" in a.draft
    assert "## Citations" in a.draft
    assert "UNVERIFIED" not in a.draft


def test_default_template_passes_gate_quantitative(good_oracle):
    spec = make_spec()
    a = engine.tutor(spec, "What is the probability of measuring |0> for a qubit?",
                     corpus_text=make_corpus_text())
    assert a.status == "grounded"
    assert a.grounded is True
    assert a.oracle is not None and a.oracle.status == "ran-ok"
    assert a.verification is not None
    assert a.verification.grounded is True
    assert a.verification.oracle_matched is True
    # the executed oracle key is quoted in the draft
    assert "0.5" in a.draft


def test_oracle_failed_answer_is_banned_unverified():
    spec = make_spec()
    a = engine.tutor(spec, "What is the probability of measuring |0> for a qubit?",
                     corpus_text=make_corpus_text(),
                     draft_fn=lambda q, r, n, ret, o: _valid_draft())
    assert a.status == "oracle-failed"
    assert a.grounded is False
    assert a.verification is not None
    assert a.verification.grounded is False
    assert a.verification.oracle_matched is False
    assert any(i.startswith("oracle-mismatch") for i in a.verification.issues)
    assert a.draft.startswith("UNVERIFIED ANSWER")


def test_thin_retrieval_stays_honest_with_verification():
    spec = make_spec()
    a = engine.tutor(spec, "What is a qubit?",
                     corpus_text={"src-a": THIN_TEXT, "src-b": THIN_TEXT})
    assert a.status == "thin-retrieval"
    assert a.grounded is False
    assert a.verification is not None
    assert a.verification.grounded is False
    assert a.draft.startswith("UNVERIFIED ANSWER")


# ---------- 6. invariants: data-only content + untouched bar ------------------
def test_min_grounding_chars_invariant_untouched():
    assert verifier.MIN_GROUNDING_CHARS == 2500


def test_draft_is_data_only_and_verifier_pure(good_oracle):
    spec = make_spec()
    before = (spec.to_dict(),)
    a = engine.tutor(spec, "What is a qubit?", corpus_text=make_corpus_text())
    # the gate neither mutated the spec nor executed anything
    assert spec.to_dict() == before[0]
    # the draft carries no instruction-like content: retrieved spans are quoted
    # verbatim (data), nothing is parsed as a command
    assert "ignore previous instructions" not in a.draft.lower()
    assert a.draft  # non-empty, honest
    assert a.grounded is True


def test_unrelated_citation_does_not_certify_unsupported_paraphrase():
    draft = (
        "## Claim\n"
        "A qubit is a classical bit with an unknown value. [1]\n"
        "\n## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(
        FakeAnswer(draft, citations=[make_citation(1)]), make_spec())
    assert rep["grounded"] is False
    assert any(i.startswith("unsupported-claim") for i in rep["issues"])


def test_negated_supported_excerpt_is_not_treated_as_supported():
    draft = (
        "## Claim\n"
        "A qubit is not a two-level quantum system. [1]\n"
        "\n## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(
        FakeAnswer(draft, citations=[make_citation(1)]), make_spec())
    assert rep["grounded"] is False
    assert any(i.startswith("unsupported-claim") for i in rep["issues"])


def test_oracle_key_path_and_value_are_bound_together():
    oracle = FakeOracle(True, {"P(|0>)": 0.5, "P(|1>)": 0.25})
    draft = (
        "## Claim\n"
        "The probability is P(|0>) = 0.25 [1].\n"
        "\n## Citations\n[1] Source A — https://a.example/qubit\n"
    )
    rep = verifier.verify_answer(
        FakeAnswer(draft, quantitative=True, citations=[make_citation(1)],
                   oracle=oracle), make_spec())
    assert rep["grounded"] is False
    assert any(i.startswith("quant-mismatch") for i in rep["issues"])


def test_structural_verifier_rejects_empty_duplicate_and_missing_references():
    empty = CurriculumSpec(subject="test", title="Empty")
    assert verifier.structural_checks(empty)

    duplicate = make_spec()
    duplicate.nodes.append(duplicate.nodes[0])
    errors = verifier.structural_checks(duplicate)
    assert any("duplicate" in value for value in errors.values())

    missing = make_spec()
    missing.nodes[0].prereqs = ["does-not-exist"]
    assert any("unknown prereq" in value
               for value in verifier.structural_checks(missing).values())


def test_verify_report_keeps_extracted_corpus_text(monkeypatch, tmp_path):
    spec = make_spec()
    body = "A qubit is documented here. " * 120
    monkeypatch.setattr(
        verifier, "fetch_live",
        lambda url: {"status": 200, "ok": True, "text": body,
                      "text_len": len(body), "method": "fixture",
                      "needs_render": False, "title": None, "author": None,
                      "error": None},
    )
    monkeypatch.setattr(verifier, "run_oracle",
                        lambda name: {"ok": True, "result": {}})
    report = verifier.verify(spec, out_dir=str(tmp_path))
    assert report["corpus"][0]["text"] == body
