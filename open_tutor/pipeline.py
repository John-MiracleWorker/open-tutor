"""Curriculum design orchestration and the ``python -m open_tutor.pipeline`` CLI."""
from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .compiler import (
    BLOCKED,
    COMPILED,
    PAUSED,
    CorruptBundleError,
    compile_spec,
    decide,
    load_last_spec,
    spec_content_hash,
)
from .designer import LocalCompletionClient, LocalModelError, generate_candidate
from .generator import (
    generate_calculus,
    generate_chemistry,
    generate_chess,
    generate_history,
    generate_music_theory,
    generate_quantum_computing,
)
from .source_adapters import discover_sources, vet_explicit_sources
from .spec import CurriculumSpec, load_yaml, save_yaml
from .verifier import print_report, verify

SUPPORTED = {
    "quantum computing": generate_quantum_computing,
    "quantum-computing": generate_quantum_computing,
    "quantum": generate_quantum_computing,
    "calculus": generate_calculus,
    "music theory": generate_music_theory,
    "music-theory": generate_music_theory,
    "chemistry": generate_chemistry,
    "chess": generate_chess,
    "history": generate_history,
}


def _slug_topic(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", topic.strip().lower()).strip("-") or "candidate"


def _write_corpus_cache(spec: CurriculumSpec, report: dict[str, Any], cache_dir: str,
                        *, subject_dir: str | None = None) -> None:
    """Persist verifier-measured extraction when supplied by the verifier.

    Older verifier versions expose only measurement metadata, so this helper
    intentionally does not invent or pad text.  The engine-worker integration
    must add the measured body under ``report.corpus[].text`` (see WORKSTREAM).
    """
    corpus = {c["id"]: c for c in report.get("corpus", [])}
    subject_dir = subject_dir or os.path.join(cache_dir, spec.subject)
    os.makedirs(subject_dir, exist_ok=True)
    for source in spec.corpus:
        measured = corpus.get(source.id, {})
        text = measured.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        path = os.path.join(subject_dir, f"{source.id}.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)


def _write_candidate(spec: CurriculumSpec, report: dict[str, Any] | None,
                     out_root: str, decision: Mapping[str, Any]) -> dict[str, str]:
    """Write a blocked/paused candidate outside active compiled curriculum."""
    candidate_dir = os.path.join(out_root, "candidates")
    os.makedirs(candidate_dir, exist_ok=True)
    spec_path = os.path.join(candidate_dir, f"{spec.subject}.yaml")
    save_yaml(spec, spec_path)
    result = {"candidate_spec_path": spec_path}
    if report is not None:
        report_path = os.path.join(candidate_dir, f"{spec.subject}.report.json")
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
        result["candidate_report_path"] = report_path
    decision_path = os.path.join(candidate_dir, f"{spec.subject}.decision.json")
    with open(decision_path, "w", encoding="utf-8") as handle:
        json.dump(dict(decision), handle, indent=2, default=str)
    result["candidate_decision_path"] = decision_path
    return result


def _error_result(topic: str, errors: list[str], out_root: str) -> dict[str, Any]:
    os.makedirs(os.path.join(out_root, "candidates"), exist_ok=True)
    return {"subject": _slug_topic(topic), "errors": errors,
            "decision": {"status": BLOCKED, "reasons": errors},
            "spec_path": None, "report_path": None}


def design_curriculum(topic: str, out_root: str = "out", review: bool = False,
                      old_spec: CurriculumSpec | None = None,
                      old_spec_path: str | None = None,
                      *, level: str | None = None, depth: str | None = None,
                      sources: Sequence[str] | None = None,
                      client: LocalCompletionClient | None = None) -> dict[str, Any]:
    """Design, verify, and route a curriculum.

    Curated starters are deterministic and available offline.  Any other topic
    requires a configured local OpenAI-compatible model and at least one vetted
    discovered source.  Model errors are returned as bounded first-class data.
    """
    key = topic.strip().lower()
    errors: list[str] = []
    if key in SUPPORTED:
        spec = SUPPORTED[key]()
        if level:
            spec.scope["level"] = level
        if depth:
            spec.scope["depth"] = depth
    else:
        discovery = vet_explicit_sources(sources or []) if sources else discover_sources(topic)
        errors.extend(discovery.get("errors", []))
        namespace = _slug_topic(topic)
        records = {}
        for row in discovery.get("sources", []):
            # Discovery IDs are stable globally; curriculum cache IDs are not.
            # Namespace them at the subject boundary so two subjects citing the
            # same public page never share or overwrite a cache artifact.
            namespaced = dict(row)
            namespaced["id"] = f"{namespace}-{row['id']}"
            records[namespaced["id"]] = namespaced
        if not records:
            errors.append("source discovery produced no vetted public sources")
            # Candidate generation is impossible with an empty vetted registry
            # (every corpus entry must cite a vetted discovery ID); fail fast
            # instead of spending two model attempts on a doomed schema run.
            return _error_result(topic, errors, out_root)
        try:
            spec = generate_candidate(topic, records, level=level or "introductory",
                                      depth=depth or "foundations", client=client)
        except (LocalModelError, ValueError) as exc:
            errors.append(f"candidate generation unavailable: {exc}")
            return _error_result(topic, errors, out_root)

    os.makedirs(out_root, exist_ok=True)
    oracle_dir = os.path.join(out_root, "oracle_outputs")
    cache_dir = os.path.join(out_root, "cache")
    cur_dir = os.path.join(out_root, "curriculum")
    os.makedirs(cur_dir, exist_ok=True)

    old_spec_corrupt = False
    if old_spec is None and old_spec_path:
        try:
            old_spec = load_yaml(old_spec_path)
        except Exception as exc:  # noqa: BLE001
            old_spec_corrupt = True
            errors.append(f"cannot read baseline {old_spec_path!r}: {type(exc).__name__}: {exc}")
    if old_spec is None and not old_spec_corrupt:
        try:
            old_spec = load_last_spec(out_root, spec.subject)
        except CorruptBundleError as exc:
            old_spec_corrupt = True
            errors.append(str(exc))

    report = verify(spec, out_dir=oracle_dir)
    print_report(report)
    if old_spec_corrupt:
        decision = decide(report, old_spec, spec, review=review)
        decision.status = BLOCKED
        decision.reasons.insert(0, "corrupt prior state: cannot safely judge replacement")
    else:
        decision = decide(report, old_spec, spec, review=review)

    if decision.status == COMPILED:
        persisted = compile_spec(spec, report, out_root=out_root, review=False, old_spec=old_spec)
        decision = persisted
        candidate_paths: dict[str, str] = {}
    else:
        # compiler.write_bundle historically wrote blocked candidates over an
        # active YAML.  Keep the active compiled subject untouched and put the
        # candidate/diff/evidence under out/candidates instead.
        candidate_paths = _write_candidate(spec, report, out_root, decision.to_dict())

    report_path = os.path.join(cur_dir, f"{spec.subject}.report.json") if decision.status == COMPILED else candidate_paths["candidate_report_path"]
    if decision.status == COMPILED:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
    if decision.status == COMPILED:
        _write_corpus_cache(spec, report, cache_dir)
    else:
        # A blocked/paused candidate is isolated by subject and fingerprint.
        # It must never replace the active subject's offline retrieval cache;
        # review approval promotes this bundle explicitly in the API layer.
        _write_corpus_cache(
            spec, report, cache_dir,
            subject_dir=os.path.join(out_root, "cache-candidates", spec.subject,
                                     spec_content_hash(spec)),
        )

    result: dict[str, Any] = {
        "subject": spec.subject, "spec_path": decision.spec_path if decision.status == COMPILED else None,
        "report_path": report_path, "receipt_path": decision.receipt_path if decision.status == COMPILED else None,
        "audit_path": decision.audit_path if decision.status == COMPILED else None,
        "report": report, "decision": decision.to_dict(), "errors": errors,
    }
    result.update(candidate_paths)
    if decision.status == BLOCKED:
        print("[compile] blocked candidate preserved under out/candidates; active bundle was not overwritten")
    elif decision.status == PAUSED:
        print("[compile] review paused candidate preserved under out/candidates")
    else:
        print(f"[compile] {spec.subject}: AUTO-COMPILED (verified)")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m open_tutor.pipeline")
    parser.add_argument("topic", nargs="+", help="subject or arbitrary topic")
    parser.add_argument("--out", default="out")
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--level")
    parser.add_argument("--depth")
    parser.add_argument("--source", action="append", dest="sources")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = design_curriculum(" ".join(args.topic), out_root=args.out, review=args.review,
                               level=args.level, depth=args.depth, sources=args.sources)
    return 0 if result.get("decision", {}).get("status") in {COMPILED, PAUSED} else 2


if __name__ == "__main__":
    raise SystemExit(main())
