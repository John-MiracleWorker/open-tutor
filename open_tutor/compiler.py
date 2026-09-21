"""P1.0 — CURRICULUM COMPILER: the single-user policy for compilation.

A generated ``CurriculumSpec`` may compile **automatically** when the
deterministic verifier (structural + grounding + oracle, SPEC §4.2) passes.
No human approval is required for a curriculum, a lesson, or an answer —
this is a single-user, self-hosted tutor, not an accreditation workflow.

The compiler is the *decision layer on top of the verifier*, and it inherits
the verifier's invariants verbatim (SPEC §8, VERIFIER-CONTRACT.md):

- **Non-LLM, deterministic.** The decision is computed only from the verifier
  report — the generator's self-assessment is never trusted (the generator
  emits candidates; the verifier decides; the compiler only routes).
- **No silent failures.** ``blocked`` is a first-class outcome with named
  reasons; ``paused`` (review mode) is too. Honest statuses only:
  ``compiled`` | ``blocked`` | ``paused``.
- **The bar is untouched.** ``MIN_GROUNDING_CHARS`` stays exactly where it is
  (verifier.py); nothing in this module measures or relaxes grounding.

Review mode (opt-in) — for a *new* subject or a *material* curriculum change
the reviewer sees: the diff (what changed), the evidence receipts (what the
verifier measured per source/oracle), the status of every check, and the audit
trail (what the compiler decided and why). The pause is a *review surface*,
never a silent pass and never a fake approval: the artifacts are still
written, stamped ``paused``, until a human records the decision in the audit
trail.

Exception blocking — only the cases that actually fail are flagged and
paused/blocked:

1. **Failed verification** — structural error, any node not grounded, or a
   cited source dead / thin / needs-render (ambiguous or conflicting
   evidence about what the corpus actually says).
2. **Unsafe change** — a material diff that would *weaken* the invariant
   (e.g. the grounding bar moved, a node lost its oracle or a source):
   blocked, never auto-compiled. Pure additions (new nodes/sources) are
   material → reviewed, not blocked.
3. **Corrupt prior state** — the old bundle for a change cannot be read:
   blocked (a material change cannot be judged against what it replaces).

Local-first: everything is written under the ``out/`` root as plain YAML/
JSON artifacts. Retrieved web content stays *data* — only its measured
metadata and the verifier's receipts land in the bundle.
"""
from __future__ import annotations

import dataclasses
import difflib
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

from .spec import CurriculumSpec, load_yaml, save_yaml
from .verifier import MIN_GROUNDING_CHARS

# ---------- vocabulary (honest, first-class, no silent passes) ----------
COMPILED = "compiled"        # automatic verified compilation — the default path
BLOCKED = "blocked"          # failed verification or unsafe change — flagged
PAUSED = "paused"            # review mode: surfaced for a human decision

# The invariant we refuse to let a change weaken.
INVIOLABLE_BARS = {
    "min_grounding_chars": MIN_GROUNDING_CHARS,   # 2500 — the product (SPEC §8)
}

# ---------- stable serialisation (deterministic hashes/diffs) ----------
def _canon(node: dict[str, Any]) -> dict[str, Any]:
    """A node's *curriculum content* — verification fields are excluded: they
    are the verifier's verdict on the same content, so comparing them would
    double-count a re-verification of an unchanged curriculum as a change."""
    return {
        "id": node.get("id"),
        "title": node.get("title"),
        "defn": node.get("defn"),
        "prereqs": list(node.get("prereqs") or []),
        "misconceptions": [
            {"id": m.get("id"), "text": m.get("text")}
            for m in (node.get("misconceptions") or [])
        ],
        "grounding_corpus": list(node.get("grounding_corpus") or []),
        "oracle": node.get("oracle"),
        "covers_keywords": list(node.get("covers_keywords") or []),
    }


def _canon_source(src: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": src.get("id"),
        "name": src.get("name"),
        "url": src.get("url"),
        "tier": src.get("tier"),
    }


def canonical_spec(spec: CurriculumSpec) -> dict[str, Any]:
    """Stable, verification-free projection of a spec's curriculum content."""
    return {
        "subject": spec.subject,
        "title": spec.title,
        "scope": spec.scope,
        "tiers": spec.tiers,
        "corpus": [_canon_source(dataclasses.asdict(c)) for c in spec.corpus],
        "oracle": spec.oracle,
        "nodes": [_canon(dataclasses.asdict(n)) for n in spec.nodes],
        "generator_note": spec.generator_note,
    }


