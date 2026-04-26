"""Firebase OAuth browser-flow stub for omi-cli.

**Status: not implemented in v0.1.0.** Tracking in v0.2.0.

The existing Omi auth pipeline (``backend/routers/auth.py``) is built around the
mobile app's deep-link redirect (``omi://auth/callback``) — the
``auth_callback.html`` template does not honor an arbitrary ``redirect_uri``,
so a localhost loopback redirect for the CLI cannot be wired up without a
backend change. Rather than ship a fragile bypass (e.g. PKCE direct against
Google with hardcoded client IDs that may not be configured for desktop usage),
we land an honest stub here. The CLI surface is wired so that adding the real
flow in v0.2.0 is purely additive — no command-line changes required.

For now, ``omi auth login --browser`` returns a clear, actionable error
pointing the user at the API-key flow.
"""

from __future__ import annotations

from omi_cli.errors import UsageError


def login_with_browser(profile_name: str) -> None:  # noqa: ARG001 — kept for forward-compat signature
    """Stub: surface a clear error. Real implementation tracked in v0.2.0."""
    raise UsageError(
        message="Browser OAuth login is coming in omi-cli v0.2.0",
        detail=(
            "For now, generate a developer API key at https://app.omi.me (Developer → API Keys), "
            "then run `omi auth login` and paste the key. Agents and CI users should prefer the "
            "API-key flow regardless — it works headlessly and supports scoped permissions."
        ),
    )


def refresh_id_token(profile_name: str) -> None:  # noqa: ARG001
    """Stub for the upcoming OAuth refresh flow."""
    raise UsageError(
        message="OAuth refresh is not available in v0.1.0",
        detail="This profile is using API-key auth — there is no token to refresh.",
    )
