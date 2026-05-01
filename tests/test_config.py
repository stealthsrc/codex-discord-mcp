from __future__ import annotations

import os
from pathlib import Path

import pytest

from codex_discord_mcp.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("DISCORD_", "CODEX_", "LOG_", "ENABLE_", "HEALTH_", "METRICS_", "MCP_")):
            monkeypatch.delenv(key, raising=False)


def test_requires_token() -> None:
    with pytest.raises(RuntimeError, match="DISCORD_TOKEN"):
        Settings.from_env(env_file=None)


def test_loads_token_from_env_file() -> None:
    env_file = Path(__file__).with_name(".test_config.env")
    try:
        env_file.write_text("DISCORD_TOKEN=file-token\n", encoding="utf-8")
        s = Settings.from_env(env_file=env_file)
        assert s.discord_token == "file-token"
    finally:
        env_file.unlink(missing_ok=True)


def test_process_env_overrides_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = Path(__file__).with_name(".test_config.env")
    try:
        env_file.write_text("DISCORD_TOKEN=file-token\n", encoding="utf-8")
        monkeypatch.setenv("DISCORD_TOKEN", "process-token")
        s = Settings.from_env(env_file=env_file)
        assert s.discord_token == "process-token"
    finally:
        env_file.unlink(missing_ok=True)


def test_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = Path.cwd()
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("CODEX_WORKDIR", str(workdir))
    s = Settings.from_env(env_file=None)
    assert s.discord_token == "tok"
    assert s.codex_default_workspace == "default"
    assert s.codex_workspaces["default"] == workdir.resolve()


def test_channel_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_IDS", "111,222, 333")
    s = Settings.from_env(env_file=None)
    assert s.is_channel_allowed(111)
    assert s.is_channel_allowed(333)
    assert not s.is_channel_allowed(444)


def test_user_role_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "1,2")
    monkeypatch.setenv("DISCORD_ALLOWED_ROLES", "10")
    s = Settings.from_env(env_file=None)
    assert s.is_user_allowed(1)
    assert not s.is_user_allowed(99)
    assert s.is_user_allowed(99, role_ids=[10])
    assert not s.is_user_allowed(99, role_ids=[5])


def test_mcp_caller_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("MCP_CALLER_USER_ID", "123")
    s = Settings.from_env(env_file=None)
    assert s.mcp_caller_user_id == 123


def test_discord_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("CODEX_DISCORD_RUNTIME", "relay")
    s = Settings.from_env(env_file=None)
    assert s.codex_discord_runtime == "relay"


def test_parent_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("CODEX_PARENT_PID", "123")
    s = Settings.from_env(env_file=None)
    assert s.codex_parent_pid == 123


def test_no_allowlist_means_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    s = Settings.from_env(env_file=None)
    assert s.is_user_allowed(123)
    assert s.is_channel_allowed(456)


def test_multiple_workspaces(monkeypatch: pytest.MonkeyPatch) -> None:
    a = Path.cwd() / "codex_discord_mcp"
    b = Path.cwd() / "tests"
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv(
        "CODEX_WORKSPACES",
        f'{{"alpha": "{a}", "beta": "{b}"}}'.replace("\\", "\\\\"),
    )
    monkeypatch.setenv("CODEX_DEFAULT_WORKSPACE", "alpha")
    s = Settings.from_env()
    assert s.workspace_path("alpha") == a.resolve()
    assert s.workspace_path("beta") == b.resolve()
    assert s.workspace_path(None) == a.resolve()
