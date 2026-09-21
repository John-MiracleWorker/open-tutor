"""P1.0 — automatic verified compilation with optional review mode.

Deterministic, offline: the verifier report is synthetic (exactly the shape
``verifier.verify`` produces), the oracles are stubbed or real local runs, and
all artifacts land in a tmp dir. No network.

Covered, per the card:
  1. automatic verified compilation   -> compiled, no approval, artifacts exist
  2. optional review mode             -> paused on new subject / material change
                                         with diff + receipts + status + audit
  3. material-change detection        -> added/removed/changed nodes & sources,
                                         cosmetic-only stays non-material,
                                         re-verification of the same content is
                                         NOT a change
  4. exception blocking               -> failed grounding / thin node / dead
                                         source / oracle failure / structural
                                         cycle / unsafe change / corrupt prior
                                         bundle / missing prior bundle
  5. invariants preserved             -> MIN_GROUNDING_CHARS untouched,
                                         non-LLM deterministic decision
"""
from __future__ import annotations

import copy
import json
import os

import yaml

from open_tutor import compiler
from open_tutor.compiler import (
    BLOCKED,
    COMPILED,
    PAUSED,
    compile_spec,
    diff_specs,
    load_last_spec,
    spec_content_hash,
)
from open_tutor.spec import CorpusSource, CurriculumSpec, Misconception, Node
from open_tutor.verifier import MIN_GROUNDING_CHARS

# ---------- synthetic spec + report builders (verifier-shaped data) ----------
def make_spec(subject: str = "test-subject",
              corpus_ids=("src-a", "src-b")) -> CurriculumSpec:
    corpus = [
        CorpusSource(id="src-a", name="Source A", url="https://a.example/qubit"),
        CorpusSource(id="src-b", name="Source B", url="https://b.example/qubit"),
    ]
    node = Node(
        id="qubit", title="The Qubit",
        defn="A qubit is a two-level quantum system with basis states.",
        covers_keywords=["qubit"],
        grounding_corpus=list(corpus_ids),
        oracle="fake_qubit",
        misconceptions=[Misconception(id="qubit-m1", text="A qubit is a hidden bit.")],
    )
    return CurriculumSpec(subject=subject, title="Test Subject",
                          corpus=corpus, nodes=[node],
                          oracle={"id": "fake"})


def make_report(spec: CurriculumSpec, *,
                all_grounded: bool = True,
                node_status: str = "grounded",
                source_status: str = "live",
                oracle_ok: bool = True,
                structural_ok: bool = True,
                structural_errors: dict | None = None) -> dict:
    """A verifier-shaped report (the exact keys ``verifier.verify`` emits).

    Like the real verifier, it also stamps the spec's per-node verification
    state and corpus status — ``build_receipts`` reads the grounding detail
    from ``node.verification``, exactly as the live pipeline leaves it.
    """
    s = spec.subject
    live = source_status == "live"
    for n in spec.nodes:
        n.status = node_status
        n.verification = {
            "structural_ok": structural_ok,
            "grounding": {
                "cited_sources": [c.id for c in spec.corpus],
                "live_sources": [c.id for c in spec.corpus] if live else [],
                "coverage_hits": [c.id for c in spec.corpus]
                if (all_grounded and live) else [],
                "ok": bool(all_grounded and live),
            },
            "oracle": {"name": n.oracle, "ok": oracle_ok},
        }
    for c in spec.corpus:
        c.status = source_status
        c.http_status = 200 if live else 404
    return {
        "subject": s,
        "structural": {"ok": structural_ok,
                       "errors": structural_errors or {}},
        "corpus": [
            {"id": c.id, "name": c.name, "url": c.url,
             "status": source_status, "http_status": 200 if source_status == "live" else 404,
             "text_len": 26000 if source_status == "live" else 0,
             "method": "trafilatura", "needs_render": False,
             "author": None, "error": None if source_status == "live" else "HTTP 404",
             "text": "qubit " * 100}
            for c in spec.corpus
        ],
        "nodes": [
            {"id": n.id, "title": n.title, "status": node_status,
             "grounding_ok": node_status == "grounded",
             "oracle": n.oracle, "oracle_ok": oracle_ok}
            for n in spec.nodes
        ],
        "oracle_outputs": ({
            "fake_qubit": {"status": "ok",
                           "output": "out/oracle_outputs/fake_qubit.json",
                           "result": {"P(|0>)": 0.5, "normalized": True}}
            if oracle_ok else
            {"status": "error", "error": "boom: statevector"}
        }),
        "summary": {
            "nodes_total": len(spec.nodes),
            "grounded": len(spec.nodes) if all_grounded else 0,
            "thin": 0 if all_grounded else 0,
            "unverified": 0 if all_grounded else len(spec.nodes),
            "corpus_live": len(spec.corpus) if source_status == "live" else 0,
            "corpus_dead": 0 if source_status == "live" else len(spec.corpus),
            "corpus_needs_render": 0,
            "corpus_total": len(spec.corpus),
            "structural_ok": structural_ok,
            "all_grounded": all_grounded and structural_ok,
        },
    }


