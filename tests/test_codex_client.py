from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from codex_discord_mcp.codex_client import CodexClient


@pytest.fixture
def fake_codex(tmp_path) -> Path:
    """Cross-platform fake codex: a tiny Python script ignoring its CLI args."""
    script = tmp_path / "fake_codex.py"
    script.write_text(textwrap.dedent("""
        import sys, json
        sys.stdout.write(json.dumps({"delta": "hello "}) + "\\n")
        sys.stdout.write(json.dumps({"delta": "world"}) + "\\n")
        sys.stdout.flush()
    """).strip())
    return script


async def test_streaming_collects_deltas(fake_codex: Path, tmp_path) -> None:
    """Use sys.executable + a wrapper that swallows args. Patch _build_args."""
    events: list[dict] = []

    class Patched(CodexClient):
        def _build_args(self, *, json_mode: bool, session_id: str | None) -> list[str]:
            return [sys.executable, str(fake_codex)]

    client = Patched(executable=sys.executable, workdir=tmp_path, timeout=10)

    async def collect(ev: dict) -> None:
        events.append(ev)

    result = await client.ask("ignored", on_event=collect)
    assert result.exit_code == 0, result.stderr
    assert "hello" in result.stdout
    assert "world" in result.stdout
    assert len(events) == 2


async def test_json_mode_ignores_non_json_stdout(tmp_path) -> None:
    script = tmp_path / "mixed.py"
    script.write_text(
        'import sys, json; '
        'sys.stdout.write(json.dumps({"type":"item.completed",'
        '"item":{"type":"agent_message","text":"OK"}})+"\\n"); '
        'sys.stdout.write("SUCCESS: process terminated\\n")'
    )

    class Patched(CodexClient):
        def _build_args(self, *, json_mode, session_id):
            return [sys.executable, str(script)]

    client = Patched(executable=sys.executable, workdir=tmp_path, timeout=5)
    result = await client.ask("x")
    assert result.stdout == "OK"


async def test_missing_executable(tmp_path) -> None:
    client = CodexClient(
        executable="codex_definitely_not_there", workdir=tmp_path, timeout=5,
    )
    result = await client.ask("hi")
    assert result.exit_code == 127
    assert "not found" in result.stderr


async def test_redaction_in_output(tmp_path) -> None:
    script = tmp_path / "leak.py"
    script.write_text(
        'import sys, json; '
        'sys.stdout.write(json.dumps({"delta":"key sk-proj-' + "A" * 30 + '"})+"\\n")'
    )

    class Patched(CodexClient):
        def _build_args(self, *, json_mode, session_id):
            return [sys.executable, str(script)]

    client = Patched(executable=sys.executable, workdir=tmp_path, timeout=5, redact_secrets=True)
    result = await client.ask("x")
    assert "sk-proj-" not in result.stdout
    assert "REDACTED" in result.stdout
