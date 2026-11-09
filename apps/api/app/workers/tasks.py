"""ARQ tasks."""

from __future__ import annotations

from typing import Any

from app.core.redis import get_redis
from app.db.session import async_session_factory
from app.services import report_service, scheduler_service


async def ping(ctx: dict[str, Any], message: str = "pong") -> str:
    return f"{message} from worker {ctx.get('job_id', 'unknown')}"


async def schedule_jobs(_ctx: dict[str, Any]) -> int:
    """One scheduler cycle: renew leases, reclaim orphans, start, then finish.

    Returns how many jobs were started (the poll loop only logs failures).
    State changes are published to Redis so SSE clients react immediately.
    """
    redis = get_redis()
    async with async_session_factory() as db:
        await scheduler_service.renew_leases(db)
        await scheduler_service.recover_orphaned_jobs(db, redis=redis)
        started = await scheduler_service.schedule_once(db, redis=redis)
        await scheduler_service.finish_due_jobs(db, redis=redis)
        return started


async def refresh_monthly_report(_ctx: dict[str, Any]) -> bool:
    """Hourly ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` (F6.1).

    Returns ``False`` on databases without materialized views (SQLite).
    """
    async with async_session_factory() as db:
        return await report_service.refresh_monthly_billing(db)
