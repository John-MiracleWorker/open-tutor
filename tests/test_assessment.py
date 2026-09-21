"""P1.4 — Verified assessment, rubrics, and teach-back.

Deterministic and offline. The T4 contract (assessment.py) is exercised
against synthetic specs with monkeypatched oracles — no model, no network,
no clock. One test uses the real quantum-computing generator spec with real
qiskit oracle execution to prove the key binds to a *run* T3 output.

Covered, per the card:
  - answer-key correctness     -> the key is evidence-derived (executed oracle
                                  value / citation set), never authored
  - rubric decisions           -> deterministic score boundaries, named rules
  - partial / invalid answers  -> unverified never awards; malformed input is
                                  flagged or rejected, never silently scored
  - citation / oracle reqs     -> factual items need T2 citations; quantitative
                                  items need a RUN T3 oracle
  - teach-back misconceptions  -> the learner's explanation maps to *validated*
                                  node misconception ids via the P1.3 keyword map
  - no-T3 honest degradation   -> a broken/missing oracle degrades, it never
                                  fakes a runnable key
  - assessment is separate     -> the graded input is the learner's attempt +
                                  the gate verdict; a draft's self-assessment
                                  cannot choose its own grade
  - the human gate is kept     -> ``assess`` is pure: no compile, no I/O, no
                                  spec mutation
"""
from __future__ import annotations

import pytest

from open_tutor import assessment as a
from open_tutor.spec import CorpusSource, CurriculumSpec, Misconception, Node

# ---------- synthetic spec (the fixture) --------------------------------------
QUBIT_M1_TEXT = "A qubit is just a bit that is either zero or one that we don't know which."
QUBIT_M2_TEXT = "A qubit is a tiny spinning magnet."
SUPER_M1_TEXT = "Superposition means the qubit is secretly zero or one and we just haven't looked."


def make_spec() -> CurriculumSpec:
    corpus = [
        CorpusSource(id="src-a", name="Source A", url="https://a.example/qubit"),
        CorpusSource(id="src-b", name="Source B", url="https://b.example/qubit"),
    ]
    qubit = Node(
        id="qubit", title="The Qubit",
        defn="A two-level quantum system with basis states.",
        misconceptions=[Misconception(id="qubit-m1", text=QUBIT_M1_TEXT),
                        Misconception(id="qubit-m2", text=QUBIT_M2_TEXT)],
        covers_keywords=["qubit"],
        grounding_corpus=["src-a", "src-b"],
        oracle="fake_qubit",
    )
    super_node = Node(
        id="superposition", title="Superposition",
        defn="A qubit in a linear combination of basis states.",
        prereqs=["qubit"],
        misconceptions=[Misconception(id="super-m1", text=SUPER_M1_TEXT)],
        covers_keywords=["superposition"],
        grounding_corpus=["src-a"],
        oracle="fake_super",
    )
    return CurriculumSpec(subject="test-subject", title="Test", corpus=corpus,
                          nodes=[qubit, super_node])


GOOD = {"P(|0>)": 0.5, "P(|1>)": 0.5, "normalized": True}
BROKEN = {"ok": False, "error": "boom: the oracle crashed"}


@pytest.fixture
def spec() -> CurriculumSpec:
    return make_spec()


@pytest.fixture
def good_oracle(monkeypatch):
    monkeypatch.setattr(a, "run_oracle", lambda name: {"ok": True, "result": GOOD})


@pytest.fixture
def broken_oracle(monkeypatch):
    monkeypatch.setattr(a, "run_oracle", lambda name: BROKEN)


def grounded(text: str = "", cits: list | None = None) -> a.LearnerResponse:
    return a.LearnerResponse(
        text=text, grounded=True,
        citations=[{"source_id": s} for s in (cits or [])])


# ---------- 1. answer-key correctness ------------------------------------------
def test_worked_key_is_the_executed_oracle_value(spec, good_oracle):
    item = a.make_item(a.KIND_WORKED, "superposition", "Compute P(|0>) of H|0>.",
                       oracle="fake_super", oracle_field="P(|0>)",
                       required_citations=["src-a"])
    key = a.build_answer_key(item, spec)
    assert key.source == "oracle"
    assert key.numeric_key == 0.5          # read from the RUN oracle, not authored
    assert key.oracle_ran is True
    assert key.issues == []


