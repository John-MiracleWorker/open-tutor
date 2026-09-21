"""Bounded, fail-closed public-source networking.

This module is intentionally small and has no model or cloud-provider code.  A
URL is data supplied by a source adapter or a user; it is never treated as an
instruction.  Host validation is performed before every request and every
redirect is checked again.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.parse
from dataclasses import dataclass

USER_AGENT = "open-tutor-research/0.2 (+local-first educational research)"
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_BYTES = 8_000_000
MAX_REDIRECTS = 5


@dataclass(frozen=True)
class URLValidation:
    ok: bool
    url: str
    error: str | None = None
    host: str | None = None


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    final_url: str
    error: str | None = None
    truncated: bool = False


_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "instance-data.ec2.internal",
}


def _literal_ip(host: str):
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _unsafe_ip(ip) -> bool:
    """Return whether an address is not a public, globally routable target.

    ``is_private`` is deliberately not the policy here.  In particular,
    RFC-6598 CGNAT/Tailscale space (100.64.0.0/10) is neither private nor
    global according to :mod:`ipaddress`, and must still be rejected for the
    public-source fetcher.  IPv4-mapped IPv6 literals are normalized before
    applying the same rule.
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(ip.is_multicast or not ip.is_global)


def _resolve_public_address(host: str, port: int) -> tuple[str | None, str | None]:
    """Resolve *host* once and return a validated address to connect to.

    Returning the address is important: validating with ``getaddrinfo`` and
    then handing the hostname to another resolver leaves a DNS-rebinding
    window.  Callers must connect to this exact address.
    """
    literal = _literal_ip(host)
    if literal is not None:
        if _unsafe_ip(literal):
            return None, f"private or special address blocked: {host}"
        return str(literal), None
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        return None, f"hostname did not resolve: {exc}"
    if not answers:
        return None, f"hostname did not resolve: {host}"
    # Reject the hostname if *any* answer is unsafe.  Choosing a seemingly
    # safe answer from a mixed response would let the resolver/network choose
    # a private destination later.
    addresses: list[str] = []
    for answer in answers:
        candidate = answer[4][0]
        resolved = _literal_ip(candidate)
        if resolved is None or _unsafe_ip(resolved):
            return None, f"hostname resolves to blocked address: {candidate}"
        addresses.append(str(resolved))
    return addresses[0], None


