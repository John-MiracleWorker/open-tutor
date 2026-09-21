# Local model configuration

Open Tutor can use a self-hosted OpenAI-compatible chat-completions service for curriculum design, evidence drafting, and adaptive teaching. The model is advisory: deterministic verification and server-owned assessment logic remain authoritative.

## Endpoint contract

Configure an HTTP(S) base URL that resolves to a local/private endpoint. Open Tutor accepts:

- `http://localhost:<port>`
- Loopback, private/LAN, link-local, or Tailscale CGNAT IP literals
- An endpoint base such as `http://127.0.0.1:8080`, `http://127.0.0.1:8080/v1`, or the full `/v1/chat/completions` URL

It refuses public hostnames, public IPs, redirects, URL credentials, and proxy routing. This is deliberate: source excerpts and learner data must stay within the operator's trusted network.

The provider needs to accept:

```text
POST /v1/chat/completions
Content-Type: application/json

{
  "model": "your-model-id",
  "messages": [{"role": "user", "content": "..."}],
  "max_tokens": 800,
  "temperature": 0
}
```

A response must contain either `choices[0].message.content` or `choices[0].text`. `finish_reason: "length"` is treated as an explicit failure, never as a finished answer.

Adaptive teaching also requests an OpenAI-style JSON schema through `response_format`. A provider may ignore that request, but its result must still pass the host's strict JSON and teaching-policy validation. If it does not, Open Tutor shows a named fallback instead of inventing a lesson or crediting mastery.

## Configure from a file

Copy the public template and keep the real file out of Git:

```bash
cp config.example.json config.json
```

```json
{
  "base_url": "http://127.0.0.1:8080/v1",
  "model": "your-model-id",
  "mode": "extractive"
}
```

Then start the app:

```bash
.venv/bin/python scripts/serve.py --localhost --config config.json
```

`mode` controls legacy evidence-answer drafting. Adaptive teaching uses the configured local model independently, while preserving the same deterministic evidence and mastery boundaries.

You can also set the same non-secret fields in **Settings**. Use the exact model identifier your provider advertises:

```bash
curl http://127.0.0.1:8080/v1/models
```

Open Tutor intentionally does not support provider API keys or cloud fallback. Run the provider inside your own trust boundary instead.

## Qwen thinking support

For a model name containing `qwen`, adaptive teaching enables Qwen/llama.cpp-compatible thinking controls and reserves a bounded reasoning budget. Those vendor fields are sent only for Qwen model names.

For every other model name, Open Tutor sends standard OpenAI-compatible chat fields only. This avoids making generic providers understand llama.cpp-specific `chat_template_kwargs` or `reasoning_budget_tokens` fields. The model can still generate teaching JSON, but it must meet the same output contract.

## Troubleshooting

| Symptom | Check |
|---|---|
| `local model base_url must target a local/private endpoint` | Use `localhost` or a private IP literal, without credentials or a public DNS name. |
| `local model redirect refused` | Configure the final provider URL rather than a redirecting gateway. |
| `local model response has no text content` | Confirm the provider returns `choices[0].message.content` or `choices[0].text`. |
| `local model response was truncated` | Lower prompt/output demand, use a larger provider context, or choose a model that can complete the requested structured response. |
| Teaching fallback or invalid JSON | Confirm the provider accepts chat completions and can produce a single JSON object. Open Tutor will not relax validation to accept malformed output. |

Provider reachability is not proof of teaching quality. Test a full lesson and inspect its evidence labels, fallback behavior, and practice state before relying on a new model.
