"""Discord bot: slash commands, per-thread sessions, queued Codex jobs."""
from __future__ import annotations

import asyncio
import io
import os
import time

import discord
from discord import app_commands

from .codex_client import CodexClient, CodexResult
from .config import Settings
from .jobs import Job, JobQueue
from .logging_setup import get_logger
from .metrics import DISCORD_BLOCKED, DISCORD_EVENTS, start_servers, stop_servers
from .security import preview, prompt_hash, redact, validate_prompt
from .storage import Storage

log = get_logger(__name__)

MAX_DISCORD_MESSAGE = 1900
LARGE_OUTPUT_THRESHOLD = 1900


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def split_discord_message(content: str) -> list[str]:
    if len(content) <= MAX_DISCORD_MESSAGE:
        return [content]
    return [
        content[i : i + MAX_DISCORD_MESSAGE]
        for i in range(0, len(content), MAX_DISCORD_MESSAGE)
    ]


LABELS = {
    "en": {
        "prompt": "Prompt",
        "project": "Project",
        "folder": "Folder",
        "model": "Model",
        "effort": "effort",
        "response": "Response",
        "truncated": "Output (truncated)",
        "attachment": "(see attachment for full output)",
    },
    "es": {
        "prompt": "Prompt",
        "project": "Proyecto",
        "folder": "Carpeta",
        "model": "Modelo",
        "effort": "esfuerzo",
        "response": "Respuesta",
        "truncated": "Salida (truncada)",
        "attachment": "(ver adjunto para salida completa)",
    },
    "fr": {
        "prompt": "Prompt",
        "project": "Projet",
        "folder": "Dossier",
        "model": "ModÃ¨le",
        "effort": "effort",
        "response": "RÃ©ponse",
        "truncated": "Sortie (tronquÃ©e)",
        "attachment": "(voir piÃ¨ce jointe pour la sortie complÃ¨te)",
    },
}


def detect_language(text: str) -> str:
    lowered = f" {text.lower()} "
    spanish_words = (" el ", " los ", " las ", " para ", " como ", " puedes ", " hacer ")
    if any(word in lowered for word in spanish_words):
        return "es"
    if any(
        word in lowered
        for word in (" le ", " les ", " des ", " pour ", " avec ", " tu ", " peux ", " faire ")
    ):
        return "fr"
    return "en"


def build_result_embed(
    *,
    prompt: str,
    result: CodexResult,
    workspace: str,
    workspace_path: str,
    model: str | None,
    job_id: str,
) -> discord.Embed:
    color = (
        discord.Color.green() if result.ok
        else discord.Color.orange() if result.timed_out or result.cancelled
        else discord.Color.red()
    )
    title = "Codex âœ“" if result.ok else (
        "Codex âŒ› timeout" if result.timed_out else
        "Codex â¹ cancelled" if result.cancelled else "Codex âœ—"
    )
    embed = discord.Embed(title=title, color=color)
    embed.add_field(
        name="Prompt", value=f"```\n{preview(prompt, 900)}\n```", inline=False,
    )
    embed.add_field(
        name="Workspace", value=f"`{workspace}`\n`{workspace_path}`", inline=False,
    )
    if result.stderr and not result.ok:
        embed.add_field(
            name="stderr", value=f"```\n{preview(result.stderr, 900)}\n```", inline=False,
        )
    embed.set_footer(
        text=f"job={job_id} â€¢ ws={workspace} â€¢ model={model or 'default'} "
             f"â€¢ {result.duration_ms}ms â€¢ exit={result.exit_code}"
    )
    return embed


async def reply_long(
    target: discord.abc.Messageable | discord.Interaction,
    *,
    embed: discord.Embed,
    body: str,
    job_id: str,
) -> None:
    """Send embed; attach long body as file when oversized."""
    is_interaction = isinstance(target, discord.Interaction)

    files: list[discord.File] = []
    if len(body) > LARGE_OUTPUT_THRESHOLD:
        buf = io.BytesIO(body.encode("utf-8"))
        files.append(discord.File(buf, filename=f"codex-{job_id}.txt"))
        embed.add_field(
            name="Output (truncated)",
            value=f"```\n{preview(body, 900)}\n```\n(see attachment for full output)",
            inline=False,
        )
    else:
        embed.add_field(
            name="Output", value=f"```\n{body or '(empty)'}\n```", inline=False,
        )

    if is_interaction:
        await target.followup.send(embed=embed, files=files)
    else:
        await target.send(embed=embed, files=files)


