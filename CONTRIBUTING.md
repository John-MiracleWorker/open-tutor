# Contributing

Open Tutor's core promise is simple: **a claim is grounded only when deterministic code proves it from substantive evidence or a trusted oracle.** Contributions must preserve that boundary.

## Non-negotiables

1. Do not lower `MIN_GROUNDING_CHARS = 2500`.
2. Do not make the verifier trust model output. Models may propose; deterministic host code decides groundedness, grading, and mastery.
3. Keep failure states visible: thin retrieval, blocked candidates, missing oracles, malformed model output, unsupported claims, and corrupt state are outcomes, not silent successes.
4. Keep trusted oracles deterministic. Never execute generated or learner-provided code.
5. Preserve replay safety, prerequisite locks, server-owned assessment keys, and subject isolation.
6. Keep model endpoints local/private and source retrieval separately constrained.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[quantum,subjects,render,dev]'
npm --prefix web ci
```

Run the full local gate before opening a pull request:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check open_tutor
.venv/bin/python -m unittest discover -s scripts -p test_serve.py
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run build
```

Use an isolated `--data-dir` for all QA. Do not add learner data, provider logs, screenshots, local endpoints, tokens, service units, or generated `out/` artifacts to a pull request.

## Extending the project

- Add subject generators, source adapters, or trusted deterministic oracles without granting them authority to declare a result grounded.
- Add model-provider support through the bounded OpenAI-compatible transport. Never add a cloud fallback or pass provider credentials through the browser.
- Add tests that cover both success and explicit failure. A passing mock is not proof that a model, source, or browser integration is safe.
- Keep the UI honest: do not add fabricated progress, verified badges, or “mastery” states.

See [architecture](ARCHITECTURE.md), [model configuration](docs/LOCAL-MODELS.md), and the [verifier contract](docs/VERIFIER-CONTRACT.md).
