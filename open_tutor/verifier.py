"""The VERIFIER — the load-bearing, honesty-enforcing half (SPEC §4.2).

Three deterministic, non-LLM checks per node:
  1. STRUCTURAL — prereqs exist, DAG acyclic, node has a definition.
  2. GROUNDING — every cited corpus source is LIVE (HTTP 200 + non-empty), and
                 the retrieved page text actually covers the node (keyword hit).
  3. EXECUTABILITY — if the node is quantitative and has an oracle, the oracle
                     RUNS and its output is captured.

A node is grounded iff all applicable checks pass. Failures are REPORTED
(thin/unverified), never silently passed — the honesty guarantee (SPEC §8).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from .spec import CurriculumSpec, Node

UA = {"User-Agent": "open-tutor-verify/0.1 (research)"}


# ---------- 1. structural ----------

def structural_checks(spec: CurriculumSpec) -> dict[str, str]:
    """node_id -> first structural error, or absent if OK."""
    errors: dict[str, str] = {}
    if not spec.nodes:
        errors["__dag__"] = "empty node graph"
    ids = {n.id for n in spec.nodes}
    counts: dict[str, int] = {}
    for n in spec.nodes:
        counts[n.id] = counts.get(n.id, 0) + 1
    duplicates = sorted(nid for nid, count in counts.items() if count > 1)
    if duplicates:
        errors["__ids__"] = "duplicate node id(s): " + ", ".join(duplicates)
    corpus_ids = {c.id for c in spec.corpus}
    for n in spec.nodes:
        if not n.id.strip():
            errors[n.id] = errors.get(n.id, "") + " empty node id;"
        if not n.defn.strip():
            errors[n.id] = "missing definition"
        for p in n.prereqs:
            if p not in ids:
                errors[n.id] = errors.get(n.id, "") + f" unknown prereq '{p}';"
        for source_id in n.grounding_corpus:
            if source_id not in corpus_ids:
                errors[n.id] = (errors.get(n.id, "")
                                + f" unknown corpus source '{source_id}';")
    if _has_cycle(spec):
        errors.setdefault("__dag__", "cycle detected")
    return errors


def _has_cycle(spec: CurriculumSpec) -> bool:
    adj = {n.id: list(n.prereqs) for n in spec.nodes}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {k: WHITE for k in adj}

    def dfs(u: str) -> bool:
        color[u] = GRAY
        for v in adj.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    return any(color[u] == WHITE and dfs(u) for u in list(adj))


# ---------- 2. grounding (live source + coverage) ----------

def fetch_live(url: str, timeout: int = 12,
               render_backend: Any = None) -> dict[str, Any]:
    """Fetch + extract a URL via the scraper ladder; return the verdict.

    Ladder: trafilatura → html-strip → (P0.5) browser render. The render rung
    runs ONLY when the static rungs mark the page `needs_render`, and the
    rendered body is measured exactly like any other — a thin render stays
    `needs-render`, it is never promoted past `MIN_GROUNDING_CHARS`.

    `render_backend` injects a `RenderBackend` callable (scraper.RenderBackend)
    for the render rung; when omitted, the process-wide backend is used
    (Playwright/Chromium if available, else an honest no-op).
    """
    from .scraper import browser_render, extract
    ex = extract(url, timeout=timeout)
    if ex.needs_render and not ex.ok:
        # Ladder rung 3: the static rungs say "SPA suspected" — render once.
        #
        # Adoption rule — the render result only replaces the static
        # measurement when it is a *better* measurement:
        #  - r.ok: rendered body is substantive (>= 200 chars) -> adopt it as
        #    the source's text. Status becomes `live` per the contract
        #    vocabulary — but MIN_GROUNDING_CHARS is enforced separately by
        #    `coverage()`, so a render under the bar is measured, reported,
        #    and still cannot ground a node (same precedent as the 1604-char
        #    `nielsen-chuang` source).
        #  - r.needs_render with real text: rendered but still thin
        #    (marketing shell) -> adopt the honest measured count, keep flag.
        #  - no browser available (empty result): keep the static rung's
        #    measured chars — never replace a real measurement with nothing.
        r = browser_render(url, backend=render_backend)
        if r.ok or (r.needs_render and r.body_len > 0):
            ex = r
    return {
        "status": ex.http_status,
        "ok": ex.ok and ex.body_len >= 200,
        "text_len": ex.body_len,
        "text": ex.body.lower(),
        "method": ex.method,
        "needs_render": ex.needs_render,
        "title": ex.title,
        "author": ex.author,
        "error": ex.error,
    }


MIN_GROUNDING_CHARS = 2500   # a grounding source must yield this much real text


def coverage(node: Node, corpus_text: dict[str, str]) -> list[str]:
    """Cited sources that are substantive AND contain the node's keywords.

    A thin landing page (e.g. an SPA home that renders < MIN_GROUNDING_CHARS)
    does NOT count — that is the whole point of the scraper tier: 'HTTP 200'
    is not 'grounded'.
    """
    kws = [k.lower() for k in node.covers_keywords if k]
    hits: list[str] = []
    for src_id in node.grounding_corpus:
        text = (corpus_text.get(src_id) or "").strip()
        if len(text) >= MIN_GROUNDING_CHARS and any(k in text for k in kws):
            hits.append(src_id)
    return hits


# ---------- 3. executability ----------

def run_oracle(name: str) -> dict[str, Any]:
    from . import oracles
    if name not in oracles.REGISTRY:
        return {"ok": False, "error": f"unknown oracle '{name}'"}
    try:
        result = oracles.REGISTRY[name]()
        return {"ok": True, "result": result}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def save_oracle_output(name: str, payload: dict[str, Any], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return path


# ---------- 4. answer gate (runtime form of the contract, P1.2) ----------

# The draft is the claim surface. Anything above this line — the question echo,
# the resolution metadata, the verbatim T2/T3 evidence sections — is context the
# engine produced, not a factual claim the draft makes.
_CLAIM_ANCHOR = "## Claim"
_CLAIM_PREFIX = "## Citations"

# Values the engine may quote in a quantitative claim: any numeric token that
# appears in the oracle's own key labels (e.g. basis labels like `P(|0>)`) or
# in its scalar/string values. Matching is exact, string-level — never fuzzy,
# never numeric-tolerance games: a number is either the oracle's or it is not.
def _oracle_allow(oracle_result: dict[str, Any]) -> set:
    allow = set()
    for k, v in (oracle_result or {}).items():
        for tok in re.findall(r"\d+(?:\.\d+)?", str(k)):
            allow.add(tok)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            allow.add(repr(float(v)))
            allow.add(str(v))
        elif isinstance(v, str):
            for tok in re.findall(r"\d+(?:\.\d+)?", v):
                allow.add(tok)
    return allow


def _answer_claims(draft: str) -> str:
    """The part of the draft the gate holds the drafter to: the claim section
    and the inline [n] citations it contains. Absent => no claims (the engine
    then reports the answer unverified rather than passing it on silence)."""
    i = draft.find(_CLAIM_ANCHOR)
    if i < 0:
        return ""
    j = draft.find(_CLAIM_PREFIX, i)
    return draft[i:j if j > 0 else len(draft)]


def _claim_lines(claims_text: str) -> list[str]:
    """The claim lines of the section — the claim units the gate checks.

    The section header itself (``## Claim``) is excluded: it labels the
    section, it is not a claim. Line-based (not sentence-based): an inline
    [n] marker anywhere in a line certifies that line, and periods inside
    quoted T2 spans (e.g. “…system. …” [1]) cannot split a line into falsely
    uncited fragments."""
    return [ln for ln in claims_text.splitlines()
            if ln.strip() and ln.strip() != _CLAIM_ANCHOR]


def _claims_without_citation(claims_text: str) -> list[str]:
    """Claim lines carrying no inline [n] marker. Deterministic,
    order-preserving."""
    return [ln for ln in _claim_lines(claims_text) if not re.search(r"\[\d+\]", ln)]


def _strip_quoted_spans(text: str) -> str:
    """Drop every ``“…”`` / ``\"...\"`` / ``'...'`` quoted run from ``text``,
    keeping only the drafter's own (unquoted) prose.

    Quoted runs are VERBATIM T2/T3 data: they can legitimately contain their
    own bracket markers (a wiki citation like ``[16]``) or numbers that are
    part of the source, not the drafter's claims. Counting them as the
    drafter's inline citations would make a valid, fully-cited answer fail
    the gate — the exact false negative this helper prevents."""
    return re.sub(r'“[^”]*”|"[^"]*"|\'[^\']*\'', "", text)


def _normalise_claim_text(text: str) -> str:
    """Normalize only presentation whitespace for exact evidence matching."""
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _citation_field(citation: Any, name: str, default: Any = None) -> Any:
    if isinstance(citation, dict):
        return citation.get(name, default)
    return getattr(citation, name, default)


def _quoted_parts(text: str) -> list[str]:
    return [part for part in re.findall(r"“([^”]*)”|\"([^\"]*)\"|'([^']*)'", text)
            for part in part if part]


def _citation_supports_claim(claim: str, marker_ids: set[int], citations: list[Any]) -> bool:
    """Require an exact excerpt in one of the cited spans.

    A citation marker is an index, not evidence by itself. The only accepted
    prose support is an exact quoted excerpt or an exact claim string found in
    the cited span. This deliberately rejects semantic paraphrases: a cheap
    deterministic verifier cannot establish their meaning.
    """
    if not marker_ids:
        return False
    evidence = [citations[i - 1] for i in sorted(marker_ids)
                if 1 <= i <= len(citations)]
    normalized_claim = _normalise_claim_text(re.sub(r"\[\d+\]", "", claim))
    quoted = [_normalise_claim_text(part) for part in _quoted_parts(claim)]
    for citation in evidence:
        span = _normalise_claim_text(_citation_field(citation, "text", ""))
        if not span:
            continue
        if any(part and part in span for part in quoted):
            return True
        # Exact unquoted excerpts are allowed for short, extractive claims. A
        # wrapper such as "The source states:" is not evidence and therefore
        # will not match.
        if normalized_claim and normalized_claim in span:
            return True
    return False


def _definition_supports_claim(claim: str, spec: CurriculumSpec) -> bool:
    """Allow exact excerpts from the selected T1 definition as canonical support."""
    clean = _normalise_claim_text(re.sub(r"\[\d+\]", "", claim))
    quoted = [_normalise_claim_text(part) for part in _quoted_parts(claim)]

    def prefix_excerpt(part: str, definition: str) -> bool:
        part_tokens = re.findall(r"[a-z0-9]+", part.casefold())
        def_tokens = re.findall(r"[a-z0-9]+", definition.casefold())
        return len(part_tokens) >= 4 and def_tokens[:len(part_tokens)] == part_tokens

    return any(
        (quoted and any(part and (
            part in _normalise_claim_text(node.defn)
            or prefix_excerpt(part, node.defn)
        ) for part in quoted))
        or (clean and clean in _normalise_claim_text(node.defn))
        for node in spec.nodes
    )


def _oracle_entries(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten deterministic oracle output into path/value entries."""
    if isinstance(value, dict):
        rows: list[tuple[str, Any]] = []
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_oracle_entries(child, path))
        return rows
    return [(prefix, value)]


