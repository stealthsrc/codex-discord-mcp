from __future__ import annotations

import pytest

from codex_discord_mcp.storage import Storage


@pytest.fixture
async def storage(tmp_path) -> Storage:
    s = Storage(tmp_path / "test.db")
    await s.init()
    return s


async def test_session_lifecycle(storage: Storage) -> None:
    assert await storage.get_session(1) is None
    s = await storage.upsert_session(1, "alpha", "gpt-test")
    assert s.workspace == "alpha"
    assert s.message_count == 0


async def test_messages_increment(storage: Storage) -> None:
    await storage.upsert_session(2, "alpha", None)
    await storage.append_message(2, "user", "hi")
    await storage.append_message(2, "assistant", "ok")
    sess = await storage.get_session(2)
    assert sess and sess.message_count == 2
    history = await storage.history(2)
    assert [r for r, _ in history] == ["user", "assistant"]
    messages = await storage.session_messages(2)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    next_msg = await storage.next_message(2, messages[0]["id"], role="assistant")
    assert next_msg and next_msg["content"] == "ok"
    any_msg = await storage.next_message_any(messages[0]["id"], role="assistant")
    assert any_msg and any_msg["thread_id"] == 2


async def test_reset_clears(storage: Storage) -> None:
    await storage.upsert_session(3, "alpha", None)
    await storage.append_message(3, "user", "hi")
    await storage.reset_session(3)
    assert await storage.get_session(3) is None
    assert await storage.history(3) == []


async def test_audit_log(storage: Storage) -> None:
    audit_id = await storage.log_audit(
        user_id=1, channel_id=2, thread_id=2, workspace="alpha",
        prompt_hash="h", prompt_preview="p", response_preview="r",
        duration_ms=10, exit_code=0, status="ok",
    )
    assert audit_id > 0
    stats = await storage.stats()
    assert stats["audits"] >= 1
