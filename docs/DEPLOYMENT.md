# Private deployment

Open Tutor is designed for a single learner and stores conversations, curricula, evidence caches, and learning state locally. The repository is public; a deployment should not be.

## Start safely

Build the frontend, choose a local data directory, then start on loopback:

```bash
npm --prefix web run build
.venv/bin/python scripts/serve.py --localhost --data-dir "$HOME/.local/share/open-tutor" --config config.json
```

The default data directory is `~/.local/share/open-tutor`. Back it up as one unit while the service is stopped: it includes SQLite state, curricula, reports, receipts, and source caches. Never commit or copy it into the repository.

For access from another device, use a private network you administer. The launcher can discover a Tailscale address by default, or you can explicitly give it a private Tailscale IP:

```bash
.venv/bin/python scripts/serve.py --host <private-tailscale-ip> --config config.json
```

The launcher rejects wildcard, public, and LAN binds. Do not put an unauthenticated public proxy or tunnel in front of the app.

## Configuration hygiene

- Copy `config.example.json` to `config.json`; it is ignored by Git.
- `write_token` is optional local write protection. If you use one, generate and store it outside version control.
- Model endpoints must remain local/private. The app accepts no provider API keys and does not silently fall back to cloud inference.
- Keep service definitions, systemd units, launch-agent files, log paths, and firewall rules out of the repository unless they are portable templates with placeholders only.

## Before upgrades

1. Back up the complete data directory while Open Tutor is stopped.
2. Build and test the replacement source tree.
3. Restart only the Open Tutor process; do not couple app updates to model-server or GPU orchestration changes.
4. Verify `/api/health`, the configured model metadata, a saved conversation, and static assets from the intended private address.

A local HTTP 200 does not prove a deployment is safe. Keep personal learner data out of QA runs and use an isolated `--data-dir` for tests.