# ---------- 1. automatic verified compilation (the default path) ----------
def test_auto_compile_when_verification_passes(tmp_path):
    spec = make_spec()
    report = make_report(spec)
    d = compile_spec(spec, report, out_root=str(tmp_path))
    assert d.status == COMPILED
    assert any("automatic verified compilation" in r for r in d.reasons)
    # artifacts: bundle (spec + receipts + audit) all written, local-first
    assert d.spec_path and os.path.exists(d.spec_path)
    assert d.receipt_path and os.path.exists(d.receipt_path)
    assert d.audit_path and os.path.exists(d.audit_path)
    # the bundle round-trips to the same spec
    assert yaml.safe_load(open(d.spec_path))["subject"] == spec.subject


def test_auto_compile_writes_receipts_with_evidence(tmp_path):
    spec = make_spec()
    report = make_report(spec)
    d = compile_spec(spec, report, out_root=str(tmp_path))
    r = json.load(open(d.receipt_path))
    # evidence receipts: measured, not self-assessed
    assert r["invariant"]["min_grounding_chars"] == 2500
    assert r["spec_content_hash"] == spec_content_hash(spec)
    assert r["summary"]["all_grounded"] is True
    assert r["corpus"][0]["status"] == "live"
    assert r["corpus"][0]["text_len"] == 26000
    assert r["nodes"][0]["grounding"]["ok"] is True
    assert r["nodes"][0]["oracle_ok"] is True
    assert r["oracle_outputs"]["fake_qubit"]["status"] == "ok"
    # audit trail: the decision is recorded with status + reasons + hash
    audit = [json.loads(line) for line in open(d.audit_path)]
    assert audit[-1]["status"] == COMPILED
    assert audit[-1]["spec_content_hash"] == spec_content_hash(spec)
    assert audit[-1]["verifier_summary"]["all_grounded"] is True


def test_auto_compile_needs_no_human_approval(tmp_path):
    """The policy: a passing curriculum compiles without anyone signing off —
    the decision names the policy, and there is no approval field anywhere."""
    spec = make_spec()
    d = compile_spec(spec, make_report(spec), out_root=str(tmp_path))
    blob = json.dumps(d.to_dict())
    assert '"approved"' not in blob and '"approval"' not in blob
    assert d.status == COMPILED


def test_auto_compile_is_deterministic(tmp_path):
    spec = make_spec()
    d1 = compile_spec(spec, make_report(spec), out_root=str(tmp_path))
    d2 = compile_spec(spec, make_report(spec), out_root=str(tmp_path))
    assert d1.status == d2.status == COMPILED
    assert d1.reasons == d2.reasons
    assert spec_content_hash(spec) == spec_content_hash(spec)


# ---------- 2. optional review mode -----------------------------------------
def test_review_mode_pauses_new_subject(tmp_path):
    spec = make_spec()
    d = compile_spec(spec, make_report(spec), out_root=str(tmp_path),
                     review=True, old_spec=None)
    assert d.status == PAUSED
    assert any("review mode: new subject" in r for r in d.reasons)
    # the pause is a review surface, not a fake pass nor a silent drop:
    # artifacts are still written, stamped paused, for the human
    r = json.load(open(d.receipt_path))
    assert r["decision"]["status"] == PAUSED
    assert r["decision"]["new_subject"] is True
    audit = [json.loads(line) for line in open(d.audit_path)]
    assert audit[-1]["status"] == PAUSED


