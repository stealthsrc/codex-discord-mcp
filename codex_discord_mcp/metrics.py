"""Prometheus metrics + simple aiohttp health endpoint."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiohttp import web
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from .logging_setup import get_logger

log = get_logger(__name__)

REGISTRY = CollectorRegistry()

JOBS_TOTAL = Counter(
    "codex_jobs_total", "Codex jobs processed", ["status"], registry=REGISTRY,
)
JOBS_INFLIGHT = Gauge(
    "codex_jobs_inflight", "Codex jobs currently running", registry=REGISTRY,
)
JOBS_QUEUED = Gauge(
    "codex_jobs_queued", "Codex jobs waiting in queue", registry=REGISTRY,
)
JOB_DURATION = Histogram(
    "codex_job_duration_seconds",
    "Codex job duration in seconds",
    buckets=(1, 5, 10, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)
DISCORD_EVENTS = Counter(
    "discord_events_total", "Discord events handled", ["event"], registry=REGISTRY,
)
DISCORD_BLOCKED = Counter(
    "discord_blocked_total", "Discord requests blocked", ["reason"], registry=REGISTRY,
)


async def start_servers(
    *,
    health_port: int | None,
    metrics_port: int | None,
    is_ready: Callable[[], Awaitable[bool]] | None = None,
) -> list[web.AppRunner]:
    runners: list[web.AppRunner] = []

    if health_port:
        async def healthz(_: web.Request) -> web.Response:
            return web.json_response({"status": "ok"})

        async def readyz(_: web.Request) -> web.Response:
            ready = True
            if is_ready is not None:
                try:
                    ready = await is_ready()
                except Exception:  # noqa: BLE001
                    ready = False
            return web.json_response(
                {"ready": ready},
                status=200 if ready else 503,
            )

        app = web.Application()
        app.add_routes([
            web.get("/healthz", healthz),
            web.get("/readyz", readyz),
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=health_port)
        await site.start()
        log.info("health_listening", port=health_port)
        runners.append(runner)

    if metrics_port:
        async def metrics(_: web.Request) -> web.Response:
            return web.Response(body=generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)

        app = web.Application()
        app.add_routes([web.get("/metrics", metrics)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=metrics_port)
        await site.start()
        log.info("metrics_listening", port=metrics_port)
        runners.append(runner)

    return runners


async def stop_servers(runners: list[web.AppRunner]) -> None:
    await asyncio.gather(*(r.cleanup() for r in runners), return_exceptions=True)
