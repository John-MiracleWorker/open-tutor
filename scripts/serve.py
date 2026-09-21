#!/usr/bin/env python3
"""Private Open Tutor launcher. Never silently falls back to a public bind."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess

TAILNET = ipaddress.ip_network('100.64.0.0/10')
ROOT = Path(__file__).resolve().parent.parent


def validate_host(host: str, *, allow_loopback: bool = False) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError('Bind host must be a numeric Tailscale IP.') from exc
    if address in TAILNET or (allow_loopback and address.is_loopback):
        return str(address)
    raise ValueError('Only a Tailscale IPv4 bind is permitted (or explicit --localhost for tests).')


def resolve_host() -> str:
    command = shutil.which('tailscale')
    if not command:
        raise RuntimeError('Tailscale CLI is unavailable. Supply --host with this machine’s Tailscale IPv4 address.')
    try:
        host = subprocess.check_output([command, 'ip', '-4'], text=True, timeout=10).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('Tailscale IP discovery failed; refusing any fallback bind.') from exc
    return validate_host(host)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--host', help='This machine’s Tailscale IPv4 address')
    group.add_argument('--localhost', action='store_true', help='Explicit loopback-only testing mode')
    parser.add_argument('--port', type=int, default=9130)
    parser.add_argument('--data-dir', type=Path, default=Path.home()/'.local/share/open-tutor')
    parser.add_argument('--config', type=Path, help='Optional local JSON: base_url, model, mode, write_token')
    parser.add_argument('--check', action='store_true', help='Validate settings/bind/build; do not start or modify data')
    args = parser.parse_args()
    host = '127.0.0.1' if args.localhost else validate_host(args.host) if args.host else resolve_host()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    frontend = ROOT/'web/dist'
    if not (frontend/'index.html').is_file():
        parser.error('Frontend is not built. Run: npm --prefix web ci && npm --prefix web run build')
    settings = json.loads(args.config.read_text()) if args.config else {}
    if not isinstance(settings, dict) or set(settings)-{'base_url','model','mode','write_token'}:
        parser.error('config must be a JSON object using base_url/model/mode/write_token only')
    if any(not isinstance(value,str) for value in settings.values()):
        parser.error('all configuration values must be strings')
    if settings.get('mode','extractive') not in ('extractive','local-model'):
        parser.error('mode must be extractive or local-model')
    if settings.get('base_url'):
        from open_tutor.llm import validate_local_endpoint
        validate_local_endpoint(settings['base_url'])
    if args.check:
        print(json.dumps({'host':host,'port':args.port,'data_dir':str(args.data_dir.resolve()),'frontend':str(frontend),'write_protection':bool(settings.get('write_token'))}))
        return
    os.umask(0o077)
    args.data_dir.mkdir(parents=True,exist_ok=True)
    from open_tutor.server import create_app
    import uvicorn
    app = create_app(data_dir=args.data_dir,frontend_dir=frontend,write_token=settings.get('write_token'))
    provider={k:settings[k] for k in ('base_url','model','mode') if k in settings}
    if provider:
        app.state.storage.update_settings(provider)
    uvicorn.run(app,host=host,port=args.port,proxy_headers=False,access_log=False)


if __name__ == '__main__':
    main()
