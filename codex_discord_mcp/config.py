"""Runtime configuration loaded from environment."""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_ENV_FILE = Path.home() / ".codex-disc-mcp" / ".env"
_CODEX_CONFIG_FILE = Path.home() / ".codex" / "config.toml"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def _getenv(
    env: Mapping[str, str],
    file_env: Mapping[str, str],
    key: str,
    default: str | None = None,
) -> str | None:
    return env.get(key) or file_env.get(key) or default


def _parse_int_set(raw: str | None) -> frozenset[int]:
    if not raw:
        return frozenset()
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            continue
    return frozenset(out)


def _parse_optional_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _parse_workspaces(raw: str | None, default: Path) -> dict[str, Path]:
    if not raw:
        return {"default": default}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"default": default}
    return {name: Path(p).resolve() for name, p in data.items()}


def _read_codex_cli_defaults(path: Path = _CODEX_CONFIG_FILE) -> dict[str, str]:
    if not path.is_file():
        return {}
    defaults: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            break
        match = re.match(r'^(model|model_reasoning_effort)\s*=\s*["\']?([^"\']+)["\']?$', line)
        if match:
            defaults[match.group(1)] = match.group(2).strip()
    return defaults


@dataclass(frozen=True)
class Settings:
    discord_token: str
    discord_channel_ids: frozenset[int]
    discord_allowed_users: frozenset[int]
    discord_allowed_roles: frozenset[int]
    codex_workspaces: dict[str, Path]
    codex_default_workspace: str
    codex_model: str | None
    codex_reasoning_effort: str | None
    codex_timeout: int
    codex_executable: str
    codex_max_prompt_chars: int
    db_path: Path
    log_level: str
    log_json: bool
    health_port: int
    metrics_port: int
    enable_health: bool
    enable_metrics: bool
    require_approval: bool
    redact_secrets: bool
    mcp_caller_user_id: int | None
    codex_discord_runtime: str
    codex_parent_pid: int | None

    @classmethod
    def from_env(cls, env_file: Path | None = _DEFAULT_ENV_FILE) -> Settings:
        file_env = _read_env_file(env_file) if env_file else {}
        token = _getenv(os.environ, file_env, "DISCORD_TOKEN")
        if not token:
            raise RuntimeError("DISCORD_TOKEN is required")

        default_workdir = Path(
            _getenv(os.environ, file_env, "CODEX_WORKDIR", os.getcwd()) or os.getcwd(),
        ).resolve()
        workspaces = _parse_workspaces(
            _getenv(os.environ, file_env, "CODEX_WORKSPACES"),
            default_workdir,
        )
        default_ws = _getenv(
            os.environ, file_env, "CODEX_DEFAULT_WORKSPACE", next(iter(workspaces.keys())),
        )
        if default_ws not in workspaces:
            workspaces[default_ws] = default_workdir

        codex_defaults = _read_codex_cli_defaults()

        return cls(
            discord_token=token,
            discord_channel_ids=_parse_int_set(_getenv(os.environ, file_env, "DISCORD_CHANNEL_ID"))
                or _parse_int_set(_getenv(os.environ, file_env, "DISCORD_CHANNEL_IDS")),
            discord_allowed_users=_parse_int_set(
                _getenv(os.environ, file_env, "DISCORD_ALLOWED_USERS"),
            ),
            discord_allowed_roles=_parse_int_set(
                _getenv(os.environ, file_env, "DISCORD_ALLOWED_ROLES"),
            ),
            codex_workspaces=workspaces,
            codex_default_workspace=default_ws,
            codex_model=_getenv(os.environ, file_env, "CODEX_MODEL") or codex_defaults.get("model"),
            codex_reasoning_effort=(
                _getenv(os.environ, file_env, "CODEX_REASONING_EFFORT")
                or _getenv(os.environ, file_env, "CODEX_MODEL_REASONING_EFFORT")
                or codex_defaults.get("model_reasoning_effort")
            ),
            codex_timeout=int(
                _getenv(os.environ, file_env, "CODEX_TIMEOUT_SECONDS", "300") or "300",
            ),
            codex_executable=_getenv(os.environ, file_env, "CODEX_EXECUTABLE", "codex") or "codex",
            codex_max_prompt_chars=int(
                _getenv(os.environ, file_env, "CODEX_MAX_PROMPT_CHARS", "8000") or "8000",
            ),
            db_path=Path(
                _getenv(os.environ, file_env, "CODEX_DB_PATH", "codex_discord.db")
                or "codex_discord.db",
            ).resolve(),
            log_level=_getenv(os.environ, file_env, "LOG_LEVEL", "INFO") or "INFO",
            log_json=_getenv(os.environ, file_env, "LOG_JSON", "0") == "1",
            health_port=int(_getenv(os.environ, file_env, "HEALTH_PORT", "8080") or "8080"),
            metrics_port=int(_getenv(os.environ, file_env, "METRICS_PORT", "9090") or "9090"),
            enable_health=_getenv(os.environ, file_env, "ENABLE_HEALTH", "1") == "1",
            enable_metrics=_getenv(os.environ, file_env, "ENABLE_METRICS", "0") == "1",
            require_approval=_getenv(os.environ, file_env, "CODEX_REQUIRE_APPROVAL", "0") == "1",
            redact_secrets=_getenv(os.environ, file_env, "CODEX_REDACT_SECRETS", "1") == "1",
            mcp_caller_user_id=_parse_optional_int(
                _getenv(os.environ, file_env, "MCP_CALLER_USER_ID"),
            ),
            codex_discord_runtime=(
                _getenv(os.environ, file_env, "CODEX_DISCORD_RUNTIME", "exec") or "exec"
            ).lower(),
            codex_parent_pid=_parse_optional_int(
                _getenv(os.environ, file_env, "CODEX_PARENT_PID"),
            ),
        )

    def workspace_path(self, name: str | None) -> Path:
        if not name:
            return self.codex_workspaces[self.codex_default_workspace]
        if name not in self.codex_workspaces:
            raise KeyError(f"Unknown workspace '{name}'")
        return self.codex_workspaces[name]

    def is_user_allowed(self, user_id: int, role_ids: list[int] | None = None) -> bool:
        if not self.discord_allowed_users and not self.discord_allowed_roles:
            return True
        if user_id in self.discord_allowed_users:
            return True
        if role_ids and any(r in self.discord_allowed_roles for r in role_ids):
            return True
        return False

    def is_channel_allowed(self, channel_id: int) -> bool:
        if not self.discord_channel_ids:
            return True
        return channel_id in self.discord_channel_ids
