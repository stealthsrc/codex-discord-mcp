from __future__ import annotations

import asyncio

from codex_discord_mcp.jobs import Job, JobQueue


async def test_serial_per_channel() -> None:
    queue = JobQueue()
    order: list[str] = []

    async def make_runner(label: str, delay: float):
        async def runner(_: Job) -> None:
            await asyncio.sleep(delay)
            order.append(label)
        return runner

    a = await make_runner("a", 0.05)
    b = await make_runner("b", 0.01)

    await queue.submit(channel_id=1, user_id=1, prompt="a", run=a)
    await queue.submit(channel_id=1, user_id=1, prompt="b", run=b)
    await asyncio.sleep(0.5)
    assert order == ["a", "b"]


async def test_parallel_across_channels() -> None:
    queue = JobQueue()
    finished = asyncio.Event()
    counter = {"n": 0}

    async def runner(_: Job) -> None:
        counter["n"] += 1
        if counter["n"] == 2:
            finished.set()
        await asyncio.sleep(0.05)

    await queue.submit(channel_id=1, user_id=1, prompt="x", run=runner)
    await queue.submit(channel_id=2, user_id=1, prompt="y", run=runner)
    await asyncio.wait_for(finished.wait(), timeout=2)


async def test_cancel_current() -> None:
    queue = JobQueue()
    started = asyncio.Event()

    async def long_runner(_: Job) -> None:
        started.set()
        await asyncio.sleep(10)

    await queue.submit(channel_id=42, user_id=1, prompt="x", run=long_runner)
    await asyncio.wait_for(started.wait(), timeout=1)
    cancelled = await queue.cancel_current(42)
    assert cancelled


async def test_cancel_nothing() -> None:
    queue = JobQueue()
    assert not await queue.cancel_current(999)