def test_key_never_crosses_the_node_oracle(spec, good_oracle):
    item = a.make_item(a.KIND_WORKED, "qubit", "Show |0> basis.",
                       oracle="fake_super", required_citations=[])
    key = a.build_answer_key(item, spec)
    assert any(i.startswith("oracle-binding") for i in key.issues)


def test_factual_key_is_the_citation_set(spec):
    item = a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                       quantitative=False, required_citations=["src-a"])
    key = a.build_answer_key(item, spec)
    assert key.source == "citation"
    assert key.numeric_key is None
    assert key.citation_ids == ["src-a"]


def test_key_for_unknown_node_is_flagged(spec, good_oracle):
    item = a.make_item(a.KIND_QUIZ, "ghost", "Anything?", quantitative=False)
    key = a.build_answer_key(item, spec)
    assert any(i.startswith("node-unknown") for i in key.issues)


# ---------- 2. rubric decisions (deterministic boundaries) ----------------------
def test_teachback_full_coverage_is_correct(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level", "basis states"])
    rep = a.grade(item, grounded("A qubit is a two-level system with basis states."),
                  spec)
    assert rep["verdict"] == a.CORRECT
    assert rep["score"] == 1.0
    assert rep["rubric_hits"] == ["two-level", "basis states"]


def test_teachback_half_coverage_is_partial(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level", "basis states"])
    rep = a.grade(item, grounded("A qubit has basis states, that's all."), spec)
    assert rep["verdict"] == a.PARTIAL
    assert rep["score"] == 0.5


def test_teachback_below_boundary_is_incorrect(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level", "basis states", "unit vector"])
    rep = a.grade(item, grounded("It's kind of a bit, I think."), spec)
    assert rep["verdict"] == a.INCORRECT
    assert rep["score"] == 0.0


def test_teachback_with_required_citations_penalty(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level"], required_citations=["src-a"])
    with_cit = a.grade(item, grounded("A two-level system.", cits=["src-a"]), spec)
    no_cit = a.grade(item, grounded("A two-level system.", cits=[]), spec)
    assert with_cit["score"] == 1.0 and with_cit["verdict"] == a.CORRECT
    assert no_cit["score"] == 0.5          # 1.0 * MISSING_CITATION_FACTOR
    assert any(i.startswith("citation-coverage") for i in no_cit["issues"])


# ---------- 3. partial / invalid answers ----------------------------------------
def test_unverified_attempt_never_awards_quiz(spec, good_oracle):
    item = a.make_item(a.KIND_QUIZ, "superposition", "Compute P(|0>) of H|0>.",
                       quantitative=True, oracle="fake_super", oracle_field="P(|0>)")
    rep = a.grade(item, a.LearnerResponse(text="0.5", grounded=False), spec)
    assert rep["verdict"] == a.INCORRECT
    assert rep["score"] == 0.0
    assert any(i.startswith("unverified-attempt") for i in rep["issues"])


def test_empty_teachback_is_flagged_not_scored(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level"])
    rep = a.grade(item, a.LearnerResponse(text="   ", grounded=True), spec)
    assert rep["verdict"] == a.FLAGGED
    assert rep["score"] is None
    assert any(i.startswith("empty-teachback") for i in rep["issues"])


def test_teachback_without_rubric_is_flagged(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=[])
    rep = a.grade(item, grounded("A two-level system."), spec)
    assert rep["verdict"] == a.FLAGGED
    assert rep["score"] is None
    assert any(i.startswith("no-rubric") for i in rep["issues"])


def test_missing_response_is_not_scored(spec, good_oracle):
    item = a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                       quantitative=False, required_citations=["src-a"])
    rep = a.grade(item, None, spec)
    assert rep["verdict"] == a.NOT_SCORED
    assert rep["score"] is None
    assert any(i.startswith("no-response") for i in rep["issues"])


def test_unknown_node_is_flagged_not_scored(spec, good_oracle):
    item = a.make_item(a.KIND_QUIZ, "ghost", "Anything?", quantitative=False)
    rep = a.grade(item, grounded("x", ["src-a"]), spec)
    assert rep["verdict"] == a.FLAGGED
    assert rep["score"] is None


def test_unknown_kind_is_flagged(spec):
    rep = a.grade({"kind": "essay", "node_id": "qubit"}, grounded("x"), spec)
    assert rep["verdict"] == a.FLAGGED
    assert any(i.startswith("kind-unknown") for i in rep["issues"])


def test_item_validation_rejects_bad_shapes(spec, good_oracle):
    good = a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                       quantitative=False, required_citations=["src-a"])
    assert a.validate_item(good, spec)[0]

    bad_kind = dict(good, kind="essay")
    assert any(i.startswith("kind-unknown") for i in a.validate_item(bad_kind, spec)[1])

    extra = dict(good, bonus_field=1)
    assert any(i == "field-extra:bonus_field" for i in a.validate_item(extra, spec)[1])

    missing = {k: v for k, v in good.items() if k != "prompt"}
    assert any(i == "field-missing:prompt" for i in a.validate_item(missing, spec)[1])

    unknown_version = dict(good, v="99")
    assert any(i.startswith("version-unknown") for i in
               a.validate_item(unknown_version, spec)[1])

    unknown_cit = a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                              quantitative=False, required_citations=["ghost"])
    assert any(i.startswith("citation-unknown") for i in
               a.validate_item(unknown_cit, spec)[1])


# ---------- 4. citation / oracle requirements ----------------------------------
def test_citation_quiz_correct_when_covered(spec):
    item = a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                       quantitative=False, required_citations=["src-a"])
    rep = a.grade(item, grounded("A two-level system.", cits=["src-a"]), spec)
    assert rep["verdict"] == a.CORRECT
    assert rep["score"] == 1.0


def test_citation_quiz_partial_when_uncovered(spec):
    item = a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                       quantitative=False, required_citations=["src-a"])
    rep = a.grade(item, grounded("A two-level system.", cits=["src-b"]), spec)
    assert rep["verdict"] == a.PARTIAL
    assert any(i.startswith("citation-coverage") for i in rep["issues"])