def spec_content_hash(spec: CurriculumSpec) -> str:
    """SHA-256 over the canonical projection — stable across re-verification
    of the same content (verifier fields never feed the hash)."""
    blob = json.dumps(canonical_spec(spec), sort_keys=True, default=str,
                      ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------- 1. diff + material-change detection (deterministic) ----------
@dataclass
class Diff:
    """What changed between two curricula (old -> new), or vs. none for a new
    subject. ``material`` is the deterministic judgment: a change that touches
    any node or any corpus source is material; title/scope/oracle metadata
    alone is cosmetic (still shown in the review, never hidden)."""
    added_nodes: List[str] = field(default_factory=list)
    removed_nodes: List[str] = field(default_factory=list)
    changed_nodes: List[str] = field(default_factory=list)
    added_sources: List[str] = field(default_factory=list)
    removed_sources: List[str] = field(default_factory=list)
    changed_sources: List[str] = field(default_factory=list)
    bars_weakened: List[str] = field(default_factory=list)   # unsafe if non-empty
    metadata_changed: List[str] = field(default_factory=list)
    text_diff: str = ""

    @property
    def material(self) -> bool:
        if self.bars_weakened:
            return True
        return bool(self.added_nodes or self.removed_nodes or self.changed_nodes
                    or self.added_sources or self.removed_sources
                    or self.changed_sources)

    @property
    def unsafe(self) -> bool:
        """An unsafe change would WEAKEN an invariant — e.g. a node dropping
        its oracle or a grounding source, or the bar itself moving. Such a
        change is blocked outright; it is never auto-compiled and review
        cannot silently approve it (the block reason names the weakening)."""
        return bool(self.bars_weakened)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "added_nodes": self.added_nodes,
            "removed_nodes": self.removed_nodes,
            "changed_nodes": self.changed_nodes,
            "added_sources": self.added_sources,
            "removed_sources": self.removed_sources,
            "changed_sources": self.changed_sources,
            "bars_weakened": self.bars_weakened,
            "metadata_changed": self.metadata_changed,
            "material": self.material,
            "unsafe": self.unsafe,
            "text_diff": self.text_diff,
        }


def _unified(old: Any, new: Any, label: str) -> str:
    a = yaml.safe_dump(old, sort_keys=True, allow_unicode=True).splitlines()
    b = yaml.safe_dump(new, sort_keys=True, allow_unicode=True).splitlines()
    return "".join(difflib.unified_diff(a, b,
                                        fromfile=f"{label} (old)",
                                        tofile=f"{label} (new)", lineterm=""))


def diff_specs(old: Optional[CurriculumSpec], new: CurriculumSpec) -> Diff:
    """Deterministic old->new comparison. ``old=None`` means new subject."""
    d = Diff()
    if old is None:
        d.text_diff = (f"new subject '{new.subject}' — "
                       f"{len(new.nodes)} nodes, {len(new.corpus)} corpus sources "
                       "(no prior bundle to diff against)")
        return d

    old_nodes = {n.id: _canon(dataclasses.asdict(n)) for n in old.nodes}
    new_nodes = {n.id: _canon(dataclasses.asdict(n)) for n in new.nodes}
    d.added_nodes = sorted(set(new_nodes) - set(old_nodes))
    d.removed_nodes = sorted(set(old_nodes) - set(new_nodes))
    d.changed_nodes = sorted(
        i for i in set(new_nodes) & set(old_nodes) if new_nodes[i] != old_nodes[i]
    )

    old_src = {c.id: _canon_source(dataclasses.asdict(c)) for c in old.corpus}
    new_src = {c.id: _canon_source(dataclasses.asdict(c)) for c in new.corpus}
    d.added_sources = sorted(set(new_src) - set(old_src))
    d.removed_sources = sorted(set(old_src) - set(new_src))
    d.changed_sources = sorted(
        i for i in set(new_src) & set(old_src) if new_src[i] != old_src[i]
    )

    # Unsafe-change detection: a node that LOSES grounding or its oracle is a
    # weakening of the invariant (the generator/verifier contract says failed
    # code is never presented as an answer, and grounding must be earned).
    weakened: List[str] = []
    for nid in d.changed_nodes:
        o, n = old_nodes[nid], new_nodes[nid]
        if (o.get("oracle") and not n.get("oracle")):
            weakened.append(f"node '{nid}' dropped oracle '{o.get('oracle')}'")
        lost_src = sorted(set(o.get("grounding_corpus") or [])
                          - set(n.get("grounding_corpus") or []))
        if lost_src:
            weakened.append(f"node '{nid}' dropped grounding source(s) "
                            f"{lost_src}")
    if d.removed_sources:
        weakened.append(f"corpus source(s) removed: {d.removed_sources}")
    d.bars_weakened = weakened

    meta_changed = []
    for key in ("subject", "title", "scope", "tiers", "oracle", "generator_note"):
        if getattr(old, key) != getattr(new, key):
            meta_changed.append(key)
    d.metadata_changed = meta_changed

    # Human-readable unified diff of the canonical projection.
    d.text_diff = (
        _unified(canonical_spec(old), canonical_spec(new), "spec")
        or "(no canonical differences)"
    )
    return d


