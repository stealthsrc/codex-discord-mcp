"""FastMCP server exposing Discord operations + Codex bridge."""
from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from .codex_client import CodexClient
from .config import Settings
from .discord_rest import DiscordREST
from .logging_setup import get_logger, setup_logging
from .security import preview, prompt_hash, redact, validate_prompt
from .storage import Storage

log = get_logger(__name__)
mcp = FastMCP("codex-discord-mcp")

_settings: Settings | None = None
_storage: Storage | None = None
_rest: DiscordREST | None = None


def _bootstrap() -> tuple[Settings, Storage, DiscordREST]:
    global _settings, _storage, _rest
    if _settings is None:
        _settings = Settings.from_env()
    if _storage is None:
        _storage = Storage(_settings.db_path)
    if _rest is None:
        _rest = DiscordREST(_settings.discord_token)
    return _settings, _storage, _rest


def _require_mcp_caller(settings: Settings) -> int:
    user_id = settings.mcp_caller_user_id
    if not settings.discord_allowed_users and not settings.discord_allowed_roles:
        return user_id or 0
    if user_id is None:
        raise PermissionError("MCP_CALLER_USER_ID is required when allowlists are configured")
    if not settings.is_user_allowed(user_id):
        raise PermissionError("MCP caller is not allowed")
    return user_id


# ----------------------- Discord tools ----------------------- #

@mcp.tool()
async def send_discord_message(channel_id: int, content: str) -> str:
    """Send a message to a Discord channel. Returns message id."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    msg = await rest.send_message(channel_id, content)
    return str(msg.get("id", "sent"))


@mcp.tool()
async def edit_discord_message(channel_id: int, message_id: int, content: str) -> str:
    """Edit an existing message authored by the bot."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    msg = await rest.edit_message(channel_id, message_id, content)
    return str(msg.get("id", "edited"))


@mcp.tool()
async def delete_discord_message(channel_id: int, message_id: int) -> str:
    """Delete a message authored by the bot."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    await rest.delete_message(channel_id, message_id)
    return "deleted"


@mcp.tool()
async def read_discord_messages(channel_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """Read up to 100 recent messages from a channel."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    msgs = await rest.list_messages(channel_id, limit=limit)
    return [
        {
            "id": m.get("id"),
            "author": (m.get("author") or {}).get("username"),
            "author_id": (m.get("author") or {}).get("id"),
            "content": m.get("content"),
            "timestamp": m.get("timestamp"),
        }
        for m in (msgs or [])
    ]


@mcp.tool()
async def add_discord_reaction(channel_id: int, message_id: int, emoji: str) -> str:
    """Add a unicode reaction (e.g. '✅') to a message."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    await rest.add_reaction(channel_id, message_id, emoji)
    return "reacted"


@mcp.tool()
async def create_discord_thread(channel_id: int, name: str) -> str:
    """Create a public thread in a text channel. Returns thread id."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    thread = await rest.create_thread(channel_id, name)
    return str(thread.get("id", "created"))


@mcp.tool()
async def list_discord_channels(guild_id: int) -> list[dict[str, Any]]:
    """List channels in a guild."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    chans = await rest.list_guild_channels(guild_id)
    return [
        {"id": c.get("id"), "name": c.get("name"), "type": c.get("type")}
        for c in (chans or [])
    ]


@mcp.tool()
async def send_discord_dm(user_id: int, content: str) -> str:
    """Send a direct message to a user."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    msg = await rest.send_dm(user_id, content)
    return str(msg.get("id", "sent"))


@mcp.tool()
async def await_discord_reply(
    channel_id: int, after_message_id: int, timeout_seconds: int = 120,
) -> dict[str, Any] | None:
    """Poll a channel until a new message appears after `after_message_id`."""
    settings, _, rest = _bootstrap()
    _require_mcp_caller(settings)
    deadline = asyncio.get_event_loop().time() + max(5, min(timeout_seconds, 600))
    last_seen = after_message_id
    while asyncio.get_event_loop().time() < deadline:
        msgs = await rest.list_messages(channel_id, limit=20)
        for m in reversed(msgs or []):
            try:
                mid = int(m.get("id", 0))
            except ValueError:
                continue
            if mid > last_seen:
                return {
                    "id": m.get("id"),
                    "author": (m.get("author") or {}).get("username"),
                    "author_id": (m.get("author") or {}).get("id"),
                    "content": m.get("content"),
                    "timestamp": m.get("timestamp"),
                }
        await asyncio.sleep(2)
    return None


