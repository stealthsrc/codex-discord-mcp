"""Thin async wrapper over Discord REST API (used by the MCP server)."""
from __future__ import annotations

from typing import Any

import aiohttp

from .logging_setup import get_logger

log = get_logger(__name__)

API_BASE = "https://discord.com/api/v10"


class DiscordREST:
    def __init__(self, token: str, *, user_agent: str = "codex-discord-mcp/0.2") -> None:
        self.token = token
        self.user_agent = user_agent

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }

    async def _request(
        self, method: str, path: str, *, json: Any = None, params: dict | None = None,
    ) -> Any:
        url = f"{API_BASE}{path}"
        async with aiohttp.ClientSession(headers=self._headers()) as session:
            async with session.request(method, url, json=json, params=params) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    log.warning("discord_api_error", status=resp.status, body=text[:300])
                    raise RuntimeError(f"Discord API {resp.status}: {text[:300]}")
                if not text:
                    return None
                try:
                    return await resp.json(content_type=None)
                except Exception:  # noqa: BLE001
                    return text

    # ------------- messages -------------- #
    async def send_message(self, channel_id: int, content: str) -> dict:
        return await self._request(
            "POST", f"/channels/{channel_id}/messages", json={"content": content},
        )

    async def edit_message(self, channel_id: int, message_id: int, content: str) -> dict:
        return await self._request(
            "PATCH", f"/channels/{channel_id}/messages/{message_id}",
            json={"content": content},
        )

    async def delete_message(self, channel_id: int, message_id: int) -> None:
        await self._request("DELETE", f"/channels/{channel_id}/messages/{message_id}")

    async def list_messages(
        self, channel_id: int, *, limit: int = 20, before: int | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": min(max(limit, 1), 100)}
        if before:
            params["before"] = before
        return await self._request(
            "GET", f"/channels/{channel_id}/messages", params=params,
        )

    # ------------- reactions ------------- #
    async def add_reaction(self, channel_id: int, message_id: int, emoji: str) -> None:
        from urllib.parse import quote
        await self._request(
            "PUT",
            f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji)}/@me",
        )

    # ------------- threads --------------- #
    async def create_thread(
        self, channel_id: int, name: str, *, auto_archive_minutes: int = 1440,
    ) -> dict:
        return await self._request(
            "POST", f"/channels/{channel_id}/threads",
            json={
                "name": name[:100],
                "auto_archive_duration": auto_archive_minutes,
                "type": 11,  # public thread
            },
        )

    # ------------- channels -------------- #
    async def list_guild_channels(self, guild_id: int) -> list[dict]:
        return await self._request("GET", f"/guilds/{guild_id}/channels")

    async def get_channel(self, channel_id: int) -> dict:
        return await self._request("GET", f"/channels/{channel_id}")

    # ------------- DMs ------------------- #
    async def create_dm(self, recipient_id: int) -> dict:
        return await self._request(
            "POST", "/users/@me/channels", json={"recipient_id": str(recipient_id)},
        )

    async def send_dm(self, recipient_id: int, content: str) -> dict:
        dm = await self.create_dm(recipient_id)
        channel_id = int(dm["id"])
        return await self.send_message(channel_id, content)