def test_quantitative_quiz_degrades_without_a_run_oracle(spec, broken_oracle):
    item = a.make_item(a.KIND_QUIZ, "superposition", "Compute P(|0>) of H|0>.",
                       quantitative=True, oracle="fake_super", oracle_field="P(|0>)")
    rep = a.grade(item, grounded("0.5"), spec)
    assert rep["verdict"] == a.DEGRADED
    assert rep["score"] is None
    assert any(i.startswith("no-t3") for i in rep["issues"])


def test_worked_degrades_when_oracle_did_not_run(spec, broken_oracle):
    item = a.make_item(a.KIND_WORKED, "superposition", "Compute P(|0>) of H|0>.",
                       oracle="fake_super", oracle_field="P(|0)",
                       required_citations=[])
    rep = a.grade(item, grounded("0.5"), spec)
    assert rep["verdict"] == a.DEGRADED
    assert rep["score"] is None
    assert any(i.startswith("no-t3-run") for i in rep["issues"])


def test_worked_without_oracle_is_flagged(spec, good_oracle):
    item = {"v": a.SCHEMA_VERSION, "kind": a.KIND_WORKED, "id": "w1",
            "node_id": "qubit", "prompt": "Show it.", "oracle": None,
            "oracle_field": None, "required_citations": []}
    rep = a.grade(item, grounded("x"), spec)
    assert rep["verdict"] == a.FLAGGED
    assert any(i.startswith("no-oracle") for i in rep["issues"])