def test_review_mode_shows_diff_receipts_status_and_audit(tmp_path):
    spec = make_spec()
    d = compile_spec(spec, make_report(spec), out_root=str(tmp_path),
                     review=True, old_spec=None)
    # new subject: no baseline to diff against — the diff names the new
    # subject and the pause is driven by new_subject (not diff.material)
    assert d.diff is not None
    assert d.new_subject is True
    assert "new subject" in d.diff.text_diff
    # receipts + status + audit trail all present and consistent
    r = json.load(open(d.receipt_path))
    assert r["decision"]["status"] == PAUSED
    assert r["decision"]["new_subject"] is True
    assert r["summary"]["all_grounded"] is True
    audit = [json.loads(line) for line in open(d.audit_path)]
    assert audit[-1]["status"] == PAUSED
    assert audit[-1]["new_subject"] is True


def test_review_mode_is_a_noop_when_nothing_material(tmp_path):
    """Review mode pauses only for new subjects or material changes — a
    re-verification of the same content compiles automatically even with
    --review (no approval theatre for an unchanged curriculum)."""
    spec = make_spec()
    d = compile_spec(spec, make_report(spec), out_root=str(tmp_path),
                     review=True, old_spec=spec)
    assert d.status == COMPILED


def test_review_mode_still_blocks_on_failure(tmp_path):
    """Review mode is an extra surface, never a rescue: a failing curriculum
    is blocked even with --review on (blocking outranks pausing)."""
    spec = make_spec()
    bad = make_report(spec, all_grounded=False, node_status="unverified",
                      source_status="dead")
    d = compile_spec(spec, bad, out_root=str(tmp_path), review=True)
    assert d.status == BLOCKED
    r = json.load(open(d.receipt_path))
    assert r["decision"]["status"] == BLOCKED


# ---------- 3. material-change detection ------------------------------------
def _node_copy(id_: str, **kw) -> Node:
    base = dict(id=id_, title="N", defn="A concept with real text.",
                covers_keywords=["qubit"], grounding_corpus=["src-a"],
                oracle="fake_qubit")
    base.update(kw)
    return Node(**base)


def test_material_change_added_node_is_material():
    old = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a")])
    new = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a"), _node_copy("b")])
    d = diff_specs(old, new)
    assert d.material is True
    assert d.added_nodes == ["b"] and not d.changed_nodes


def test_material_change_removed_and_changed_node():
    old = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u"),
                                CorpusSource(id="src-b", name="B", url="u2")],
                         nodes=[_node_copy("a"), _node_copy("b", oracle=None)])
    new = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a", defn="Changed definition text."),
                                _node_copy("b", oracle="fake_qubit")])
    d = diff_specs(old, new)
    assert d.removed_sources == ["src-b"]
    assert d.changed_nodes == ["a", "b"]
    assert d.material is True
    assert d.text_diff and "Changed definition text" in d.text_diff


