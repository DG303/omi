"""Tests for ``backend.routers.auth._validate_redirect_uri``.

The validator must accept every ``redirect_uri`` shape the Omi clients
already use today (mobile, desktop, named-bundle desktop builds, CLI) and
reject anything that could leak the OAuth code off-device. These tests
serve as a regression guard — if the allowlist tightens in a way that
rejects an existing client's URI, CI will fail here before any deploy.

Mapping to real-world clients:

* ``omi://auth/callback``                — Flutter app (``app/lib/services/auth_service.dart``)
* ``omi-computer://auth/callback``       — desktop prod build
* ``omi-computer-dev://auth/callback``   — desktop dev build (``Desktop/Info.plist``)
* ``omi-fix-rewind://auth/callback``     — example named test bundle
* ``com.omi.app://auth/callback``        — reverse-DNS form (RFC 8252-recommended)
* ``http://127.0.0.1:PORT/callback``     — omi-cli loopback server
* ``http://localhost:PORT/callback``     — omi-cli loopback (alt)
* ``http://[::1]:PORT/callback``         — IPv6 loopback
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

# Backend modules expect ENCRYPTION_SECRET to be set at import time.
os.environ.setdefault(
    "ENCRYPTION_SECRET",
    "omi_test_secret_for_redirect_uri_validation_unit_test_only",
)

# Allow importing ``backend.routers.auth`` without running the full backend
# entrypoint — same trick the rest of tests/unit uses.
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from routers.auth import _validate_redirect_uri  # noqa: E402

# ---------------------------------------------------------------------------
# Acceptance — every shape an existing Omi client uses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        # Flutter mobile
        "omi://auth/callback",
        # Desktop prod
        "omi-computer://auth/callback",
        # Desktop dev (Desktop/Info.plist)
        "omi-computer-dev://auth/callback",
        # Named test bundle (CLAUDE.md "omi-{anything}" convention)
        "omi-fix-rewind://auth/callback",
        # Reverse-DNS custom scheme (RFC 8252-recommended)
        "com.omi.app://auth/callback",
        # CLI loopback — IPv4 numeric
        "http://127.0.0.1:8765/callback",
        # CLI loopback — hostname
        "http://localhost:5000/callback",
        # CLI loopback — IPv6
        "http://[::1]:5000/callback",
        # Custom scheme without a path (degenerate but valid)
        "omi://",
        # Custom scheme carrying its own state in the query
        "omi-computer://auth/callback?from=settings",
    ],
)
def test_validator_accepts_every_known_client_shape(uri: str) -> None:
    # Should not raise for anything an Omi client actually sends.
    _validate_redirect_uri(uri)


# ---------------------------------------------------------------------------
# Rejection — every shape a malicious caller might try
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        # Empty / whitespace
        "",
        "   ",
        # Garbage with no scheme
        "auth/callback",
        # https — would leak code off-device
        "https://attacker.example.com/cb",
        "https://localhost/cb",  # https NEVER allowed, even on loopback
        # http — non-loopback hostname rejected
        "http://attacker.example.com/cb",
        "http://omi.me/cb",
        "http://192.168.1.42/cb",
        # Browser-executable schemes
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "blob:http://localhost/abc",
        "filesystem:http://localhost/abc",
        "about:blank",
        # Malformed scheme
        "://x",
        "1omi://auth/callback",  # scheme must start with a letter
        "omi$://auth/callback",  # ``$`` not allowed in scheme
        # Non-ASCII letters: RFC 3986 forbids these in scheme names. Python's
        # ``str.isalpha`` would accept them — we explicitly use ASCII-only.
        "ômi://auth/callback",
        "омi://auth/callback",  # cyrillic 'о'
    ],
)
def test_validator_rejects_dangerous_or_malformed_uris(uri: str) -> None:
    with pytest.raises(HTTPException) as info:
        _validate_redirect_uri(uri)
    assert info.value.status_code == 400


def test_https_is_never_accepted_even_for_loopback() -> None:
    """RFC 8252 §7.3 explicitly mandates HTTP (not HTTPS) for loopback."""
    with pytest.raises(HTTPException):
        _validate_redirect_uri("https://127.0.0.1:5000/callback")


def test_http_remote_host_rejection_message_mentions_loopback() -> None:
    with pytest.raises(HTTPException) as info:
        _validate_redirect_uri("http://attacker.example.com/cb")
    assert "loopback" in info.value.detail.lower()


def test_default_omi_redirect_unchanged() -> None:
    """The mobile app's exact redirect MUST keep working — most-load-bearing case."""
    _validate_redirect_uri("omi://auth/callback")
