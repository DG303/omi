"""Tests for the Typer root: --version, --help, global-flag plumbing."""

from __future__ import annotations

import json

from omi_cli import __version__
from omi_cli.main import app


def test_version_flag(cli_runner) -> None:
    result = cli_runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_version_subcommand(cli_runner) -> None:
    result = cli_runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_all_top_level_commands(cli_runner) -> None:
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("auth", "config", "memory", "conversation", "action-item", "goal", "version"):
        assert cmd in result.stdout


def test_auth_status_unauthenticated_in_json(config_path, cli_runner) -> None:
    result = cli_runner.invoke(app, ["--json", "auth", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["authenticated"] is False
    assert payload["auth_method"] is None