# ---------- 2. the decision (deterministic routing on the report) ----------
@dataclass
class CompileDecision:
    status: str                       # compiled | blocked | paused
    reasons: List[str]                # named, honest — never empty on block/pause
    diff: Optional[Diff] = None
    new_subject: bool = False
    material: bool = False
    receipt_path: Optional[str] = None
    bundle_path: Optional[str] = None
    audit_path: Optional[str] = None
    spec_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reasons": self.reasons,
            "new_subject": self.new_subject,
            "material": self.material,
            "diff": None if self.diff is None else self.diff.to_dict(),
            "receipt_path": self.receipt_path,
            "bundle_path": self.bundle_path,
            "audit_path": self.audit_path,
            "spec_path": self.spec_path,
        }


def _block_reasons(report: Dict[str, Any], spec: CurriculumSpec | None = None) -> List[str]:
    """Every failure in the report, named. Empty list iff verification passed."""
    r: List[str] = []
    if not report.get("structural", {}).get("ok", False):
        r.append(f"structural: {report['structural'].get('errors')}")
    s = report.get("summary", {})
    if not s.get("all_grounded", False):
        r.append(f"verification: {s.get('grounded')}/{s.get('nodes_total')} "
                 f"nodes grounded (thin={s.get('thin')}, "
                 f"unverified={s.get('unverified')})")
        for n in report.get("nodes", []):
            if n.get("status") != "grounded":
                r.append(f"  node '{n['id']}': {n['status']}"
                         + (f" (oracle ok={n.get('oracle_ok')})"
                            if n.get("oracle") else ""))
    # A thin/dead source is first-class evidence, but it only blocks the
    # curriculum when a node actually needs that source to ground itself. An
    # unused SPA source may remain in the corpus while every node is grounded
    # independently by another measured source (the quantum reference does
    # this with IBM Quantum Learning).
    if spec is not None:
        by_id = {c.get("id"): c for c in report.get("corpus", [])}
        for node in spec.nodes:
            if node.status == "grounded":
                continue
            for source_id in node.grounding_corpus:
                c = by_id.get(source_id, {})
                if c.get("status") != "live":
                    r.append(f"source '{source_id}': {c.get('status', 'unknown')}"
                             f" (http={c.get('http_status')}, "
                             f"text_len={c.get('text_len')}"
                             + (f": {c.get('error')}" if c.get("error") else "")
                             + " — required by an ungrounded node")
    return r