def build_plain_result(
    *,
    prompt: str,
    result: CodexResult,
    workspace: str,
    workspace_path: str,
    model: str | None,
    reasoning_effort: str | None,
    job_id: str,
) -> str:
    labels = LABELS[detect_language(prompt)]
    status = (
        "OK" if result.ok
        else "TIMEOUT" if result.timed_out
        else "CANCELLED" if result.cancelled
        else "ERROR"
    )
    parts = [
        f"**Codex {status}**",
        "",
        f"**{labels['prompt']}**: {preview(prompt, 900)}",
        f"**{labels['project']}**: `{workspace}`",
        f"**{labels['folder']}**: `{workspace_path}`",
        (
            f"**{labels['model']}**: `{model or 'unknown'}` · "
            f"{labels['effort']} `{reasoning_effort or 'unknown'}`"
        ),
        f"`job={job_id}` · `{result.duration_ms}ms` · `exit={result.exit_code}`",
    ]
    if result.stderr and not result.ok:
        parts.extend(["", "**stderr**", preview(result.stderr, 900)])
    return "\n".join(parts)


async def reply_long_plain(
    target: discord.abc.Messageable | discord.Interaction,
    *,
    header: str,
    body: str,
    job_id: str,
) -> None:
    is_interaction = isinstance(target, discord.Interaction)
    labels = LABELS[detect_language(header)]
    files: list[discord.File] = []

    if len(body) > LARGE_OUTPUT_THRESHOLD:
        buf = io.BytesIO(body.encode("utf-8"))
        files.append(discord.File(buf, filename=f"codex-{job_id}.txt"))
        content = (
            f"{header}\n\n{labels['truncated']}\n"
            f"{preview(body, 900)}\n"
            f"{labels['attachment']}"
        )
    else:
        content = f"{header}\n\n**{labels['response']}**\n{body or '(empty)'}"

    if is_interaction:
        await target.followup.send(content=content, files=files)
    else:
        for chunk in split_discord_message(content):
            await target.send(content=chunk)
        if files:
            await target.send(files=files)


