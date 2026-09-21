"""Bounded local OpenAI-compatible completion client.

This module is deliberately small and boring: it speaks only HTTP JSON to a
caller-selected local/private endpoint, never discovers or uploads to a cloud
provider, and returns text. Grounding is still decided by ``verifier.py``.
Retrieved source text is inserted as a marked data field by the engine; this
client does not execute, interpret, or follow anything in that text.
"""
from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class LocalCompletionError(RuntimeError):
    """A named local-model transport or response failure."""


def validate_local_endpoint(base_url: str) -> str:
    """Validate an endpoint without DNS/network access.

    Open Tutor is local-first. Hostnames are restricted to ``localhost`` and
    IP literals in loopback, private, link-local, or Tailscale CGNAT ranges;
    public/cloud destinations are rejected before any request is attempted.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("local model base_url is required")
    parsed = urllib.parse.urlparse(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("local model base_url must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("local model base_url must not contain credentials")
    host = parsed.hostname
    if not host:
        raise ValueError("local model base_url has no host")
    host_lower = host.casefold().rstrip(".")
    if host_lower == "localhost":
        return base_url.rstrip("/")
    try:
        address = ipaddress.ip_address(host_lower)
    except ValueError as exc:
        raise ValueError(
            "local model base_url host must be localhost or a private IP literal"
        ) from exc
    allowed = (address.is_loopback or address.is_private or address.is_link_local
               or address in ipaddress.ip_network("100.64.0.0/10"))
    if not allowed:
        raise ValueError("local model base_url must target a local/private endpoint")
    return base_url.rstrip("/")


class _RejectLocalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LocalCompletionError("local model redirect refused; configure the final local endpoint")


def _open_local(request: urllib.request.Request, timeout: float):
    """No ambient proxy and no redirect may move local prompts off-device/LAN."""
    validate_local_endpoint(request.full_url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectLocalRedirect())
    return opener.open(request, timeout=timeout)


def _endpoint(base_url: str) -> str:
    base = validate_local_endpoint(base_url)
    if base.endswith("/v1/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LocalCompletionError("local model response has no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise LocalCompletionError("local model response choice is not an object")
    if first.get("finish_reason") == "length":
        raise LocalCompletionError("local model response was truncated at its token limit")
    message = first.get("message")
    if isinstance(message, Mapping) and isinstance(message.get("content"), str):
        content = message["content"]
        if content.strip():
            return content
    if isinstance(first.get("text"), str) and first["text"].strip():
        return first["text"]
    raise LocalCompletionError("local model response has no text content")


def local_completion(messages: Sequence[Mapping[str, str]], base_url: str,
                     model: str, timeout: float = 120.0,
                     max_tokens: int = 800,
                     response_format: Mapping[str, Any] | None = None,
                     stop: Sequence[str] | None = None,
                     enable_thinking: bool | None = None,
                     reasoning_budget_tokens: int | None = None) -> str:
    """Call a local OpenAI-compatible chat-completions endpoint once.

    The function has no retry loop so callers can impose their own total retry
    budget. It validates response shape and never returns a self-declared
    grounded flag; the deterministic verifier remains the authority.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("local model name is required")
    if timeout <= 0 or timeout > 120:
        raise ValueError("timeout must be in (0, 120]")
    if not isinstance(max_tokens, int) or not 1 <= max_tokens <= 32_000:
        raise ValueError("max_tokens must be an integer in [1, 32000]")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise TypeError("messages must be a sequence of message objects")
    clean_messages = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise TypeError("each message must be an object")
        role, content = message.get("role"), message.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise ValueError("each message needs a valid role and string content")
        clean_messages.append({"role": role, "content": content})

    payload = {"model": model, "messages": clean_messages,
               "max_tokens": max_tokens, "temperature": 0}
    if enable_thinking is not None and type(enable_thinking) is not bool:
        raise TypeError("enable_thinking must be boolean or None")
    if enable_thinking is not None or "qwen" in model.casefold():
        payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking if enable_thinking is not None else False}
    if reasoning_budget_tokens is not None:
        if type(reasoning_budget_tokens) is not int:
            raise TypeError("reasoning_budget_tokens must be an integer")
        if not 0 <= reasoning_budget_tokens < max_tokens:
            raise ValueError("reasoning_budget_tokens must leave room within max_tokens")
        if enable_thinking is not True:
            raise ValueError("reasoning_budget_tokens requires enable_thinking=True")
        payload["reasoning_budget_tokens"] = reasoning_budget_tokens
    if response_format is not None:
        payload["response_format"] = dict(response_format)
    if stop is not None:
        if not isinstance(stop, (list, tuple)) or not 1 <= len(stop) <= 4 or any(not isinstance(s, str) or not s or len(s) > 100 for s in stop):
            raise ValueError("stop must contain 1 to 4 bounded strings")
        payload["stop"] = list(stop)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        _endpoint(base_url), data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with _open_local(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LocalCompletionError(f"local model request failed: {type(exc).__name__}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalCompletionError("local model returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise LocalCompletionError("local model response is not an object")
    return _content(payload)


@dataclass
class LocalCompletion:
    """Callable adapter useful as an engine ``draft_fn`` dependency."""

    base_url: str
    model: str
    timeout: float = 120.0
    max_tokens: int = 800

    def __call__(self, messages: Sequence[Mapping[str, str]]) -> str:
        return local_completion(messages, self.base_url, self.model,
                                timeout=self.timeout, max_tokens=self.max_tokens)