def validate_url(url: str, *, resolve: bool = False) -> URLValidation:
    """Validate an HTTP(S) URL and reject local/private/metadata targets.

    ``resolve=False`` is suitable for validating an adapter-produced URL.  The
    fetch path applies this syntax/policy check, then resolves exactly once and
    connects to that returned address, repeating the process for every redirect.
    """
    if not isinstance(url, str) or len(url) > 2048:
        return URLValidation(False, str(url), "URL is missing or too long")
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        return URLValidation(False, url, f"invalid URL: {exc}")
    if parsed.scheme.lower() not in {"http", "https"}:
        return URLValidation(False, url, "only http and https URLs are allowed")
    if parsed.username or parsed.password:
        return URLValidation(False, url, "userinfo in URLs is not allowed")
    host = parsed.hostname
    if not host:
        return URLValidation(False, url, "URL has no hostname")
    try:
        port = parsed.port
    except ValueError as exc:
        return URLValidation(False, url, f"invalid port: {exc}")
    if port is not None and not 1 <= port <= 65535:
        return URLValidation(False, url, "port is outside 1..65535")
    host = host.rstrip(".").lower()
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        return URLValidation(False, url, f"local or metadata hostname blocked: {host}", host)
    literal = _literal_ip(host)
    if literal is not None:
        if _unsafe_ip(literal):
            return URLValidation(False, url, f"private or special address blocked: {host}", host)
        return URLValidation(True, url, host=host)
    if resolve:
        resolve_port = port or (443 if parsed.scheme.lower() == "https" else 80)
        _, error = _resolve_public_address(host, resolve_port)
        if error:
            return URLValidation(False, url, error, host)
    return URLValidation(True, url, host=host)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose TCP peer is pinned but TLS uses the URL host."""

    def __init__(self, address: str, port: int, server_hostname: str,
                 *, timeout: float):
        # ``host`` is the address so HTTPConnection connects to that exact
        # peer.  Keep the URL hostname separately for certificate/SNI checks.
        super().__init__(address, port=port, timeout=timeout,
                         context=ssl.create_default_context())
        self._server_hostname = server_hostname

    def connect(self):
        http.client.HTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(self.sock,
                                               server_hostname=self._server_hostname)


def _request_once(url: str, address: str, *, timeout: float,
                  max_bytes: int) -> FetchResult:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    # Include the port in Host only when it is non-default, as a normal client
    # would.  The TCP connection itself always uses the validated address.
    host_header = host
    if ":" in host:
        host_header = f"[{host}]"
    if parsed.port and parsed.port != (443 if scheme == "https" else 80):
        host_header = f"{host_header}:{port}"
    path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    headers = {"Host": host_header, "User-Agent": USER_AGENT,
               "Accept-Encoding": "identity"}
    connection: http.client.HTTPConnection
    if scheme == "https":
        connection = _PinnedHTTPSConnection(address, port, host, timeout=timeout)
    else:
        connection = http.client.HTTPConnection(address, port=port, timeout=timeout)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        status = int(response.status or 0)
        response_headers = {str(k).lower(): str(v) for k, v in response.getheaders()}
        data = response.read(max_bytes + 1)
        return FetchResult(url, status, response_headers, data[:max_bytes], url,
                           truncated=len(data) > max_bytes)
    finally:
        connection.close()


def safe_fetch(url: str, *, timeout: int = DEFAULT_TIMEOUT,
               max_bytes: int = DEFAULT_MAX_BYTES) -> FetchResult:
    """Fetch bounded bytes from a public URL, preserving real HTTP status.

    Redirects are followed manually so their destinations receive the same
    SSRF checks.  A response larger than ``max_bytes`` is returned truncated and
    marked as such; callers must not pretend that it is complete text.
    """
    if max_bytes < 1:
        return FetchResult(url, 0, {}, b"", url, "max_bytes must be positive")
    current = url
    request_timeout = max(1, min(int(timeout), 120))
    for _ in range(MAX_REDIRECTS + 1):
        # Syntax/hostname policy first, then resolve exactly once below.  Do
        # not resolve in validate_url and again for the connection: the second
        # lookup would reopen the DNS-rebinding window this fetcher closes.
        checked = validate_url(current)
        if not checked.ok:
            return FetchResult(url, 0, {}, b"", current, checked.error)
        try:
            parsed = urllib.parse.urlsplit(current)
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
            address, resolve_error = _resolve_public_address(parsed.hostname or "", port)
            if resolve_error or address is None:
                return FetchResult(url, 0, {}, b"", current, resolve_error)
            result = _request_once(current, address, timeout=request_timeout,
                                   max_bytes=max_bytes)
            location = result.headers.get("location")
            if result.status in {301, 302, 303, 307, 308} and location:
                current = urllib.parse.urljoin(current, location)
                continue
            return FetchResult(url, result.status, result.headers, result.body, current,
                               truncated=result.truncated,
                               error=(f"HTTP {result.status}" if result.status >= 400 else None))
        except Exception as exc:  # noqa: BLE001
            return FetchResult(url, 0, {}, b"", current, f"{type(exc).__name__}: {exc}")
    return FetchResult(url, 0, {}, b"", current, f"too many redirects (>{MAX_REDIRECTS})")


def fetch_text(url: str, *, timeout: int = DEFAULT_TIMEOUT,
               max_bytes: int = DEFAULT_MAX_BYTES,
               encoding: str = "utf-8") -> tuple[FetchResult, str]:
    result = safe_fetch(url, timeout=timeout, max_bytes=max_bytes)
    return result, result.body.decode(encoding, "replace")