class CodexCog(discord.app_commands.Group):
    """Slash command group: /codex ask|cancel|status|reset|model|workspace."""

    def __init__(self, bot: CodexDiscordBot) -> None:
        super().__init__(name="codex", description="Talk to Codex CLI")
        self.bot = bot

    @app_commands.command(name="ask", description="Send a prompt to Codex")
    @app_commands.describe(
        prompt="What you want Codex to do",
        workspace="Workspace name (defaults to active)",
        model="Codex model override",
    )
    async def ask(
        self,
        interaction: discord.Interaction,
        prompt: str,
        workspace: str | None = None,
        model: str | None = None,
    ) -> None:
        await self.bot.handle_ask(interaction, prompt, workspace=workspace, model=model)

    @app_commands.command(name="cancel", description="Cancel the running Codex job")
    async def cancel(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_cancel(interaction)

    @app_commands.command(name="status", description="Show queue + session status")
    async def status(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_status(interaction)

    @app_commands.command(name="reset", description="Clear the conversation in this thread")
    async def reset(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_reset(interaction)

    @app_commands.command(name="workspaces", description="List configured workspaces")
    async def workspaces(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_workspaces(interaction)


class CodexDiscordBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(CodexCog(self))

        self.storage = Storage(settings.db_path)
        self.queue = JobQueue()
        self._runners: list = []
        self._parent_watchdog: asyncio.Task | None = None

    # ------ lifecycle ----------------------------------------------------- #
    async def setup_hook(self) -> None:
        await self.storage.init()

        async def is_ready() -> bool:
            return self.is_ready()

        self._runners = await start_servers(
            health_port=self.settings.health_port if self.settings.enable_health else None,
            metrics_port=self.settings.metrics_port if self.settings.enable_metrics else None,
            is_ready=is_ready,
        )
        try:
            synced = await self.tree.sync()
            log.info("slash_commands_synced", count=len(synced))
        except Exception as exc:  # noqa: BLE001
            log.warning("slash_sync_failed", error=str(exc))
        if self.settings.codex_parent_pid:
            self._parent_watchdog = asyncio.create_task(
                self._watch_parent(self.settings.codex_parent_pid),
                name="parent-watchdog",
            )

    async def close(self) -> None:
        if self._parent_watchdog:
            self._parent_watchdog.cancel()
        await stop_servers(self._runners)
        await super().close()

    async def on_ready(self) -> None:
        DISCORD_EVENTS.labels(event="ready").inc()
        log.info("bot_ready", user=str(self.user))

    async def _watch_parent(self, pid: int) -> None:
        while True:
            if not _pid_exists(pid):
                log.info("parent_exited", pid=pid)
                await self.close()
                return
            await asyncio.sleep(2)

    # ------ permissions --------------------------------------------------- #
    def _interaction_blocked(self, interaction: discord.Interaction) -> str | None:
        if not self.settings.is_channel_allowed(interaction.channel_id or 0):
            return "channel"
        role_ids = (
            [r.id for r in interaction.user.roles]
            if isinstance(interaction.user, discord.Member) else []
        )
        if not self.settings.is_user_allowed(interaction.user.id, role_ids):
            return "user"
        return None

    # ------ slash command handlers --------------------------------------- #
    async def handle_ask(
        self,
        interaction: discord.Interaction,
        prompt: str,
        *,
        workspace: str | None,
        model: str | None,
    ) -> None:
        DISCORD_EVENTS.labels(event="ask").inc()
        block = self._interaction_blocked(interaction)
        if block:
            DISCORD_BLOCKED.labels(reason=block).inc()
            await interaction.response.send_message(
                f"â›” blocked ({block})", ephemeral=True,
            )
            return

        validation = validate_prompt(prompt, max_chars=self.settings.codex_max_prompt_chars)
        if not validation.ok:
            DISCORD_BLOCKED.labels(reason="validation").inc()
            await interaction.response.send_message(
                f"â›” {validation.reason}", ephemeral=True,
            )
            return

        ws_name = workspace or self.settings.codex_default_workspace
        if ws_name not in self.settings.codex_workspaces:
            await interaction.response.send_message(
                f"â›” unknown workspace `{ws_name}`. "
                f"Available: {', '.join(self.settings.codex_workspaces.keys())}",
                ephemeral=True,
            )
            return

        thread_id = interaction.channel_id or 0
        await self.storage.upsert_session(thread_id, ws_name, model or self.settings.codex_model)
        if self.settings.codex_discord_runtime == "relay":
            if not isinstance(interaction.channel, discord.DMChannel):
                await interaction.response.send_message(
                    "relay mode accepts DMs only",
                    ephemeral=True,
                )
                return
            await self.storage.append_message(thread_id, "user", prompt)
            await interaction.response.send_message(
                f"queued for CLI: `discord-{thread_id}`",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        async def runner(job: Job) -> None:
            await self._run_codex_job(
                job=job,
                interaction=interaction,
                workspace=ws_name,
                model=model or self.settings.codex_model,
            )

        await self.queue.submit(
            channel_id=thread_id,
            user_id=interaction.user.id,
            prompt=prompt,
            run=runner,
        )

    async def handle_cancel(self, interaction: discord.Interaction) -> None:
        DISCORD_EVENTS.labels(event="cancel").inc()
        cancelled = await self.queue.cancel_current(interaction.channel_id or 0)
        msg = "â¹ cancelled" if cancelled else "no running job"
        await interaction.response.send_message(msg, ephemeral=True)

    async def handle_status(self, interaction: discord.Interaction) -> None:
        DISCORD_EVENTS.labels(event="status").inc()
        thread_id = interaction.channel_id or 0
        current = self.queue.current(thread_id)
        queued = self.queue.queue_size(thread_id)
        session = await self.storage.get_session(thread_id)
        stats = await self.storage.stats()
        embed = discord.Embed(title="Codex status", color=discord.Color.blurple())
        embed.add_field(
            name="Channel",
            value=(
                f"running: {'yes' if current else 'no'}\n"
                f"queued: {queued}\n"
                f"session msgs: {session.message_count if session else 0}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Global",
            value=f"sessions: {stats['sessions']} â€¢ audits 24h: {stats['audits_24h']}",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def handle_reset(self, interaction: discord.Interaction) -> None:
        DISCORD_EVENTS.labels(event="reset").inc()
        await self.storage.reset_session(interaction.channel_id or 0)
        await interaction.response.send_message("ðŸ§¹ session cleared", ephemeral=True)

    async def handle_workspaces(self, interaction: discord.Interaction) -> None:
        DISCORD_EVENTS.labels(event="workspaces").inc()
        lines = [
            f"â€¢ `{name}` â†’ `{path}`"
            for name, path in self.settings.codex_workspaces.items()
        ]
        default = self.settings.codex_default_workspace
        await interaction.response.send_message(
            f"**Workspaces** (default: `{default}`)\n" + "\n".join(lines),
            ephemeral=True,
        )

    # ------ DM + mention handler ------------------------------------------ #
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)

        if is_dm:
            prompt = message.content.strip()
        else:
            if self.settings.codex_discord_runtime == "relay":
                return
            if not self.settings.is_channel_allowed(message.channel.id):
                return
            if not self.user or self.user not in message.mentions:
                return
            prompt = message.content
            if self.user:
                prompt = (
                    prompt.replace(f"<@{self.user.id}>", "")
                          .replace(f"<@!{self.user.id}>", "")
                          .strip()
                )

        if not prompt:
            return

        role_ids = (
            [r.id for r in message.author.roles]
            if isinstance(message.author, discord.Member) else []
        )
        if not self.settings.is_user_allowed(message.author.id, role_ids):
            DISCORD_BLOCKED.labels(reason="user").inc()
            return

        DISCORD_EVENTS.labels(event="dm" if is_dm else "mention").inc()

        validation = validate_prompt(prompt, max_chars=self.settings.codex_max_prompt_chars)
        if not validation.ok:
            await message.reply(f"â›” {validation.reason}", mention_author=False)
            return

        ws = self.settings.codex_default_workspace
        await self.storage.upsert_session(message.channel.id, ws, self.settings.codex_model)
        if self.settings.codex_discord_runtime == "relay":
            await self.storage.append_message(message.channel.id, "user", prompt)
            await message.reply(
                f"queued for CLI: `discord-{message.channel.id}`",
                mention_author=False,
            )
            return

        async def runner(job: Job) -> None:
            await self._run_codex_job(
                job=job,
                interaction=message,
                workspace=ws,
                model=self.settings.codex_model,
            )

        async with message.channel.typing():
            await self.queue.submit(
                channel_id=message.channel.id,
                user_id=message.author.id,
                prompt=prompt,
                run=runner,
            )

    # ------ codex execution ---------------------------------------------- #
    async def _run_codex_job(
        self,
        *,
        job: Job,
        interaction: discord.Interaction | discord.Message,
        workspace: str,
        model: str | None,
    ) -> None:
        workspace_path = self.settings.workspace_path(workspace)
        client = CodexClient(
            executable=self.settings.codex_executable,
            workdir=workspace_path,
            model=model,
            reasoning_effort=self.settings.codex_reasoning_effort,
            timeout=self.settings.codex_timeout,
            redact_secrets=self.settings.redact_secrets,
        )
        await self.storage.append_message(job.channel_id, "user", job.prompt)

        progress_msg = None
        last_edit = 0.0
        chunks: list[str] = []

        async def on_event(_: dict) -> None:
            nonlocal progress_msg, last_edit
            now = time.time()
            if now - last_edit < 1.5:
                return
            last_edit = now
            joined = "\n".join(chunks)
            current = redact(joined) if self.settings.redact_secrets else joined
            preview_text = preview(current, 1500) or "â€¦"
            try:
                if progress_msg is None and isinstance(interaction, discord.Interaction):
                    progress_msg = await interaction.followup.send(
                        f"â³ working...\n```\n{preview_text}\n```",
                        wait=True,
                    )
                elif progress_msg is not None:
                    await progress_msg.edit(
                        content=f"â³ working...\n```\n{preview_text}\n```",
                    )
            except Exception:  # noqa: BLE001
                pass

        # capture streamed deltas for progress
        _orig_on_event = on_event

        async def collecting(event: dict) -> None:
            delta = event.get("delta") or event.get("text") or event.get("content")
            if isinstance(delta, str):
                chunks.append(delta)
            await _orig_on_event(event)

        result = await client.ask(
            job.prompt,
            session_id=f"discord-{job.channel_id}",
            on_event=collecting,
        )

        body = result.stdout if result.ok else (result.stdout or result.stderr)
        await self.storage.append_message(job.channel_id, "assistant", body)
        await self.storage.log_audit(
            user_id=job.user_id,
            channel_id=job.channel_id,
            thread_id=job.channel_id,
            workspace=workspace,
            prompt_hash=prompt_hash(job.prompt),
            prompt_preview=preview(job.prompt, 200),
            response_preview=preview(body, 200),
            duration_ms=result.duration_ms,
            exit_code=result.exit_code,
            status="ok" if result.ok else "error",
        )

        header = build_plain_result(
            prompt=job.prompt, result=result,
            workspace=workspace, workspace_path=str(workspace_path),
            model=model, reasoning_effort=self.settings.codex_reasoning_effort,
            job_id=job.job_id,
        )

        if isinstance(interaction, discord.Interaction):
            await reply_long_plain(interaction, header=header, body=body, job_id=job.job_id)
            if progress_msg is not None:
                try:
                    await progress_msg.delete()
                except Exception:  # noqa: BLE001
                    pass
        else:
            await reply_long_plain(interaction.channel, header=header, body=body, job_id=job.job_id)


def run_bot() -> None:
    from .logging_setup import setup_logging

    settings = Settings.from_env()
    setup_logging(level=settings.log_level, json_output=settings.log_json)
    bot = CodexDiscordBot(settings)
    bot.run(settings.discord_token, log_handler=None)