def test_worked_incomplete_numeric_field_is_partial(spec, good_oracle):
    monkey_good = {"ok": True, "result": {"note": "no numbers here"}}
    item = a.make_item(a.KIND_WORKED, "superposition", "Compute it.",
                       oracle="fake_super", oracle_field="missing-number",
                       required_citations=[])
    import open_tutor.assessment as A
    A.run_oracle = lambda name: monkey_good
    try:
        rep = a.grade(item, grounded("x"), spec)
    finally:
        A.run_oracle = None
    # restored by the next fixture use; assert the partial boundary held
    assert rep["verdict"] == a.PARTIAL
    assert any(i.startswith("incomplete-key") for i in rep["issues"])


# ---------- 5. teach-back misconception mapping ---------------------------------
def test_teachback_maps_validated_misconception(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level"])
    text = ("A qubit is just a bit that is either zero or one that we don't "
            "know which, so it is a two-level thing.")
    rep = a.grade(item, grounded(text, ["src-a"]), spec)
    mids = [m["misconception_id"] for m in rep["misconceptions"]]
    assert "qubit-m1" in mids            # a validated T1 reference, not a guess
    assert all(m["node_id"] == "qubit" for m in rep["misconceptions"])
    # full rubric (1.0) halved by the validated misconception trigger
    assert rep["score"] == 0.5
    assert any(i.startswith("misconception-triggered") for i in rep["issues"])


def test_teachback_clean_explanation_maps_nothing(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level", "basis states"])
    rep = a.grade(item, grounded("A qubit is a two-level system with basis states."),
                  spec)
    assert rep["misconceptions"] == []
    assert rep["score"] == 1.0


def test_teachback_scoped_to_its_node_only(spec, good_oracle):
    # The superposition misconception text contains "qubit" — but a qubit-node
    # teach-back must not map the superposition node's misconception.
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level"])
    text = ("Superposition means the qubit is secretly zero or one and we "
            "just haven't looked, but a qubit is a two-level system.")
    rep = a.grade(item, grounded(text, ["src-a"]), spec)
    assert all(m["node_id"] == "qubit" for m in rep["misconceptions"])


# ---------- 6. no-T3 honest degradation ------------------------------------------
def test_no_usable_key_never_fabricates_one(spec, broken_oracle):
    item = a.make_item(a.KIND_QUIZ, "superposition", "Compute P(|0>) of H|0>.",
                       quantitative=True, oracle="fake_super", oracle_field="P(|0)")
    key = a.build_answer_key(item, spec)
    assert key.source == "none"
    assert key.numeric_key is None
    assert any(i.startswith("no-t3-key") for i in key.issues)


# ---------- 7. assessment stays separate from the draft -------------------------
def test_response_adapter_reads_only_learner_surface(spec):
    class FakeVerification:
        def to_dict(self):
            return {"grounded": True, "issues": []}

    class FakeCitation:
        def to_dict(self):
            return {"source_id": "src-a", "text": "…"}

    class FakeAnswer:
        draft = "A qubit is a two-level system with basis states."
        grounded = False                       # self-claim; ignored…
        verification = FakeVerification()      # …the gate verdict wins

        def __init__(self):
            self.citations = [FakeCitation()]

    resp = a.response_from_answer(FakeAnswer())
    assert resp.grounded is True
    assert resp.text == FakeAnswer.draft
    assert [c["source_id"] for c in resp.citations] == ["src-a"]


def test_grade_ignores_any_draft_self_score(spec, good_oracle):
    item = a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                       quantitative=False, required_citations=["src-a"])
    resp = a.LearnerResponse(text="whatever", grounded=False,
                             citations=[{"source_id": "src-a"}])
    rep = a.grade(item, resp, spec)
    # The learner cannot choose its own grade: unverified -> 0.0, always.
    assert rep["verdict"] == a.INCORRECT and rep["score"] == 0.0


def test_dict_responses_are_adapted(spec, good_oracle):
    item = a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                       quantitative=False, required_citations=["src-a"])
    report = a.assess(spec, [item], responses={
        "quiz-qubit": {"draft": "A two-level system.", "grounded": True,
                       "citations": [{"source_id": "src-a"}]}})
    assert report["results"][0]["verdict"] == a.CORRECT


