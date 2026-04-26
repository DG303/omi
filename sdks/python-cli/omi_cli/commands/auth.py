"""``omi auth`` — login, logout, status."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Optional

import typer

from omi_cli import config as cfg
from omi_cli.auth import api_key as api_key_auth
from omi_cli.auth import oauth as oauth_auth
from omi_cli.auth.store import clear_credentials
from omi_cli.client import OmiClient
from omi_cli.errors import AuthError, CliError, UsageError

if TYPE_CHECKING:
    from omi_cli.main import AppContext


app = typer.Typer(no_args_is_help=True)


def _ctx(typer_ctx: typer.Context) -> "AppContext":
    """Type-narrowing accessor for the Typer context object."""
    obj = typer_ctx.obj
    if obj is None:  # pragma: no cover — defensive; root callback always sets it
        raise RuntimeError("AppContext not initialized")
    return obj  # type: ignore[no-any-return]


@app.command("login", help="Authenticate this profile with an Omi developer API key.")
def login(
    typer_ctx: typer.Context,
    api_key_arg: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="API key. If omitted, you will be prompted interactively (preferred — keeps the key out of shell history).",
    ),
    browser: bool = typer.Option(
        False,
        "--browser",
        help="Use the Firebase OAuth browser flow (coming in v0.2.0).",
    ),
) -> None:
    ctx = _ctx(typer_ctx)
    renderer = ctx.renderer

    if browser:
        # Surface the v0.2.0 stub message — clean, expected error path.
        oauth_auth.login_with_browser(ctx.profile_name)
        return  # unreachable; the stub raises

    if api_key_arg is None:
        # Read from stdin if not a TTY (handy for piping `omi auth login < key.txt`).
        if not sys.stdin.isatty():
            api_key_arg = sys.stdin.read().strip()
        else:
            api_key_arg = typer.prompt("Paste your Omi developer API key", hide_input=True).strip()

    profile = api_key_auth.login_with_api_key(ctx.profile_name, api_key_arg, api_base=ctx.api_base_override)

    # Optional sanity check: reach out and confirm the key works. We do this on
    # a tolerant endpoint (memories list with limit=1) so a missing scope on the
    # primary domain doesn't make login appear to fail.
    try:
        with OmiClient(profile, verbose=ctx.verbose) as client:
            client.get("/v1/dev/user/memories", params={"limit": 1})
    except AuthError as exc:
        # Roll back the bad credential so the user isn't stuck with a broken profile.
        clear_credentials(ctx.profile_name)
        raise exc
    except CliError as exc:
        # Network / rate-limit / non-auth issues: don't roll back, but warn.
        renderer.warn(f"Could not verify the key right now ({exc.message}). It is stored — try again shortly.")

    renderer.success(f"Logged in as profile [bold]{profile.name}[/bold] ({profile.masked_credential()}).")
    if ctx.renderer.json_mode:
        renderer.emit({"profile": profile.name, "auth_method": profile.auth_method, "api_base": profile.api_base})


@app.command("logout", help="Clear credentials for the active profile.")
def logout(typer_ctx: typer.Context) -> None:
    ctx = _ctx(typer_ctx)
    cleared = clear_credentials(ctx.profile_name)
    if cleared:
        ctx.renderer.success(f"Cleared credentials for profile [bold]{ctx.profile_name}[/bold].")
    else:
        ctx.renderer.warn(f"Profile [bold]{ctx.profile_name}[/bold] was not authenticated.")
    if ctx.renderer.json_mode:
        ctx.renderer.emit({"profile": ctx.profile_name, "logged_out": cleared})


@app.command("status", help="Show the auth state of the active profile.")
def status(typer_ctx: typer.Context) -> None:
    ctx = _ctx(typer_ctx)
    profile = ctx.get_profile()
    payload = {
        "profile": profile.name,
        "authenticated": profile.is_authenticated(),
        "auth_method": profile.auth_method,
        "api_base": profile.api_base,
        "credential": profile.masked_credential(),
    }
    ctx.renderer.emit(payload, title="omi auth status")


@app.command("whoami", help="Resolve the current credential against the API and display identity info.")
def whoami(typer_ctx: typer.Context) -> None:
    ctx = _ctx(typer_ctx)
    # The public dev surface doesn't expose a /me endpoint, but a memories list
    # round-trip with the credential confirms the key is alive and identifies
    # the user implicitly (the count belongs to *this* user). This keeps the
    # command useful without inventing new backend routes.
    with ctx.make_client() as client:
        memories = client.get("/v1/dev/user/memories", params={"limit": 1})
    payload = {
        "profile": ctx.profile_name,
        "credential": ctx.get_profile().masked_credential(),
        "api_base": ctx.get_profile().api_base,
        "owns_memories": isinstance(memories, list),
    }
    ctx.renderer.emit(payload, title="omi whoami")


@app.command("refresh", help="Refresh the OAuth ID token (no-op for API-key profiles).")
def refresh(typer_ctx: typer.Context) -> None:
    ctx = _ctx(typer_ctx)
    profile = ctx.get_profile()
    if profile.auth_method != "oauth":
        raise UsageError(
            message="Nothing to refresh",
            detail=(
                f"Profile '{profile.name}' uses API-key auth — there is no token to refresh. "
                "API keys are long-lived; rotate them in the Omi web app if needed."
            ),
        )
    oauth_auth.refresh_id_token(profile.name)


def _ensure_authenticated(profile: cfg.Profile) -> None:  # pragma: no cover — utility for sibling commands
    if not profile.is_authenticated():
        raise UsageError(
            message="Not authenticated",
            detail=f"Profile '{profile.name}' has no credentials. Run `omi auth login`.",
        )
