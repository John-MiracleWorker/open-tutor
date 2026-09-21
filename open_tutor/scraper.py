"""Tier-2 source EXTRACTION (the "web scraper" layer).

Role: turn a live URL into clean, article-scoped text + metadata for the
verifier's coverage check and for future chunking/embedding.

This is the LAYER where a scraper earns its keep — it does NOT *find* sources
(that's search/retrieval, an LLM+web step). It *extracts* them.

Extraction ladder (try in order, keep first with real body text):
  1. trafilatura          — article-focused, handles most static sites
  2. raw-HTML text strip  — fallback for pages trafilatura can't parse
  3. browser render       — SPA fallback (P0.5): render in a headless browser
                            (Playwright/Chromium, pluggable backend) and
                            re-extract from the rendered DOM

Design note: the PoC already proved a plain fetch is *not enough* — IBM Quantum
Learning is a client-side SPA, so its server HTML has no concept text. This
module surfaces that as a low `body_len` + a `needs_render=True` flag instead of
silently passing the coverage check on nothing. Rung 3 then gives a thin SPA
source one honest chance: rendered body is measured like any other — it only
matters if it is real text.
"""
from __future__ import annotations

import io
import os
import re
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass
from typing import Callable, Optional

import trafilatura

from .network import DEFAULT_MAX_BYTES, safe_fetch, validate_url

RENDER_SUSPECT_CHARS = 2500
PDF_MAX_PAGES = 200
PDF_MAX_TEXT_CHARS = DEFAULT_MAX_BYTES


@dataclass
class Extracted:
    url: str
    ok: bool
    method: str = "none"            # trafilatura | html-strip | pdf | plaintext | browser | none
    http_status: int = 0            # last HTTP status seen (0 = no response)
    title: str | None = None
    author: str | None = None
    date: str | None = None
    body: str = ""
    body_len: int = 0
    needs_render: bool = False      # True if body too thin → SPA suspected
    error: str | None = None


def extract(url: str, timeout: int = 15, min_body: int = 200) -> Extracted:
    checked = validate_url(url)
    if not checked.ok:
        return Extracted(url=url, ok=False, method="none", http_status=0, error=checked.error)
    fetched = safe_fetch(url, timeout=timeout, max_bytes=DEFAULT_MAX_BYTES)
    status = fetched.status
    if status >= 400 or status == 0:
        return Extracted(url=url, ok=False, method="none", http_status=status,
                         error=fetched.error or (f"HTTP {status}" if status else "no response"))
    content_type = _content_type(fetched.headers.get("content-type", ""))
    kind = _detect_content_kind(content_type, fetched.body)
    if kind == "pdf":
        return _extract_pdf(url, status, fetched.body, min_body=min_body)
    if kind == "plaintext":
        return _extract_plaintext(url, status, fetched.body, fetched.headers,
                                  min_body=min_body)
    if kind == "binary":
        return Extracted(url=url, ok=False, method="none", http_status=status,
                         error=f"unsupported binary content type {content_type or 'unknown'}")
    return _extract_html(url, status, fetched.body, fetched.headers, min_body=min_body)


def _content_type(value: str) -> str:
    return (value.split(";", 1)[0].strip().lower() if value else "")


def _has_pdf_signature(data: bytes) -> bool:
    # PDF permits a short binary/header prefix before %PDF-, but do not scan
    # arbitrary content: a textual page containing the token is not a PDF.
    prefix = data[:1024].lstrip(b" \t\r\n\xef\xbb\xbf")
    return prefix.startswith(b"%PDF-")


def _looks_like_html(data: bytes) -> bool:
    prefix = data[:2048].lstrip(b" \t\r\n\xef\xbb\xbf").lower()
    return prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"<!--"))


def _detect_content_kind(content_type: str, data: bytes) -> str:
    signature_pdf = _has_pdf_signature(data)
    if content_type == "application/pdf" or signature_pdf:
        # A claimed PDF without the magic header is rejected by the PDF rung;
        # keeping it as PDF gives the caller a visible, typed failure.
        return "pdf"
    if content_type in {"text/html", "application/xhtml+xml"} or _looks_like_html(data):
        return "html"
    if content_type.startswith("text/") or content_type in {
        "application/xml", "application/json", "application/ld+json",
    }:
        return "plaintext"
    if not content_type and b"\x00" not in data[:4096]:
        return "plaintext"
    return "binary"


