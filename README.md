# codex-discord-mcp

Discord bot **and** MCP server bridging Discord ↔ Codex CLI.

- Discord users talk to Codex via `/codex` slash commands or by mentioning the bot.
- Other agents (Claude, etc.) drive Discord through the MCP server.

## Features

- **Slash commands**: `/codex ask`, `/cancel`, `/status`, `/reset`, `/workspaces`.
- **Per-thread sessions** persisted in SQLite (history + audit log).
- **Job queue** — one Codex job at a time per channel, with cancellation.
- **Streaming progress** — bot edits a status message while Codex runs (`codex exec --json`).
- **Multi-workspace** — switch project directories from Discord (`workspace:` parameter).
- **Security**: prompt validation, sensitive-path blocklist, secret redaction (OpenAI/Anthropic/GitHub/AWS/Discord/private keys), user + role allowlists, channel allowlist.
- **Observability**: `/healthz` + `/readyz` (aiohttp) and Prometheus `/metrics`.
- **MCP tools**: send/edit/delete/read messages, reactions, threads, DMs, channel listing, `await_reply`, `run_codex`, `list_codex_workspaces`, `codex_audit_stats`.
- **Docker** + **GitHub Actions CI** + **pytest** suite.

## Install

### One-liner (no clone needed)

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/StealthyLabsHQ/codex-discord-mcp/main/install.ps1 | iex
```

The installer asks for `DISCORD_TOKEN`, `DISCORD_CLIENT_ID`, and an optional
`CODEX_WORKDIR`. It writes `%USERPROFILE%\.codex-disc-mcp\.env`, installs the
package, and prints the Discord invite URL.

### From clone

```bash
pip install -e ".[dev]"
# or
pip install .
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

## Run the Discord bot

Required: bot token, message-content intent enabled, bot invited with `applications.commands`.

```powershell
$env:DISCORD_TOKEN="..."
$env:CODEX_WORKDIR="D:\path\to\project"
codex-discord-bot
```

Slash commands sync on startup. After a few seconds, type `/codex ask` in any allowed channel.

Mention-style fallback: mention the bot in an allowed channel and it will run the rest of the message as a prompt.

## Run the MCP server

```powershell
$env:DISCORD_TOKEN="..."
codex-discord-mcp
```

### Available MCP tools

| Tool | Purpose |
| --- | --- |
| `send_discord_message(channel_id, content)` | Send a message |
| `edit_discord_message(channel_id, message_id, content)` | Edit a bot message |
| `delete_discord_message(channel_id, message_id)` | Delete a bot message |
| `read_discord_messages(channel_id, limit)` | Read recent messages |
| `add_discord_reaction(channel_id, message_id, emoji)` | Add unicode reaction |
| `create_discord_thread(channel_id, name)` | Open a public thread |
| `list_discord_channels(guild_id)` | List channels in a guild |
| `send_discord_dm(user_id, content)` | DM a user |
| `await_discord_reply(channel_id, after_message_id, timeout_seconds)` | Wait for next raw Discord message |
| `read_codex_session_messages(channel_id, limit)` | Read persisted Codex session messages |
| `await_codex_session_message(channel_id, after_message_id, role?, timeout_seconds)` | Wait for next persisted Codex session message |
| `run_codex(prompt, workspace?, model?)` | One-shot Codex query |
| `list_codex_workspaces()` | List configured workspaces |
| `codex_audit_stats()` | Audit log counters |

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | — (required) | Bot token |
| `DISCORD_CHANNEL_IDS` | — | Comma-separated channel allowlist |
| `DISCORD_ALLOWED_USERS` | — | Comma-separated user-id allowlist |
| `DISCORD_ALLOWED_ROLES` | — | Comma-separated role-id allowlist |
| `CODEX_EXECUTABLE` | `codex` | Path to the Codex CLI |
| `CODEX_WORKDIR` | `cwd` | Default workspace path |
| `CODEX_WORKSPACES` | — | JSON map `{"name":"path"}` for multi-workspace |
| `CODEX_DEFAULT_WORKSPACE` | first key | Default workspace name |
| `CODEX_MODEL` | — | Codex model override |
| `CODEX_TIMEOUT_SECONDS` | `300` | Per-job timeout |
| `CODEX_MAX_PROMPT_CHARS` | `8000` | Hard limit on prompt length |
| `CODEX_REDACT_SECRETS` | `1` | Redact common secret patterns from output |
| `CODEX_DB_PATH` | `codex_discord.db` | SQLite path |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `0` | Structured logging |
| `ENABLE_HEALTH` / `HEALTH_PORT` | `1` / `8080` | aiohttp health server |
| `ENABLE_METRICS` / `METRICS_PORT` | `0` / `9090` | Prometheus exporter |

See [.env.example](.env.example) for the full list.

## Docker

```bash
cp .env.example .env  # fill DISCORD_TOKEN
docker compose up --build
```

The image installs the Python package only; pass your Codex installer through the
`CODEX_INSTALL_CMD` build arg, e.g. `--build-arg CODEX_INSTALL_CMD="pip install codex-cli"`.

## Development

```bash
pip install -e ".[dev]"
pytest                # run the suite
ruff check .          # lint
mypy codex_discord_mcp
```

## Security model

Discord input is **untrusted** (see [AGENTS.md](AGENTS.md)). The bot:

- Blocks dangerous shell patterns (`rm -rf /`, `mkfs`, fork bombs, …) before invoking Codex.
- Refuses prompts referencing sensitive paths (`.env`, SSH keys, credential files).
- Redacts API keys / tokens / private-key headers from any output sent back to Discord.
- Restricts who can talk to the bot via channel + user + role allowlists.
- Audits every job (user, channel, prompt hash + 200-char preview, exit code, duration) in SQLite.

Set `CODEX_REQUIRE_APPROVAL=1` to make the bot defer high-risk patches until a human reacts.

## License

MIT
