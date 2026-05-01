"""SQLite-backed sessions + audit log."""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id        INTEGER PRIMARY KEY,
    workspace        TEXT    NOT NULL,
    model            TEXT,
    created_at       INTEGER NOT NULL,
    last_used_at     INTEGER NOT NULL,
    message_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id        INTEGER NOT NULL,
    role             TEXT    NOT NULL,
    content          TEXT    NOT NULL,
    created_at       INTEGER NOT NULL,
    FOREIGN KEY(thread_id) REFERENCES sessions(thread_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    thread_id        INTEGER,
    workspace        TEXT,
    prompt_hash      TEXT    NOT NULL,
    prompt_preview   TEXT    NOT NULL,
    response_preview TEXT,
    duration_ms      INTEGER,
    exit_code        INTEGER,
    status           TEXT    NOT NULL,
    created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_channel ON audit_log(channel_id, created_at);
"""


@dataclass(frozen=True)
class Session:
    thread_id: int
    workspace: str
    model: str | None
    created_at: int
    last_used_at: int
    message_count: int


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialised = False

    async def init(self) -> None:
        if self._initialised:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()
        self._initialised = True

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        await self.init()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            yield db

    async def get_session(self, thread_id: int) -> Session | None:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT thread_id, workspace, model, created_at, last_used_at, message_count "
                "FROM sessions WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return Session(**dict(row))

    async def upsert_session(
        self, thread_id: int, workspace: str, model: str | None
    ) -> Session:
        now = int(time.time())
        async with self._conn() as db:
            await db.execute(
                """
                INSERT INTO sessions(thread_id, workspace, model, created_at, last_used_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    workspace = excluded.workspace,
                    model = excluded.model,
                    last_used_at = excluded.last_used_at
                """,
                (thread_id, workspace, model, now, now),
            )
            await db.commit()
        session = await self.get_session(thread_id)
        assert session is not None
        return session

    async def reset_session(self, thread_id: int) -> None:
        async with self._conn() as db:
            await db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
            await db.execute("DELETE FROM sessions WHERE thread_id = ?", (thread_id,))
            await db.commit()

    async def append_message(self, thread_id: int, role: str, content: str) -> None:
        now = int(time.time())
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO messages(thread_id, role, content, created_at) VALUES(?, ?, ?, ?)",
                (thread_id, role, content, now),
            )
            await db.execute(
                "UPDATE sessions SET last_used_at = ?, message_count = message_count + 1 "
                "WHERE thread_id = ?",
                (now, thread_id),
            )
            await db.commit()

    async def history(self, thread_id: int, limit: int = 50) -> list[tuple[str, str]]:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT role, content FROM messages WHERE thread_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (thread_id, limit),
            )
            rows = await cur.fetchall()
            return [(r["role"], r["content"]) for r in rows]

    async def session_messages(self, thread_id: int, limit: int = 50) -> list[dict]:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT id, role, content, created_at FROM messages WHERE thread_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (thread_id, min(max(limit, 1), 100)),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in reversed(rows)]

    async def next_message(
        self, thread_id: int, after_id: int, role: str | None = None
    ) -> dict | None:
        async with self._conn() as db:
            if role:
                cur = await db.execute(
                    "SELECT id, role, content, created_at FROM messages "
                    "WHERE thread_id = ? AND id > ? AND role = ? ORDER BY id ASC LIMIT 1",
                    (thread_id, after_id, role),
                )
            else:
                cur = await db.execute(
                    "SELECT id, role, content, created_at FROM messages "
                    "WHERE thread_id = ? AND id > ? ORDER BY id ASC LIMIT 1",
                    (thread_id, after_id),
                )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def next_message_any(
        self, after_id: int, role: str | None = None
    ) -> dict | None:
        async with self._conn() as db:
            if role:
                cur = await db.execute(
                    "SELECT id, thread_id, role, content, created_at FROM messages "
                    "WHERE id > ? AND role = ? ORDER BY id ASC LIMIT 1",
                    (after_id, role),
                )
            else:
                cur = await db.execute(
                    "SELECT id, thread_id, role, content, created_at FROM messages "
                    "WHERE id > ? ORDER BY id ASC LIMIT 1",
                    (after_id,),
                )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def log_audit(
        self,
        *,
        user_id: int,
        channel_id: int,
        thread_id: int | None,
        workspace: str | None,
        prompt_hash: str,
        prompt_preview: str,
        response_preview: str | None,
        duration_ms: int | None,
        exit_code: int | None,
        status: str,
    ) -> int:
        now = int(time.time())
        async with self._conn() as db:
            cur = await db.execute(
                """
                INSERT INTO audit_log(
                    user_id, channel_id, thread_id, workspace,
                    prompt_hash, prompt_preview, response_preview,
                    duration_ms, exit_code, status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, channel_id, thread_id, workspace,
                    prompt_hash, prompt_preview[:500],
                    (response_preview or "")[:500],
                    duration_ms, exit_code, status, now,
                ),
            )
            await db.commit()
            return int(cur.lastrowid or 0)

    async def stats(self) -> dict[str, int]:
        async with self._conn() as db:
            out: dict[str, int] = {}
            for label, query in (
                ("sessions", "SELECT COUNT(*) FROM sessions"),
                ("messages", "SELECT COUNT(*) FROM messages"),
                ("audits", "SELECT COUNT(*) FROM audit_log"),
                ("audits_24h",
                 "SELECT COUNT(*) FROM audit_log WHERE created_at > strftime('%s','now')-86400"),
            ):
                cur = await db.execute(query)
                row = await cur.fetchone()
                out[label] = int(row[0]) if row else 0
            return out