def _number_text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return str(value)


def _oracle_claim_matches(claim: str, result: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate path/value expressions, not an unordered bag of numbers."""
    body = re.sub(r"\[\d+\]", "", _strip_quoted_spans(claim))
    entries = [(path, value) for path, value in _oracle_entries(result)
               if _number_text(value) is not None]
    if not entries:
        return False, ["quant-mismatch: oracle has no numeric path/value entries"]

    expressions = 0
    failures: list[str] = []
    used_spans: list[tuple[int, int]] = []
    for path, expected in entries:
        # Key labels may contain regex punctuation (e.g. P(|0>)); match the
        # literal path and an explicit equality/colon/is relation after it.
        pattern = re.compile(
            re.escape(path) + r"\s*(?:=|:|\bis\b|\bequals\b)\s*"
            r"(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)",
            flags=re.IGNORECASE,
        )
        for match in pattern.finditer(body):
            expressions += 1
            used_spans.append(match.span())
            actual = float(match.group(1))
            if actual != float(expected):
                failures.append(f"{path} expected {expected!r}, got {match.group(1)!r}")

    # Any numeric token outside a known key label or a validated expression is
    # an invented number. Key-label indices (|0>, |1>) are not values.
    key_label_numbers = set()
    for path, _ in entries:
        key_label_numbers.update(re.findall(r"\d+(?:\.\d+)?", path))
    numeric_tokens = list(re.finditer(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", body))
    for token in numeric_tokens:
        if token.group(0).lstrip("-") in key_label_numbers:
            continue
        if any(start <= token.start() < end for start, end in used_spans):
            continue
        failures.append(f"invented numeric token {token.group(0)!r}")
    if expressions == 0:
        failures.append("no structured oracle path/value expression")
    return not failures, failures


def verify_answer(answer, spec: CurriculumSpec) -> dict[str, Any]:
    """Deterministic, non-LLM gate over a drafted answer (ROADMAP P1.2).

    Rules — the runtime form of the verifier contract:
      R1  The draft must carry a `## Claim` section with at least one claim
          line (an empty draft or a claimless draft cannot be grounded — no
          silent passes). The T1 canonical definition and the verbatim T2/T3
          evidence live OUTSIDE the claim section: they are context, not
          claims the drafter made.
      R2  Every claim line must carry an inline [n] citation marker, and every
          [n] must index a retrieved T2 span from THIS answer's citations (a
          citation that does not exist here is invalid, not a pass).
      R3  Quantitative claims need a T3 match: the oracle must have run ok
          (`ran-ok`) AND every numeric token in the claim section must appear
          in the oracle result (its key labels or scalar/string values) — a
          number the oracle did not produce is a quantitative mismatch.
      R4  Non-quantitative answers skip R3 but never R2.

    The gate is pure: it inspects strings and the preflight result only — it
    re-runs nothing, calls no model, and does not lower MIN_GROUNDING_CHARS or
    any other bar. It returns a report dict (never raises on a bad answer), so
    the engine can retry or flag `unverified` without a crash path.
    """
    draft = (getattr(answer, "draft", "") or "")
    claims = _answer_claims(draft)
    issues: list[str] = []

    if not draft.strip():
        issues.append("empty-draft: the draft is empty; nothing to verify")
        grounded = False
        return {
            "grounded": grounded, "quantitative": False,
            "citations_used": [], "claims": [], "uncited_claims": [],
            "oracle_matched": None, "issues": issues,
        }

    quantitative = bool(getattr(answer, "quantitative", False))
    # The drafter's inline citations: [n] markers in the CLAIM section's
    # unquoted prose only. Quoted T2 spans carry the source's own brackets
    # (a wiki "[16]"), so they are data, not the drafter's citations.
    cited = set(re.findall(r"\[(\d+)\]", _strip_quoted_spans(claims)))
    citations = list(getattr(answer, "citations", []) or [])
    valid_ids = {i for i, _ in enumerate(citations, 1)}
    corpus_by_id = {source.id: source for source in spec.corpus}
    unusable_citation_ids: set[int] = set()
    for index, citation in enumerate(citations, 1):
        source_id = _citation_field(citation, "source_id")
        source = corpus_by_id.get(source_id)
        if source is None:
            issues.append(f"invalid-citation-provenance: citation [{index}] has "
                          f"unknown source {source_id!r}")
            unusable_citation_ids.add(index)
        elif source.status in {"dead", "needs-render"}:
            issues.append(f"invalid-citation-provenance: citation [{index}] source "
                          f"{source_id!r} is {source.status}")
            unusable_citation_ids.add(index)

    # R1: the draft must actually state its claims in the claim section.
    if _CLAIM_ANCHOR not in draft:
        issues.append("no-claim-section: the draft has no '## Claim' section; "
                      "a grounded answer must state its claims where the gate "
                      "can see them")
    elif not _claim_lines(claims):
        issues.append("empty-claim-section: the claim section carries no claims")

    # R2a: every [n] the drafter uses must index a real citation index.
    invalid = sorted(int(r) for r in cited if int(r) not in valid_ids)
    if invalid:
        issues.append(
            f"invalid-citation: inline marker(s) {invalid} reference no "
            f"retrieved span in this answer's citations")

    # R2b: every claim line must carry at least one inline citation.
    uncited = _claims_without_citation(claims)
    if uncited:
        preview = uncited[0][:80] + ("…" if len(uncited[0]) > 80 else "")
        issues.append(
            f"missing-citation: {len(uncited)} claim line(s) carry no inline "
            f"[n] citation; first: {preview!r}")

    # R2c: a grounded answer needs at least one usable citation at all.
    if not citations:
        issues.append("no-citations: the answer carries no retrieved T2 spans")

    def _line_supported(line: str) -> bool:
        marker_ids = {int(n) for n in re.findall(
            r"\[(\d+)\]", _strip_quoted_spans(line))
                      if int(n) in valid_ids and int(n) not in unusable_citation_ids}
        if _citation_supports_claim(line, marker_ids, citations):
            return True
        if _definition_supports_claim(line, spec):
            return True
        if quantitative:
            oracle = getattr(answer, "oracle", None)
            result = getattr(oracle, "result", None) if oracle is not None else None
            if (result and getattr(oracle, "ok", None) is True
                    and _oracle_claim_matches(line, result)[0]):
                return True
        return False

    unsupported = [line for line in _claim_lines(claims)
                   if re.search(r"\[\d+\]", line) and not _line_supported(line)]
    if unsupported:
        issues.append("unsupported-claim: claim text is not an exact excerpt from "
                      "the cited T2 span; semantic paraphrases are unverified")
    for line in _claim_lines(claims):
        outside_quotes = _strip_quoted_spans(line).casefold()
        if re.search(r"\b(?:not|never|no|cannot|can't|isn't|doesn't|false)\b",
                     outside_quotes):
            issues.append("unsupported-claim: negated claims are not certified by "
                          "an extractive citation")
            break

    # R3: quantitative claims must match the executed oracle key.
    oracle_matched: bool | None = None
    if quantitative:
        oracle = getattr(answer, "oracle", None)
        result = getattr(oracle, "result", None) if oracle is not None else None
        ran_ok = bool(oracle is not None and getattr(oracle, "ok", None) is True
                      and result)
        if not ran_ok or result is None:
            issues.append("oracle-mismatch: quantitative claim, but the T3 "
                          "oracle did not produce a usable ran-ok result")
            oracle_matched = False
        else:
            # Scan only the drafter's UNQUOTED prose for numbers: quoted T2
            # spans carry the source's own numerics (arxiv ids, dates, page
            # numbers) that are data, not the drafter's quantitative claims.
            # (Inline [n] markers are indices, not claims, so they drop too.)
            claim_body = re.sub(r"\[\d+\]", "", _strip_quoted_spans(claims))
            if not re.search(r"\d", claim_body):
                matched, details = True, []
            else:
                matched, details = _oracle_claim_matches(claims, result)
            if not matched:
                issues.append(
                    "quant-mismatch: " + "; ".join(details))
                oracle_matched = False
            else:
                oracle_matched = True

    grounded = not issues
    return {
        "grounded": grounded,
        "quantitative": quantitative,
        "citations_used": sorted(int(r) for r in cited if int(r) in valid_ids),
        "claims": claims,
        "uncited_claims": uncited,
        "oracle_matched": oracle_matched,
        "issues": issues,
    }


# ---------- orchestration ----------

def verify(spec: CurriculumSpec, out_dir: str = "out/oracle_outputs") -> dict[str, Any]:
    report: dict[str, Any] = {
        "subject": spec.subject,
        "structural": {"ok": True, "errors": {}},
        "nodes": [],
        "oracle_outputs": {},
        "summary": {},
    }

    # structural
    serrors = structural_checks(spec)
    if serrors:
        report["structural"] = {"ok": False, "errors": serrors}

    # fetch corpus (live) — via the scraper tier
    corpus_text: dict[str, str] = {}
    corpus_meta: dict[str, dict[str, Any]] = {}
    for c in spec.corpus:
        res = fetch_live(c.url)
        c.status = "live" if res["ok"] else ("needs-render" if res.get("needs_render") else "dead")
        c.http_status = res.get("status")
        corpus_text[c.id] = res.get("text", "")
        corpus_meta[c.id] = res
    report["corpus"] = [
        {"id": c.id, "name": c.name, "url": c.url, "status": c.status,
         "http_status": c.http_status, "text_len": len(corpus_text.get(c.id, "")),
         # The runtime cache is populated from this field. Keep the measured
         # extracted body in the report; metadata alone cannot support a later
         # offline answer.
         "text": corpus_text.get(c.id, ""),
         "method": corpus_meta.get(c.id, {}).get("method", "none"),
         "needs_render": bool(corpus_meta.get(c.id, {}).get("needs_render")),
         "author": corpus_meta.get(c.id, {}).get("author"),
         "error": corpus_meta.get(c.id, {}).get("error")}
        for c in spec.corpus
    ]

    # per-node
    n_ok = n_thin = n_unver = 0
    for n in spec.nodes:
        v: dict[str, Any] = {"structural_ok": n.id not in serrors}

        # grounding
        hits = coverage(n, corpus_text)
        cited = [c for c in n.grounding_corpus if corpus_text.get(c, "").strip()]
        v["grounding"] = {
            "cited_sources": n.grounding_corpus,
            "live_sources": cited,
            "coverage_hits": hits,
            "ok": bool(hits) and bool(cited),
        }

        # executability
        if n.oracle:
            ores = run_oracle(n.oracle)
            v["oracle"] = {"name": n.oracle, "ok": ores["ok"]}
            if ores["ok"]:
                p = save_oracle_output(n.oracle, ores["result"], out_dir)
                v["oracle"]["output"] = p
                report["oracle_outputs"][n.oracle] = {
                    "status": "ok", "output": os.path.relpath(p), "result": ores["result"],
                }
            else:
                v["oracle"]["error"] = ores.get("error")
                report["oracle_outputs"][n.oracle] = {"status": "error",
                                                       "error": ores.get("error")}
        else:
            v["oracle"] = {"name": None, "ok": "na"}

        # decide
        grounded = (v["structural_ok"] and v["grounding"]["ok"]
                    and (not n.oracle or v["oracle"]["ok"]))
        if grounded:
            n.status = "grounded"
            n_ok += 1
        elif v["structural_ok"] and not v["grounding"]["ok"]:
            n.status = "unverified"
            n_unver += 1
        else:
            n.status = "thin"
            n_thin += 1
        n.verification = v
        report["nodes"].append({
            "id": n.id, "title": n.title, "status": n.status,
            "grounding_ok": v["grounding"]["ok"],
            "oracle": (n.oracle or None),
            "oracle_ok": v["oracle"].get("ok"),
        })

    total = len(spec.nodes)
    report["summary"] = {
        "nodes_total": total,
        "grounded": n_ok,
        "thin": n_thin,
        "unverified": n_unver,
        "corpus_live": sum(1 for c in spec.corpus if c.status == "live"),
        "corpus_dead": sum(1 for c in spec.corpus if c.status == "dead"),
        "corpus_needs_render": sum(1 for c in spec.corpus if c.status == "needs-render"),
        "corpus_total": len(spec.corpus),
        "structural_ok": report["structural"]["ok"],
        "all_grounded": (n_ok == total and report["structural"]["ok"]),
    }
    return report


def print_report(report: dict[str, Any]) -> None:
    print("\n" + "=" * 64)
    print(f"  VERIFICATION REPORT — {report['subject']}")
    print("=" * 64)
    s = report["summary"]
    print(f"  structural DAG:   {'OK' if s['structural_ok'] else 'FAIL'}")
    print(f"  corpus:           {s['corpus_live']} live / {s['corpus_needs_render']} needs-render / {s['corpus_dead']} dead  (of {s['corpus_total']})")
    print(f"  nodes:            {s['grounded']} grounded / {s['thin']} thin / "
          f"{s['unverified']} unverified  (of {s['nodes_total']})")
    print("-" * 64)
    for n in report["nodes"]:
        mark = {"grounded": "OK", "thin": "THIN", "unverified": "???"}[n["status"]]
        oracle = n["oracle"] or "none"
        print(f"  [{mark}] {n['id']:<18} src={'OK' if n['grounding_ok'] else 'X'}"
              f"  oracle={oracle}")
    print("-" * 64)
    print(f"  ALL GROUNDED:     {'YES' if s['all_grounded'] else 'NO — see flags above'}")
    if report.get("corpus"):
        print("  corpus (extraction method from scraper tier):")
        for c in report["corpus"]:
            flag = {"live": "LIVE", "dead": "DEAD", "needs-render": "RENDER"}.get(c["status"], c["status"].upper())
            print(f"     [{flag:>6}] {c['id']:<22} http={c['http_status']}  via={c['method']:<12}"
                  f" len={c['text_len']:>6}  {c['url']}")
            if c.get("needs_render"):
                print("             ↳ SPA: server HTML has no body → needs browser render (Tier-3b)")
    print("=" * 64)