@mcp.tool()
async def read_codex_session_messages(
    channel_id: int, limit: int = 20,
) -> list[dict[str, Any]]:
    """Read persisted Codex conversation messages for a Discord channel/thread."""
    settings, storage, _ = _bootstrap()
    _require_mcp_caller(settings)
    return await storage.session_messages(channel_id, limit=limit)


@mcp.tool()
async def await_codex_session_message(
    channel_id: int,
    after_message_id: int,
    role: str | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any] | None:
    """Wait for next persisted Codex conversation message in a channel/thread."""
    settings, storage, _ = _bootstrap()
    _require_mcp_caller(settings)
    deadline = asyncio.get_event_loop().time() + max(5, min(timeout_seconds, 600))
    while asyncio.get_event_loop().time() < deadline:
        msg = await storage.next_message(channel_id, after_message_id, role=role)
        if msg:
            return msg
        await asyncio.sleep(2)
    return None


@mcp.tool()
async def await_discord_command(
    after_message_id: int = 0,
    timeout_seconds: int = 120,
) -> dict[str, Any] | None:
    """Wait for next relayed Discord user message across sessions."""
    settings, storage, _ = _bootstrap()
    _require_mcp_caller(settings)
    deadline = asyncio.get_event_loop().time() + max(5, min(timeout_seconds, 600))
    while asyncio.get_event_loop().time() < deadline:
        msg = await storage.next_message_any(after_message_id, role="user")
        if msg:
            msg["session_id"] = f"discord-{msg['thread_id']}"
            return msg
        await asyncio.sleep(2)
    return None


@mcp.tool()
async def reply_discord_command(channel_id: int, content: str) -> str:
    """Reply to a relayed Discord command channel and persist assistant message."""
    settings, storage, rest = _bootstrap()
    _require_mcp_caller(settings)
    msg = await rest.send_message(channel_id, content)
    await storage.append_message(channel_id, "assistant", content)
    return str(msg.get("id", "sent"))


# ----------------------- Codex tools ----------------------- #

@mcp.tool()
async def run_codex(
    prompt: str,
    workspace: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Run a one-shot Codex query and return stdout/exit_code/duration."""
    settings, storage, _ = _bootstrap()
    user_id = _require_mcp_caller(settings)
    validation = validate_prompt(prompt, max_chars=settings.codex_max_prompt_chars)
    if not validation.ok:
        return {"ok": False, "error": validation.reason}

    workdir = settings.workspace_path(workspace)
    client = CodexClient(
        executable=settings.codex_executable,
        workdir=workdir,
        model=model or settings.codex_model,
        timeout=settings.codex_timeout,
        redact_secrets=settings.redact_secrets,
    )
    result = await client.ask(prompt)
    body = redact(result.stdout) if settings.redact_secrets else result.stdout
    await storage.log_audit(
        user_id=user_id, channel_id=0, thread_id=None,
        workspace=workspace or settings.codex_default_workspace,
        prompt_hash=prompt_hash(prompt),
        prompt_preview=preview(prompt, 200),
        response_preview=preview(body, 200),
        duration_ms=result.duration_ms,
        exit_code=result.exit_code,
        status="ok" if result.ok else "error",
    )
    return {
        "ok": result.ok,
        "stdout": body,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
    }


@mcp.tool()
async def list_codex_workspaces() -> dict[str, str]:
    """Return configured workspace names and their paths."""
    settings, _, _ = _bootstrap()
    _require_mcp_caller(settings)
    return {name: str(path) for name, path in settings.codex_workspaces.items()}


@mcp.tool()
async def codex_audit_stats() -> dict[str, int]:
    """Return audit log statistics."""
    settings, storage, _ = _bootstrap()
    _require_mcp_caller(settings)
    return await storage.stats()


def run_mcp() -> None:
    setup_logging()
    _bootstrap()
    mcp.run()