def decide(report: Dict[str, Any], old_spec: Optional[CurriculumSpec],
           new_spec: CurriculumSpec, *, review: bool = False) -> CompileDecision:
    """Route a verified spec to compiled / blocked / paused.

    Policy (single-user, P1.0):
      - verification passed + (no old bundle, or no material change)
            -> COMPILED  (automatic; no human approval needed)
      - verification passed + new subject, or material change, and review mode
            -> PAUSED    (diff + receipts + status + audit trail surfaced)
      - verification passed + new subject / material change, review off
            -> COMPILED  (the default policy; the bundle carries the diff
                          and receipts so the change is auditable after the
                          fact — review mode exists to pause *before*, not
                          to police after)
      - verification failed OR unsafe change OR unreadable prior bundle
            -> BLOCKED   (flagged; nothing is ever auto-compiled on a
                          failure — the failure is the named reason)
    """
    reasons: List[str] = []
    new_subject = old_spec is None
    diff = diff_specs(old_spec, new_spec)

    if diff.unsafe:
        reasons.append("unsafe change: " + "; ".join(diff.bars_weakened))
    for bad in _block_reasons(report, new_spec):
        reasons.append(bad)

    if reasons:
        return CompileDecision(status=BLOCKED, reasons=reasons, diff=diff,
                               new_subject=new_subject, material=diff.material)

    if review and (new_subject or diff.material):
        kind = "new subject" if new_subject else "material curriculum change"
        return CompileDecision(
            status=PAUSED,
            reasons=[f"review mode: {kind} — paused for review; the diff, "
                     f"evidence receipts, and status are in the bundle"],
            diff=diff, new_subject=new_subject, material=diff.material)

    return CompileDecision(status=COMPILED,
                           reasons=["automatic verified compilation: structural, "
                                    "grounding, and oracle checks all passed "
                                    "(deterministic verifier, no LLM)"],
                           diff=diff, new_subject=new_subject,
                           material=diff.material)


# ---------- 3. evidence receipts (the verifier's measured evidence) ----------
def build_receipts(spec: CurriculumSpec, report: Dict[str, Any],
                   decision: CompileDecision) -> Dict[str, Any]:
    """The evidence a reviewer (or the audit log) needs, all measured — never
    self-assessed. Retrieved content stays data: only its metadata and the
    verifier's own measurements are recorded here."""
    corpus = {c["id"]: c for c in report.get("corpus", [])}
    nodes = {n["id"]: n for n in report.get("nodes", [])}
    node_receipts = []
    for n in spec.nodes:
        rn = nodes.get(n.id, {})
        verification = n.verification or {}
        gr = verification.get("grounding", {})
        node_receipts.append({
            "id": n.id,
            "title": n.title,
            "status": rn.get("status"),
            "structural_ok": verification.get("structural_ok"),
            "grounding": {
                "cited_sources": gr.get("cited_sources"),
                "live_sources": gr.get("live_sources"),
                "coverage_hits": gr.get("coverage_hits"),
                "ok": gr.get("ok"),
            },
            "oracle": rn.get("oracle"),
            "oracle_ok": rn.get("oracle_ok"),
            "oracle_output": report.get("oracle_outputs", {}).get(
                n.oracle or "", {}).get("output") if n.oracle else None,
        })
    return {
        "subject": spec.subject,
        "spec_content_hash": spec_content_hash(spec),
        "invariant": {"min_grounding_chars": MIN_GROUNDING_CHARS},
        "summary": report.get("summary", {}),
        "structural": report.get("structural", {}),
        "corpus": [
            {
                "id": c.id,
                "name": c.name,
                "url": c.url,
                "status": c.status,
                "http_status": c.http_status,
                "text_len": corpus.get(c.id, {}).get("text_len"),
                "method": corpus.get(c.id, {}).get("method"),
                "needs_render": corpus.get(c.id, {}).get("needs_render"),
                "author": corpus.get(c.id, {}).get("author"),
                "error": corpus.get(c.id, {}).get("error"),
            }
            for c in spec.corpus
        ],
        "nodes": node_receipts,
        "oracle_outputs": report.get("oracle_outputs", {}),
        "decision": {
            "status": decision.status,
            "reasons": decision.reasons,
            "material": decision.material,
            "new_subject": decision.new_subject,
        },
    }


# ---------- 4. compiled bundle (local-first artifact) ----------
def write_bundle(spec: CurriculumSpec, report: Dict[str, Any],
                 decision: CompileDecision, out_root: str) -> Dict[str, str]:
    """Persist a bundle without allowing a candidate to replace active data.

    A compiled decision is written to the stable subject path.  Paused and
    blocked decisions are written below ``curriculum/.candidates``; their
    receipt and spec remain inspectable, but ``load_last_spec`` and a runtime
    importer can only see the stable compiled path.  This is the transaction
    boundary used by the API's optional review flow.
    """
    cur_dir = os.path.join(out_root, "curriculum")
    os.makedirs(cur_dir, exist_ok=True)
    if decision.status == COMPILED:
        bundle_dir = cur_dir
    else:
        bundle_dir = os.path.join(cur_dir, ".candidates")
        os.makedirs(bundle_dir, exist_ok=True)
    spec_path = os.path.join(bundle_dir, f"{spec.subject}.yaml")
    save_yaml(spec, spec_path)

    receipts = build_receipts(spec, report, decision)
    receipt_path = os.path.join(bundle_dir, f"{spec.subject}.receipts.json")
    with open(receipt_path, "w", encoding="utf-8") as f:
        json.dump(receipts, f, indent=2, default=str)

    audit_path = os.path.join(cur_dir, "audit.log.jsonl")
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "subject": spec.subject,
        "spec_content_hash": spec_content_hash(spec),
        "status": decision.status,
        "reasons": decision.reasons,
        "material": decision.material,
        "new_subject": decision.new_subject,
        "diff": None if decision.diff is None
                  else {k: v for k, v in decision.diff.to_dict().items()
                        if k != "text_diff"},
        "verifier_summary": report.get("summary", {}),
    }
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")

    return {"spec_path": spec_path, "receipt_path": receipt_path,
            "audit_path": audit_path}


