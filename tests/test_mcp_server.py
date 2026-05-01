from __future__ import annotations

import os

import pytest

from codex_discord_mcp.config import Settings
from codex_discord_mcp.mcp_server import _require_mcp_caller


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("DISCORD_", "CODEX_", "LOG_", "ENABLE_", "HEALTH_", "METRICS_", "MCP_")):
            monkeypatch.delenv(key, raising=False)


def test_mcp_caller_open_without_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    s = Settings.from_env(env_file=None)
    assert _require_mcp_caller(s) == 0


def test_mcp_caller_required_with_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "123")
    s = Settings.from_env(env_file=None)
    with pytest.raises(PermissionError, match="MCP_CALLER_USER_ID"):
        _require_mcp_caller(s)


def test_mcp_caller_checks_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "123")
    monkeypatch.setenv("MCP_CALLER_USER_ID", "123")
    s = Settings.from_env(env_file=None)
    assert _require_mcp_caller(s) == 123
