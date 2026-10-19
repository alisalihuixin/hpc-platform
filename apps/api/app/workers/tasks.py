"""ARQ tasks."""

from __future__ import annotations

from typing import Any

from app.db.session import async_session_factory
from app.services import scheduler_service


async def ping(ctx: dict[str, Any], message: str = "pong") -> str:
    return f"{message} from worker {ctx.get('job_id', 'unknown')}"


async def schedule_jobs(_ctx: dict[str, Any]) -> int:
    """One scheduler pass, usable as a queued task or from the poll loop."""
    async with async_session_factory() as db:
        return await scheduler_service.schedule_once(db)