def _decode_bytes(data: bytes, headers: dict[str, str]) -> str:
    content_type = headers.get("content-type", "")
    match = re.search(r"(?:^|;)\s*charset=([\w.-]+)", content_type, flags=re.IGNORECASE)
    encoding = match.group(1) if match else "utf-8"
    try:
        "".encode(encoding)
    except LookupError:
        encoding = "utf-8"
    return data.decode(encoding, "replace").lstrip("\ufeff")


def _extract_html(url: str, status: int, data: bytes, headers: dict[str, str],
                  *, min_body: int = 200) -> Extracted:
    html = _decode_bytes(data, headers)
    meta = _meta(html)
    # 1) article-scoped extraction
    try:
        md = trafilatura.extract(html, include_comments=False, include_tables=True,
                                 include_formatting=False)
    except Exception:  # noqa: BLE001 — raw strip remains an honest fallback
        md = None
    if md and len(md.strip()) >= min_body:
        body = md.strip()
        if len(body) < RENDER_SUSPECT_CHARS:
            return Extracted(url=url, ok=False, method="trafilatura", http_status=status,
                             title=meta.get("title"), author=meta.get("author"),
                             date=meta.get("date"), body=body, body_len=len(body),
                             needs_render=True,
                             error="thin extracted body; browser render may contain the full lesson")
        return Extracted(url=url, ok=True, method="trafilatura", http_status=status,
                         title=meta.get("title"), author=meta.get("author"),
                         date=meta.get("date"), body=body, body_len=len(body))

    # 2) raw HTML text strip (fallback).  It is intentionally bounded by the
    # network layer and never executes script content.
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) >= min_body:
        return Extracted(url=url, ok=True, method="html-strip", http_status=status,
                         title=_meta(html).get("title"), body=body, body_len=len(body))
    return Extracted(url=url, ok=False, method="none", http_status=status,
                     title=_meta(html).get("title"), body=body, body_len=len(body),
                     needs_render=True,
                     error="thin body; SPA suspected → needs browser render")


def _extract_plaintext(url: str, status: int, data: bytes, headers: dict[str, str],
                       *, min_body: int = 200) -> Extracted:
    body = re.sub(r"\s+", " ", _decode_bytes(data, headers)).strip()
    if len(body) >= min_body:
        return Extracted(url=url, ok=True, method="plaintext", http_status=status,
                         body=body, body_len=len(body))
    return Extracted(url=url, ok=False, method="plaintext", http_status=status,
                     body=body, body_len=len(body),
                     error=f"thin plaintext body ({len(body)} chars)")


def _extract_pdf(url: str, status: int, data: bytes, *, min_body: int = 200) -> Extracted:
    if not _has_pdf_signature(data):
        return Extracted(url=url, ok=False, method="pdf", http_status=status,
                         error="PDF content type claimed but %PDF- signature is missing")
    try:
        from pypdf import PdfReader
    except ImportError:
        return Extracted(url=url, ok=False, method="pdf", http_status=status,
                         error="PDF extraction requires optional dependency pypdf")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            return Extracted(url=url, ok=False, method="pdf", http_status=status,
                             error="encrypted PDF is not extracted")
        pieces: list[str] = []
        for page_number, page in enumerate(reader.pages):
            if page_number >= PDF_MAX_PAGES:
                break
            text = page.extract_text() or ""
            if text:
                pieces.append(text)
            if sum(len(piece) for piece in pieces) >= PDF_MAX_TEXT_CHARS:
                break
        body = re.sub(r"\s+", " ", "\n".join(pieces)).strip()
        if len(body) > PDF_MAX_TEXT_CHARS:
            body = body[:PDF_MAX_TEXT_CHARS]
        metadata = reader.metadata or {}
        title = str(metadata.get("/Title")) if metadata.get("/Title") else None
        author = str(metadata.get("/Author")) if metadata.get("/Author") else None
        date = str(metadata.get("/CreationDate")) if metadata.get("/CreationDate") else None
    except Exception as exc:  # noqa: BLE001 — malformed PDFs are first-class failures
        return Extracted(url=url, ok=False, method="pdf", http_status=status,
                         error=f"PDF extraction failed: {type(exc).__name__}: {exc}")
    if not body:
        return Extracted(url=url, ok=False, method="pdf", http_status=status,
                         title=title, author=author, date=date,
                         error="PDF contains no extractable text (empty or image-only)")
    if len(body) < min_body:
        return Extracted(url=url, ok=False, method="pdf", http_status=status,
                         title=title, author=author, date=date, body=body,
                         body_len=len(body), error=f"thin PDF text ({len(body)} chars)")
    return Extracted(url=url, ok=True, method="pdf", http_status=status,
                     title=title, author=author, date=date, body=body,
                     body_len=len(body))


