# Open Tutor

**A self-hosted, source-grounded learning workspace.** Design a curriculum, follow its concept map, ask questions, inspect the supporting evidence, and practice what you are learning.

Open Tutor separates generation from verification. A local model can propose a curriculum or draft teaching material, but it cannot certify claims, award mastery, alter thresholds, or execute generated code. Those decisions stay with deterministic application code and server-issued assessment keys.

> **Status:** Alpha. It is a single-learner, self-hosted application—not a hosted service or a replacement for expert instruction.

## What it does

- **Guided learning:** source-backed tutoring, durable conversations, prerequisite-aware concept maps, bounded adaptive teaching, and exact evidence passages.
- **Curriculum design:** authored starter paths plus structured local-model generation for new subjects; generated candidates are independently verified before activation.
- **Practice and progress:** server-issued quizzes and teach-backs, BKT knowledge estimates, FSRS scheduling, and replay-safe learner state. Asking a question never awards mastery.
- **Local model support:** connect a compatible self-hosted model endpoint from the Settings screen or a local configuration file. No API keys are accepted or stored by Open Tutor.
- **Aurora interface:** responsive local assets, reduced-motion support, evidence dialogs, a mobile concept drawer, and a pinned composer.

## Quick start

### 1. Install dependencies

Requirements: Python 3.11+, Node.js, and a self-hosted OpenAI-compatible model endpoint for model-assisted design or teaching. The app can still show its source/evidence flows without a model.

```bash
git clone https://github.com/John-MiracleWorker/open-tutor.git
cd open-tutor

python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[quantum,subjects,render,dev]'
npm --prefix web ci
npm --prefix web run build
```

### 2. Configure your model (optional)

Copy the example without committing your local configuration:

```bash
cp config.example.json config.json
```

Set `base_url` to your own local or private endpoint and `model` to an identifier it serves. Then run Open Tutor on loopback:

```bash
.venv/bin/python scripts/serve.py --localhost --config config.json
```

Open <http://127.0.0.1:9130>. For another device on your private network, use a private bind that fits your security model; the included launcher supports Tailscale discovery by default and deliberately rejects wildcard/public binds.

See [local model setup](docs/LOCAL-MODELS.md) for the supported HTTP contract, Qwen-specific thinking behavior, and provider troubleshooting. See [private deployment](docs/DEPLOYMENT.md) for data locations and safer network boundaries.

## Model compatibility

Open Tutor uses an OpenAI-compatible `POST /v1/chat/completions` endpoint. Choose the exact `model` ID advertised by your provider—usually from `GET /v1/models`.

- **Standard local models** receive standard chat fields (`model`, `messages`, `max_tokens`, `temperature`, and structured-output request data where needed).
- **Qwen through llama.cpp-compatible servers** additionally receives Qwen thinking controls for adaptive lessons. These vendor-specific fields are never sent to other model names.
- Responses remain independently checked. A model that rejects structured output, truncates, or returns invalid teaching JSON produces an explicit fallback; it never self-certifies or changes learner mastery.

The endpoint must be local or private: `localhost`, loopback, private/LAN or link-local IP literals, or Tailscale CGNAT IPs. Public/cloud hostnames, redirects, credentials in URLs, and ambient proxies are intentionally refused so source context and learner data do not leave the operator's trust boundary.

## Grounding and safety boundaries

- The minimum evidence floor is **2,500 extracted characters**. A title, abstract, or HTTP success response is not sufficient evidence.
- The deterministic verifier, not a model, decides whether evidence supports a claim.
- Generated content cannot award scores/mastery, change prerequisite locks, or execute code.
- Source retrieval and model transport are separately constrained. Public source fetching rejects private/metadata targets; local-model calls reject public destinations and redirects.
- Failure states—including thin retrieval, blocked candidates, unsupported claims, model errors, and corrupt state—stay visible rather than being silently converted to success.

Read [the verifier contract](docs/VERIFIER-CONTRACT.md), [source-safety policy](docs/SOURCE-SAFETY.md), and [adaptive-teaching contract](docs/ADAPTIVE-TEACHING.md) before extending the system.

## Development

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check open_tutor
.venv/bin/python -m unittest discover -s scripts -p test_serve.py
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run build
```

The app stores runtime data outside the repository by default. Use an isolated `--data-dir` for development and tests; never commit generated caches, local configuration, learner data, screenshots, or model logs.

## Documentation

- [Local models](docs/LOCAL-MODELS.md)
- [Private deployment](docs/DEPLOYMENT.md)
- [HTTP API](docs/API.md)
- [Architecture](ARCHITECTURE.md)
- [Data model](docs/DATA-MODEL.md)
- [Evidence and retrieval](docs/RESEARCH.md)
- [Contributing](CONTRIBUTING.md)

## License

[MIT](LICENSE) © 2026 Open Tutor contributors.
