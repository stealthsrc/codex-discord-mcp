"""Per-channel async job queue with cancellation support."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .logging_setup import get_logger
from .metrics import JOB_DURATION, JOBS_INFLIGHT, JOBS_QUEUED, JOBS_TOTAL

log = get_logger(__name__)


@dataclass
class Job:
    job_id: str
    channel_id: int
    user_id: int
    prompt: str
    coro_factory: Callable[[Job], Awaitable[None]]
    task: asyncio.Task | None = None
    enqueued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    cancelled: bool = False


class JobQueue:
    """One worker per channel — guarantees serial execution per channel/thread."""

    def __init__(self) -> None:
        self._workers: dict[int, asyncio.Task] = {}
        self._queues: dict[int, asyncio.Queue[Job]] = {}
        self._inflight: dict[int, Job] = {}
        self._by_id: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def submit(
        self,
        *,
        channel_id: int,
        user_id: int,
        prompt: str,
        run: Callable[[Job], Awaitable[None]],
    ) -> Job:
        job = Job(
            job_id=uuid.uuid4().hex[:12],
            channel_id=channel_id,
            user_id=user_id,
            prompt=prompt,
            coro_factory=run,
        )
        async with self._lock:
            queue = self._queues.setdefault(channel_id, asyncio.Queue())
            self._by_id[job.job_id] = job
            await queue.put(job)
            JOBS_QUEUED.inc()
            if channel_id not in self._workers or self._workers[channel_id].done():
                self._workers[channel_id] = asyncio.create_task(
                    self._worker_loop(channel_id), name=f"job-worker-{channel_id}",
                )
        log.info("job_submitted", job_id=job.job_id, channel_id=channel_id, user_id=user_id)
        return job

    async def _worker_loop(self, channel_id: int) -> None:
        queue = self._queues[channel_id]
        while True:
            try:
                job = await asyncio.wait_for(queue.get(), timeout=60)
            except asyncio.TimeoutError:
                if queue.empty():
                    return
                continue
            JOBS_QUEUED.dec()
            JOBS_INFLIGHT.inc()
            self._inflight[channel_id] = job
            job.started_at = time.time()
            status = "ok"
            try:
                job.task = asyncio.create_task(job.coro_factory(job))
                await job.task
            except asyncio.CancelledError:
                status = "cancelled"
                job.cancelled = True
            except Exception as exc:  # noqa: BLE001
                status = "error"
                log.exception("job_failed", job_id=job.job_id, error=str(exc))
            finally:
                duration = time.time() - (job.started_at or time.time())
                JOB_DURATION.observe(duration)
                JOBS_INFLIGHT.dec()
                JOBS_TOTAL.labels(status=status).inc()
                self._inflight.pop(channel_id, None)
                self._by_id.pop(job.job_id, None)
                log.info(
                    "job_finished", job_id=job.job_id, status=status,
                    duration=round(duration, 2),
                )

    def current(self, channel_id: int) -> Job | None:
        return self._inflight.get(channel_id)

    def queue_size(self, channel_id: int) -> int:
        q = self._queues.get(channel_id)
        return q.qsize() if q else 0

    async def cancel_current(self, channel_id: int) -> bool:
        job = self._inflight.get(channel_id)
        if not job or not job.task:
            return False
        job.cancelled = True
        job.task.cancel()
        return True

    def stats(self) -> dict[str, int]:
        return {
            "channels_active": sum(1 for w in self._workers.values() if not w.done()),
            "inflight": len(self._inflight),
            "queued": sum(q.qsize() for q in self._queues.values()),
        }