def test_cosmetic_only_change_is_not_material():
    old = CurriculumSpec(subject="s", title="Old Title",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a")])
    new = CurriculumSpec(subject="s", title="New Title",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a")])
    d = diff_specs(old, new)
    assert d.material is False
    assert d.metadata_changed == ["title"]
    # still visible in review (the diff names it), just not a pause trigger
    assert "title" in d.metadata_changed


def test_reverification_of_same_content_is_not_a_change():
    """The hash/diff must ignore the verifier's verdict fields: re-running
    verification on an unchanged curriculum is not a material change."""
    base = make_spec()
    reverified = copy.deepcopy(base)
    reverified.nodes[0].status = "grounded"          # verifier fields
    reverified.nodes[0].verification = {"structural_ok": True, "grounding": {}}
    reverified.corpus[0].status = "live"             # verifier fields
    reverified.corpus[0].http_status = 200
    assert spec_content_hash(base) == spec_content_hash(reverified)
    d = diff_specs(base, reverified)
    assert d.material is False


def test_review_mode_pauses_material_change(tmp_path):
    old = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a")])
    new = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a"), _node_copy("b")])
    d = compile_spec(new, make_report(new), out_root=str(tmp_path),
                     review=True, old_spec=old)
    assert d.status == PAUSED
    assert any("material curriculum change" in r for r in d.reasons)
    assert d.diff is not None and d.diff.added_nodes == ["b"]
    # the diff is in the bundle, for the reviewer
    audit = [json.loads(line) for line in open(d.audit_path)]
    assert audit[-1]["diff"]["added_nodes"] == ["b"]


def test_material_change_auto_compiles_without_review(tmp_path):
    """P1.0 policy: a *verified* material change compiles automatically when
    review mode is off — it is flagged in the audit trail (diff + material),
    not hidden and not silently approved. Review mode is the opt-in pause."""
    old = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a")])
    new = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a"), _node_copy("b")])
    d = compile_spec(new, make_report(new), out_root=str(tmp_path),
                     review=False, old_spec=old)
    assert d.status == COMPILED
    assert d.material is True
    audit = [json.loads(line) for line in open(d.audit_path)]
    assert audit[-1]["material"] is True
    assert audit[-1]["diff"]["added_nodes"] == ["b"]


# ---------- 4. exception blocking (flag + pause, never silent) --------------
def test_blocks_on_failed_grounding(tmp_path):
    spec = make_spec()
    bad = make_report(spec, all_grounded=False, node_status="unverified")
    d = compile_spec(spec, bad, out_root=str(tmp_path))
    assert d.status == BLOCKED
    assert any("verification:" in r for r in d.reasons)
    assert any("node 'qubit': unverified" in r for r in d.reasons)
    # the failure is inspectable in the bundle, not masked
    r = json.load(open(d.receipt_path))
    assert r["decision"]["status"] == BLOCKED
    assert r["decision"]["reasons"] == d.reasons


def test_blocks_on_dead_source(tmp_path):
    spec = make_spec()
    bad = make_report(spec, all_grounded=False, node_status="thin",
                      source_status="dead")
    d = compile_spec(spec, bad, out_root=str(tmp_path))
    assert d.status == BLOCKED
    assert any("source 'src-a': dead" in r for r in d.reasons)
    assert any("HTTP 404" in r for r in d.reasons)


def test_blocks_on_needs_render_source(tmp_path):
    """An unresolvable SPA is ambiguous evidence about its content -> block."""
    spec = make_spec()
    bad = make_report(spec, all_grounded=False, node_status="unverified",
                      source_status="needs-render")
    bad["corpus"][0]["http_status"] = 200
    bad["corpus"][0]["text_len"] = 688
    bad["corpus"][0]["needs_render"] = True
    d = compile_spec(spec, bad, out_root=str(tmp_path))
    assert d.status == BLOCKED
    assert any("source 'src-a': needs-render" in r for r in d.reasons)


def test_blocks_on_oracle_failure(tmp_path):
    spec = make_spec()
    bad = make_report(spec, all_grounded=False, node_status="unverified",
                      oracle_ok=False)
    bad["nodes"][0]["oracle_ok"] = False
    d = compile_spec(spec, bad, out_root=str(tmp_path))
    assert d.status == BLOCKED
    assert any("node 'qubit': unverified (oracle ok=False)" in r
               for r in d.reasons)


def test_blocks_on_structural_cycle(tmp_path):
    spec = make_spec()
    bad = make_report(spec, all_grounded=False, structural_ok=False,
                      structural_errors={"__dag__": "cycle detected"})
    d = compile_spec(spec, bad, out_root=str(tmp_path))
    assert d.status == BLOCKED
    assert any("structural:" in r for r in d.reasons)
    assert any("cycle detected" in r for r in d.reasons)


def test_blocks_on_unsafe_change_dropped_oracle(tmp_path):
    old = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a", oracle="fake_qubit")])
    new = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a", oracle=None)])
    d = compile_spec(new, make_report(new), out_root=str(tmp_path),
                     old_spec=old)
    assert d.status == BLOCKED
    assert any("unsafe change" in r and "dropped oracle" in r
               for r in d.reasons)
    # unsafe beats review mode: --review cannot launder a weakening
    d2 = compile_spec(new, make_report(new), out_root=str(tmp_path),
                      review=True, old_spec=old)
    assert d2.status == BLOCKED


def test_blocks_on_unsafe_change_dropped_source(tmp_path):
    old = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u"),
                                CorpusSource(id="src-b", name="B", url="u2")],
                         nodes=[_node_copy("a", grounding_corpus=["src-a", "src-b"])])
    new = CurriculumSpec(subject="s", title="T",
                         corpus=[CorpusSource(id="src-a", name="A", url="u")],
                         nodes=[_node_copy("a")])
    d = compile_spec(new, make_report(new), out_root=str(tmp_path),
                     old_spec=old)
    assert d.status == BLOCKED
    assert any("unsafe change" in r for r in d.reasons)


