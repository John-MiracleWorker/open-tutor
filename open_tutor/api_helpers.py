"""Small, dependency-free validation helpers shared by the local API."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def valid_identifier(value: str) -> bool:
    return bool(isinstance(value, str) and IDENTIFIER_RE.fullmatch(value))


def valid_uuid_hex(value: str) -> bool:
    return bool(isinstance(value, str) and UUID_HEX_RE.fullmatch(value))


def local_endpoint(value: str | None) -> bool:
    """Accept only loopback HTTP(S) provider endpoints."""
    if not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if (parsed.scheme not in {"http", "https"} or parsed.hostname is None
            or parsed.username or parsed.password or parsed.fragment):
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in LOCAL_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local
                or address in ipaddress.ip_network("100.64.0.0/10"))


def bounded_text(value: str, limit: int, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > limit:
        raise ValueError(f"{name} exceeds the {limit}-character limit")
    return value.strip()
