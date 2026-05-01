from __future__ import annotations

import argparse

from .cli import run_bot_cli, run_mcp_cli


def main() -> None:
    parser = argparse.ArgumentParser(prog="codex-discord-mcp")
    parser.add_argument("mode", choices=("bot", "mcp"))
    args = parser.parse_args()

    if args.mode == "bot":
        run_bot_cli()
    else:
        run_mcp_cli()


if __name__ == "__main__":
    main()
