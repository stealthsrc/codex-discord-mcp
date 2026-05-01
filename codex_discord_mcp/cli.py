"""Console entry points."""
from __future__ import annotations

from .discord_bot import run_bot
from .mcp_server import run_mcp


def run_bot_cli() -> None:
    run_bot()


def run_mcp_cli() -> None:
    run_mcp()