def test_blocks_on_corrupt_prior_bundle(tmp_path):
    spec = make_spec()
    old_path = tmp_path / "old" / "broken.yaml"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("::: not: [valid: yaml", encoding="utf-8")
    d = compile_spec(spec, make_report(spec), out_root=str(tmp_path),
                     old_spec_path=str(old_path))
    assert d.status == BLOCKED
    assert any("corrupt prior state" in r for r in d.reasons)


def test_blocks_when_prior_bundle_missing(tmp_path):
    spec = make_spec()
    missing = tmp_path / "does-not-exist.yaml"
    d = compile_spec(spec, make_report(spec), out_root=str(tmp_path),
                     old_spec_path=str(missing))
    assert d.status == BLOCKED
    assert any("old spec not found" in r for r in d.reasons)


def test_load_last_spec_roundtrip_and_missing(tmp_path):
    spec = make_spec()
    compile_spec(spec, make_report(spec), out_root=str(tmp_path))
    loaded = load_last_spec(str(tmp_path), spec.subject)
    assert loaded is not None
    assert loaded.subject == spec.subject
    assert spec_content_hash(loaded) == spec_content_hash(spec)
    assert load_last_spec(str(tmp_path), "no-such-subject") is None


def test_audit_trail_accumulates(tmp_path):
    spec = make_spec()
    compile_spec(spec, make_report(spec), out_root=str(tmp_path))
    d2 = compile_spec(spec, make_report(spec), out_root=str(tmp_path),
                      review=True, old_spec=spec)
    assert d2.status == COMPILED
    lines = [json.loads(line) for line in open(
        str(tmp_path / "curriculum" / "audit.log.jsonl"))]
    assert len(lines) == 2
    assert lines[0]["status"] == COMPILED and lines[1]["status"] == COMPILED


# ---------- 5. invariants preserved -----------------------------------------
def test_min_grounding_chars_is_untouched():
    assert MIN_GROUNDING_CHARS == 2500
    assert compiler.MIN_GROUNDING_CHARS is MIN_GROUNDING_CHARS
    assert compiler.INVIOLABLE_BARS["min_grounding_chars"] == 2500


def test_compiler_is_non_llm_and_report_driven():
    """The decision is a pure function of the verifier report + specs — feed it
    two identical passing reports, get two identical decisions; flip one
    report field, the decision changes. No model, no randomness."""
    spec = make_spec()
    good = make_report(spec)
    d1 = compiler.decide(good, None, spec)
    d2 = compiler.decide(good, None, spec)
    assert (d1.status, d1.reasons) == (d2.status, d2.reasons) == (COMPILED, d1.reasons)
    bad = make_report(spec, all_grounded=False, node_status="unverified")
    d3 = compiler.decide(bad, None, spec)
    assert d3.status == BLOCKED


def test_bundle_roundtrip_preserves_curriculum(tmp_path):
    """The compiled bundle is the spec of record: load it back and the
    canonical content (nodes, sources, wiring) is intact."""
    spec = make_spec()
    d = compile_spec(spec, make_report(spec), out_root=str(tmp_path))
    from open_tutor.spec import load_yaml
    back = load_yaml(d.spec_path)
    assert back.subject == spec.subject
    assert [n.id for n in back.nodes] == [n.id for n in spec.nodes]
    assert [c.id for c in back.corpus] == [c.id for c in spec.corpus]
    assert back.nodes[0].oracle == "fake_qubit"
    assert back.nodes[0].grounding_corpus == spec.nodes[0].grounding_corpus
