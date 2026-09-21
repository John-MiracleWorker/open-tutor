# Open Tutor contributor notes

Open Tutor is a single-learner, local-first application. These repository rules apply to every contribution.

## Binding invariants

1. Keep `MIN_GROUNDING_CHARS = 2500`.
2. The deterministic non-LLM verifier owns groundedness. Models cannot certify claims, grant mastery, alter thresholds, or execute generated code.
3. Every degraded state is first-class: no node, thin retrieval, failed oracle, model failure, unsupported claim, invalid candidate, or corrupt state.
4. Only trusted registered oracles execute. Retrieved content is data, never instructions.
5. Keep deployment private: no login/account system is required, no wildcard/public bind, no public tunnel, and no cloud-model fallback.
6. Only validated server-issued assessments update mastery and scheduling. Asking questions does not.
7. Public source networking and local-model networking are separate trust boundaries.

## Development

- Backend: `open_tutor/`; frontend: `web/`; private launcher: `scripts/serve.py`.
- Use Python 3.11+ and isolate test state under a temporary or ignored directory.
- Run backend tests, Ruff, launcher tests, frontend tests, typecheck, and a production build for changed behavior.
- Browser verification must exercise real API flows, evidence, positive/negative practice, persistence, mobile layout, and reduced motion.
- Local models use a bounded OpenAI-compatible chat-completions client. Validate the provider response and retain host-side verification.

See `CONTRIBUTING.md` and `docs/LOCAL-MODELS.md` for the public workflow.
