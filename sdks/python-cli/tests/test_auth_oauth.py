"""Tests for the OAuth stub (v0.1.0)."""

from __future__ import annotations

import pytest

from omi_cli.auth import oauth
from omi_cli.errors import UsageError


def test_login_with_browser_raises_v0_2_message() -> None:
    with pytest.raises(UsageError) as info:
        oauth.login_with_browser("default")
    msg = str(info.value).lower()
    assert "v0.2.0" in msg or "0.2.0" in msg
    assert "api key" in msg or "api-key" in msg


def test_refresh_id_token_raises_for_v0_1() -> None:
    with pytest.raises(UsageError):
        oauth.refresh_id_token("default")