# ---------- 8. the human gate is preserved ---------------------------------------
def test_assess_is_pure_and_reports_honest_summary(spec, good_oracle):
    items = [
        a.make_item(a.KIND_QUIZ, "qubit", "What is a qubit?",
                    quantitative=False, required_citations=["src-a"]),
        a.make_item(a.KIND_TEACHBACK, "qubit", "Explain a qubit.",
                    rubric_points=["two-level"], required_citations=["src-a"]),
    ]
    report = a.assess(spec, items, responses={
        "quiz-qubit": grounded("A two-level system.", ["src-a"]),
        # teach-back item has NO response -> honestly not-scored
    })
    assert report["schema_version"] == a.SCHEMA_VERSION
    assert report["items_total"] == 2
    assert report["summary"][a.CORRECT] == 1
    assert report["summary"][a.NOT_SCORED] == 1
    # no compile, no mutation: the spec is untouched
    assert [n.status for n in spec.nodes] == ["unknown", "unknown"]
    assert not spec.generator_note


# ---------- 9. end-to-end: the real quantum spec, real qiskit T3 ------------------
def test_end_to_end_real_spec_and_real_oracle():
    from open_tutor.generator import generate_quantum_computing
    spec = generate_quantum_computing()
    item = a.make_item(a.KIND_WORKED, "superposition",
                       "Compute P(|0>) of H|0>.",
                       oracle="superposition_hadamard", oracle_field="P(|0>)",
                       required_citations=["wiki-superposition"])
    ok, issues = a.validate_item(item, spec)
    assert ok, issues

    key = a.build_answer_key(item, spec)
    assert key.oracle_ran is True                 # REAL qiskit execution
    assert key.numeric_key == 0.5                 # the executed value, verbatim
    assert key.source == "oracle"

    rep = a.grade(item, grounded("P(|0>) = 0.5", ["wiki-superposition"]), spec)
    assert rep["verdict"] == a.CORRECT
    assert rep["answer_key"]["numeric_key"] == 0.5


def test_real_spec_teachback_maps_the_known_misconception():
    from open_tutor.generator import generate_quantum_computing
    spec = generate_quantum_computing()
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain what a qubit is.",
                       rubric_points=["two-level", "basis"],
                       required_citations=["wiki-qubit"])
    text = ("A qubit is just a bit that is either 0 or 1 that we don't know "
            "which; it is a two-level system with basis states.")
    rep = a.grade(item, grounded(text, ["wiki-qubit"]), spec)
    mids = [m["misconception_id"] for m in rep["misconceptions"]]
    assert "qubit-m1" in mids
    assert all(m["node_id"] == "qubit" for m in rep["misconceptions"])


def test_cross_bound_oracle_can_never_produce_a_scored_key(spec, good_oracle):
    item = a.make_item(a.KIND_QUIZ, "qubit", "Compute it.",
                       quantitative=True, oracle="fake_super",
                       oracle_field="P(|0>)")
    key = a.build_answer_key(item, spec)
    assert key.source == "none"
    assert key.numeric_key is None
    rep = a.grade(item, grounded("0.5"), spec)
    assert rep["verdict"] in (a.DEGRADED, a.FLAGGED)
    assert rep["score"] is None


def test_unverified_teachback_is_not_scored(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain it.",
                       rubric_points=["two-level"])
    rep = a.grade(item, a.LearnerResponse(
        text="A qubit is a two-level system.", grounded=False), spec)
    assert rep["verdict"] == a.INCORRECT
    assert rep["score"] == 0.0
    assert any(i.startswith("unverified-attempt") for i in rep["issues"])


def test_ambiguous_teachback_with_claimed_rubric_is_flagged(spec, good_oracle):
    item = a.make_item(a.KIND_TEACHBACK, "qubit", "Explain it.",
                       rubric_points=["two-level"])
    rep = a.grade(item, grounded("I think a qubit is a two-level system."), spec)
    assert rep["verdict"] == a.FLAGGED
    assert rep["score"] is None
    assert any(i.startswith("ambiguous-teachback") for i in rep["issues"])