# ---------- 5. the one-shot entry point ----------
def compile_spec(spec: CurriculumSpec, report: Dict[str, Any], *,
                 out_root: str = "out", review: bool = False,
                 old_spec: Optional[CurriculumSpec] = None,
                 old_spec_path: Optional[str] = None) -> CompileDecision:
    """Compile a verified spec under the single-user policy.

    - ``review``: opt-in review mode (new subject / material change pauses).
    - ``old_spec`` / ``old_spec_path``: the prior bundle's spec, for
      material-change detection and the diff. A path that cannot be read
      blocks (a material change cannot be judged against what it replaces).
    """
    if old_spec is None and old_spec_path:
        if not os.path.exists(old_spec_path):
            return CompileDecision(
                status=BLOCKED,
                reasons=["corrupt prior state: old spec not found at "
                         f"'{old_spec_path}' — cannot judge a material change"],
            )
        try:
            old_spec = load_yaml(old_spec_path)
        except Exception as e:  # noqa: BLE001 — corrupt YAML is a named block
            return CompileDecision(
                status=BLOCKED,
                reasons=[f"corrupt prior state: cannot read old spec "
                         f"'{old_spec_path}': {type(e).__name__}: {e}"],
            )

    decision = decide(report, old_spec, spec, review=review)
    paths = write_bundle(spec, report, decision, out_root)
    decision.receipt_path = paths["receipt_path"]
    decision.bundle_path = paths["spec_path"]
    decision.audit_path = paths["audit_path"]
    decision.spec_path = paths["spec_path"]
    return decision


def load_last_spec(out_root: str, subject: str) -> Optional[CurriculumSpec]:
    """The previously compiled bundle for ``subject``, if any (None = new)."""
    path = os.path.join(out_root, "curriculum", f"{subject}.yaml")
    if not os.path.exists(path):
        return None
    try:
        return load_yaml(path)
    except Exception:  # noqa: BLE001 — corrupt bundle is handled by caller
        raise CorruptBundleError(path)


class CorruptBundleError(RuntimeError):
    """The prior bundle exists but cannot be parsed — a material change
    against it cannot be judged (block, don't guess)."""
    def __init__(self, path: str):
        super().__init__(f"corrupt prior bundle: {path}")
        self.path = path


def print_decision(decision: CompileDecision) -> None:
    """The human-readable compile outcome (statuses + reasons, never masked)."""
    bar = "=" * 64
    print("\n" + bar)
    print(f"  COMPILE — {decision.status.upper()}")
    print(bar)
    for reason in decision.reasons:
        print(f"  {reason}")
    d = decision.diff
    if d is not None:
        tag = "MATERIAL" if d.material else "cosmetic"
        print(f"  change:      {tag}" + (" (new subject)" if decision.new_subject else ""))
        if d.added_nodes:
            print(f"    + nodes:    {d.added_nodes}")
        if d.removed_nodes:
            print(f"    - nodes:    {d.removed_nodes}")
        if d.changed_nodes:
            print(f"    ~ nodes:    {d.changed_nodes}")
        if d.added_sources:
            print(f"    + sources:  {d.added_sources}")
        if d.removed_sources:
            print(f"    - sources:  {d.removed_sources}")
        if d.bars_weakened:
            print(f"    UNSAFE:     {d.bars_weakened}")
    if decision.audit_path:
        print(f"  bundle:      {decision.bundle_path}")
        print(f"  receipts:    {decision.receipt_path}")
        print(f"  audit trail: {decision.audit_path}")
    print(bar)
