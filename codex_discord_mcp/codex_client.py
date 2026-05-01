"""Async wrapper around `codex exec` with streaming + cancel support."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .logging_setup import get_logger
from .security import redact

log = get_logger(__name__)


@dataclass
class CodexResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    cancelled: bool = False
    timed_out: bool = False
    events: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.cancelled and not self.timed_out


@dataclass(frozen=True)
class CodexClient:
    executable: str = "codex"
    workdir: Path = Path.cwd()
    model: str | None = None
    reasoning_effort: str | None = None
    timeout: int = 300
    redact_secrets: bool = True

    def _build_args(self, *, json_mode: bool, session_id: str | None) -> list[str]:
        args = [
            self.executable, "exec",
            "--skip-git-repo-check",
            "--color", "never",
            "-C", str(self.workdir),
        ]
        if self.model:
            args.extend(["-m", self.model])
        if self.reasoning_effort:
            args.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
        if json_mode:
            args.append("--json")
        args.append("-")
        return args

    async def ask(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        on_event: Callable[[dict], Awaitable[None]] | None = None,
    ) -> CodexResult:
        """Run codex exec, stream JSON events when possible, return final result."""
        args = self._build_args(json_mode=True, session_id=session_id)

        loop = asyncio.get_running_loop()
        start = loop.time()

        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(
                __import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags if sys.platform == "win32" else 0,
                start_new_session=sys.platform != "win32",
            )
        except FileNotFoundError as exc:
            return CodexResult(
                stdout="", stderr=f"codex executable not found: {exc}",
                exit_code=127, duration_ms=0,
            )

        assert proc.stdin and proc.stdout and proc.stderr
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        events: list[dict] = []
        text_chunks: list[str] = []

        async def consume_stdout() -> None:
            assert proc.stdout
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                event: dict | None = None
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(event)
                item = event.get("item")
                delta = None
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    delta = item.get("text")
                else:
                    delta = event.get("delta") or event.get("text")
                if isinstance(delta, str):
                    text_chunks.append(delta)
                if on_event is not None:
                    try:
                        await on_event(event)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("on_event_callback_failed", error=str(exc))

        stderr_chunks: list[bytes] = []

        async def consume_stderr() -> None:
            assert proc.stderr
            async for raw in proc.stderr:
                stderr_chunks.append(raw)

        cancelled = False
        timed_out = False
        try:
            await asyncio.wait_for(
                asyncio.gather(consume_stdout(), consume_stderr(), proc.wait()),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            await self._terminate(proc)
        except asyncio.CancelledError:
            cancelled = True
            await self._terminate(proc)
            raise
        finally:
            duration_ms = int((loop.time() - start) * 1000)

        stdout = "\n".join(text_chunks).strip()
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()

        if self.redact_secrets:
            stdout = redact(stdout)
            stderr = redact(stderr)

        exit_code = proc.returncode if proc.returncode is not None else -1
        return CodexResult(
            stdout=stdout or "(no response)",
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            cancelled=cancelled,
            timed_out=timed_out,
            events=events,
        )

    @staticmethod
    async def _terminate(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            await proc.wait()
