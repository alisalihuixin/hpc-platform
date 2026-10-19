"""ARQ tasks."""

from __future__ import annotations

from typing import Any

from app.db.session import async_session_factory
from app.services import scheduler_service


async def ping(ctx: dict[str, Any], message: str = "pong") -> str:
    return f"{message} from worker {ctx.get('job_id', 'unknown')}"


async def schedule_jobs(_ctx: dict[str, Any]) -> int:
    """One scheduler cycle: start queued jobs, then finish running ones.

    Returns how many jobs were started (the poll loop only logs failures).
    """
    async with async_session_factory() as db:
        started = await scheduler_service.schedule_once(db)
        await scheduler_service.finish_due_jobs(db)
        return started
