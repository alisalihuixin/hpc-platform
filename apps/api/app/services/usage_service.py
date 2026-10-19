"""Usage queries (F5.3 personal detail / F5.4 group ranking).

Both queries read the append-only ``usage_events`` table and report **net**
GPU-hours: reversal rows (``reversed=True``) are subtracted from the originals.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.group import Group
from app.models.usage_event import UsageEvent
from app.models.user import User


def _time_conditions(since: datetime | None, until: datetime | None) -> list:
    conditions = []
    if since is not None:
        conditions.append(UsageEvent.started_at >= since)
    if until is not None:
        conditions.append(UsageEvent.started_at <= until)
    return conditions


async def list_user_usage(
    db: AsyncSession,
    user_id: int,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[list[UsageEvent], Decimal]:
    """Personal detail rows plus their net total (F5.3).

    Every matching row is returned (the time window is the natural bound), so
    ``sum(item.signed_gpu_hours)`` always equals the returned total.
    """
    events = list(
        (
            await db.scalars(
                select(UsageEvent)
                .where(
                    UsageEvent.user_id == user_id,
                    *_time_conditions(since, until),
                )
                .order_by(UsageEvent.started_at.desc(), UsageEvent.id.desc())
            )
        ).all()
    )
    total = sum((event.signed_gpu_hours for event in events), Decimal(0))
    return events, total


async def group_ranking(
    db: AsyncSession,
    group_id: int,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[Group, list[tuple[int, str | None, Decimal, int]], Decimal]:
    """Per-member net GPU-hours for a group, highest first (F5.4).

    ``job_count`` counts distinct jobs, so a reversal row never inflates it.
    """
    group = await db.get(Group, group_id)
    if group is None:
        raise NotFoundError("Group not found")

    signed_gpu_hours = case(
        (UsageEvent.reversed.is_(True), -UsageEvent.gpu_hours),
        else_=UsageEvent.gpu_hours,
    )
    rows = await db.execute(
        select(
            UsageEvent.user_id,
            User.username,
            func.sum(signed_gpu_hours).label("gpu_hours"),
            func.count(func.distinct(UsageEvent.job_id)).label("job_count"),
        )
        .join(User, User.id == UsageEvent.user_id)
        .where(
            UsageEvent.group_id == group_id,
            *_time_conditions(since, until),
        )
        .group_by(UsageEvent.user_id, User.username)
        .order_by(func.sum(signed_gpu_hours).desc(), UsageEvent.user_id.asc())
    )

    items = [
        (row.user_id, row.username, Decimal(str(row.gpu_hours)), int(row.job_count))
        for row in rows.all()
    ]
    total = sum((item[2] for item in items), Decimal(0))
    return group, items, total