# ---------- 3. browser render (SPA fallback, P0.5) ----------
#
# A *render backend* turns a URL into the page's rendered HTML:
#
#     RenderBackend = Callable[[str], Optional[str]]
#
# It returns the rendered HTML document, or None when rendering is impossible
# (no browser installed, launch failure, timeout). The backend is a plain
# callable — a runner or test can inject anything that matches, e.g. a
# Playwright/Chromium driver, a `subprocess`-based driver, or a stub.
#
# Security posture (SPEC §8): the rendered document is treated strictly as
# DATA — it is parsed for text locally, and nothing in it is ever executed.
#
# The no-op backend is the safe default: a machine without a headless browser
# still runs the whole pipeline, and the source stays honestly `needs-render`.
RenderBackend = Callable[[str], Optional[str]]


def _noop_render_backend(url: str) -> str | None:
    """Default backend when no headless browser is available: no render."""
    return None


def _playwright_render_backend(url: str) -> str | None:
    """Render through a fresh Playwright context and bounded safe fetches.

    Imported lazily so the dependency stays optional — machines without
    Playwright (or a usable browser) fall back to the no-op backend.  The
    browser never gets direct network access: every HTTP(S) request is fulfilled
    from ``safe_fetch``, which pins its validated destination and rejects
    redirects to unsafe addresses.  This avoids a Chromium DNS-rebinding gap.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        with sync_playwright() as pw:
            channels = _browser_channels()
            for channel in channels:
                browser = None
                context = None
                try:
                    launch_options = {"headless": True}
                    if channel is not None:
                        launch_options["channel"] = channel
                    browser = pw.chromium.launch(**launch_options)
                    # Fresh context: no profile, cookies, saved credentials, or
                    # external-account state.  Service workers cannot bypass
                    # the request interception boundary.
                    context = browser.new_context(service_workers="block",
                                                   accept_downloads=False)
                    page = context.new_page()
                    # WebSocket handshakes are not useful for extraction and
                    # must not become a browser-side network escape hatch.
                    page.route_web_socket("**/*", lambda websocket: websocket.close())
                    request_count = 0

                    def guard(route) -> None:
                        nonlocal request_count
                        request = route.request
                        request_count += 1
                        if request_count > 128 or request.method not in {"GET", "HEAD"}:
                            route.abort("blockedbyclient")
                            return
                        parsed = urllib.parse.urlsplit(request.url)
                        if (parsed.scheme.lower() not in {"http", "https"}
                                or request.resource_type in {"websocket", "eventsource", "download"}):
                            route.abort("blockedbyclient")
                            return
                        fetched = safe_fetch(request.url, timeout=15, max_bytes=2_000_000)
                        if fetched.status == 0 or fetched.truncated:
                            route.abort("blockedbyclient")
                            return
                        if "attachment" in fetched.headers.get("content-disposition", "").lower():
                            route.abort("blockedbyclient")
                            return
                        # safe_fetch asks for identity encoding; strip hop-by-hop
                        # framing headers before Playwright owns the response.
                        response_headers = {
                            key: value for key, value in fetched.headers.items()
                            if key not in {"connection", "content-length", "transfer-encoding"}
                        }
                        route.fulfill(status=fetched.status or 502,
                                      headers=response_headers, body=fetched.body)

                    page.route("**/*", guard)
                    page.goto(url, timeout=30_000, wait_until="domcontentloaded")
                    with suppress(Exception):  # networkidle is a bonus, not a gate
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    rendered = page.content()
                    return rendered[:DEFAULT_MAX_BYTES]
                except Exception as exc:  # noqa: BLE001
                    # A missing bundled browser commonly reaches this branch;
                    # try the configured/system channel next.
                    _ = exc
                finally:
                    if context is not None:
                        with suppress(Exception):
                            context.close()
                    if browser is not None:
                        with suppress(Exception):
                            browser.close()
            return None
    except Exception:  # noqa: BLE001 — any launch/nav failure is "no browser"
        return None


def _browser_channels() -> list[str | None]:
    """Return safe Playwright channel names, with a system-Chrome fallback."""
    configured = os.environ.get("OPEN_TUTOR_RENDER_CHANNEL", "").strip().lower()
    allowed = {"chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary",
               "msedge", "msedge-beta", "msedge-dev", "msedge-canary"}
    if configured and configured not in allowed:
        return []
    if configured:
        return [None if configured == "chromium" else configured]
    return [None, "chrome", "msedge"]


def default_render_backend() -> RenderBackend:
    """Best available render backend, resolved once.

    Order of preference: an explicit override (OPEN_TUTOR_RENDER_BACKEND=off
    forces the no-op path for CI/tests), then the Playwright/Chromium
    backend when importable, else the no-op. Resolution never raises.
    """
    import os

    if os.environ.get("OPEN_TUTOR_RENDER_BACKEND", "").lower() in ("off", "none", "0"):
        return _noop_render_backend
    if os.environ.get("OPEN_TUTOR_RENDER_BACKEND", "").lower() == "playwright":
        return _playwright_render_backend
    try:
        import playwright  # noqa: F401
    except ImportError:
        return _noop_render_backend
    return _playwright_render_backend


_render_backend: RenderBackend | None = None


def set_render_backend(backend: RenderBackend | None) -> None:
    """Inject the process-wide render backend (None = re-resolve default)."""
    global _render_backend
    _render_backend = backend


def get_render_backend() -> RenderBackend:
    global _render_backend
    if _render_backend is None:
        _render_backend = default_render_backend()
    return _render_backend


def browser_render(url: str, backend: RenderBackend | None = None,
                   timeout: int = 30) -> Extracted:
    """Ladder rung 3: render a thin (SPA) page in a browser, re-extract.

    `backend` is the injectable render callable — see `RenderBackend`. When
    omitted, the process-wide backend (Playwright default / no-op fallback) is
    used. The result keeps the exact `Extracted` shape; `method` is `"browser"`
    on success. A rendered body that is still thin stays `needs_render=True` —
    the bar is never lowered, only honestly measured.
    """
    checked = validate_url(url)
    if not checked.ok:
        return Extracted(url=url, ok=False, method="none", http_status=0,
                         needs_render=True, error=f"render blocked: {checked.error}")
    b = backend if backend is not None else get_render_backend()
    html = b(url)
    if not html:
        return Extracted(
            url=url, ok=False, method="none", http_status=0,
            needs_render=True,
            error="no render backend available (no headless browser installed)",
        )

    # Re-extract with the same article-focused ladder the static path trusts.
    rendered = html.encode("utf-8", "replace")
    extracted = _extract_html(url, 200, rendered, {"content-type": "text/html"})
    body = extracted.body
    title = extracted.title
    if body and len(body) >= 200:
        return Extracted(
            url=url, ok=True, method="browser", http_status=200,
            title=title, author=extracted.author, date=extracted.date,
            body=body, body_len=len(body),
        )

    # Rendered, but still thin: report the real char count, keep the flag.
    return Extracted(
        url=url, ok=False, method="browser", http_status=200, title=title,
        author=extracted.author, date=extracted.date, body=body,
        body_len=len(body), needs_render=True,
        error=(extracted.error or
               f"rendered body still thin ({len(body)} chars) → no concept text"),
    )


def _meta(html: str) -> dict[str, str | None]:
    def tag(name: str) -> str | None:
        m = re.search(
            rf"<meta\s+[^>]*(?:name|property)=['\"]{name}['\"][^>]*content=['\"]([^'\"]+)",
            html, flags=re.IGNORECASE) or \
        re.search(
            rf"<meta\s+[^>]*content=['\"]([^'\"]+)['\"][^>]*(?:name|property)=['\"]{name}",
            html, flags=re.IGNORECASE)
        return m.group(1) if m else None

    tm = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.DOTALL | re.IGNORECASE)
    return {"title": tm.group(1).strip() if tm else None,
            "author": tag("author") or tag("og:article:author"),
            "date": tag("article:published_time") or tag("date") or tag("pubdate")}


def extract_many(urls: list[str]) -> list[Extracted]:
    return [extract(u) for u in urls]


def coverage(body: str, keywords: list[str]) -> list[str]:
    b = body.lower()
    return [k for k in (k.lower() for k in keywords if k) if k in b]
