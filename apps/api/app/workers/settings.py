"""ARQ worker settings.  Run with: ``arq app.workers.settings.WorkerSettings``.

Besides the queued tasks, the worker runs a lightweight poll loop so a job
submitted through the API reaches ``running`` within a couple of seconds: ARQ's
cron is minute-granular, which is too coarse for F4.3's "秒级进入 Running".
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.tasks import ping, schedule_jobs

logger = logging.getLogger(__name__)


async def _scheduler_loop() -> None:
    while True:
        try:
            await schedule_jobs({})
        except Exception:  # noqa: BLE001 - a bad cycle must not kill the poller
            logger.exception("scheduler cycle failed")
        await asyncio.sleep(settings.scheduler_interval_seconds)


async def startup(ctx: dict) -> None:
    ctx["scheduler_task"] = asyncio.create_task(_scheduler_loop())


async def shutdown(ctx: dict) -> None:
    task: asyncio.Task | None = ctx.pop("scheduler_task", None)
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class WorkerSettings:
    functions = [ping, schedule_jobs]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 10
    job_timeout = 300
    keep_result = 3600
