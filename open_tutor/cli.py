#!/usr/bin/env python3
"""CLI entry point.

    python -m open_tutor.cli "quantum computing"            # design + verify + compile
    python -m open_tutor.cli compile "quantum computing"    # same, explicit verb
    python -m open_tutor.cli compile "quantum computing" --review
    python -m open_tutor.cli compile "quantum computing" --old path/to/old.yaml
    python -m open_tutor.cli engine "What is a qubit?" [subject]
    # or (after `pip install .`):
    open-tutor "quantum computing"
"""
from __future__ import annotations

import sys
from typing import List, Optional

from .pipeline import design_curriculum


def _serve_question(question: str, subject: str = "quantum computing") -> int:
    """Runtime tutor: serve one grounded answer (or an honest degraded state).

    Local-first: if the subject's compiled bundle exists under out/, the spec
    and T2 cache come from there and nothing is re-fetched or re-verified.
    Only a bundle-less subject runs the full design + verify path.
    """
    import json
    import os

    from .engine import tutor
    from .pipeline import SUPPORTED
    from .spec import load_yaml
    from .verifier import verify

    key = subject.strip().lower()
    if key not in SUPPORTED:
        raise SystemExit(
            f"Unknown subject '{subject}'. Supported in this PoC: {list(SUPPORTED)}.")

    bundle = os.path.join("out", "curriculum", f"{SUPPORTED[key]().subject}.yaml")
    if os.path.exists(bundle) and os.path.isdir("out/cache"):
        spec = load_yaml(bundle)
        answer = tutor(spec, question, cache_dir="out/cache")
    else:
        spec = SUPPORTED[key]()
        verify(spec, out_dir="out/oracle_outputs")   # populate node status + capture oracles
        answer = tutor(spec, question, cache_dir="out/cache")
    print(json.dumps(answer.to_dict(), indent=2, default=str))
    return 0


def _compile(args: List[str]) -> int:
    """`compile <subject> [--review] [--old PATH]` — the P1.0 policy surface.

    Default: automatic verified compilation (no approval needed). `--review`
    pauses for a human on a new subject or material curriculum change (diff,
    evidence receipts, status, and audit trail are in the bundle). Failed
    verification or unsafe changes are ALWAYS flagged and blocked, regardless
    of flags.
    """
    rest = list(args)
    review = False
    old: Optional[str] = None
    positional: List[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--review":
            review = True
        elif a == "--old":
            if i + 1 >= len(rest):
                raise SystemExit("usage: --old PATH")
            old = rest[i + 1]
            i += 1
        else:
            positional.append(a)
        i += 1

    topic = positional[0] if positional else "quantum computing"
    design_curriculum(topic, review=review, old_spec_path=old)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "engine":
        question = argv[1] if len(argv) > 1 else ""
        subject = argv[2] if len(argv) > 2 else "quantum computing"
        if not question:
            raise SystemExit('usage: open-tutor engine "<question>" [subject]')
        return _serve_question(question, subject)
    if argv and argv[0] == "compile":
        return _compile(argv[1:])
    topic = argv[0] if argv else "quantum computing"
    design_curriculum(topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
