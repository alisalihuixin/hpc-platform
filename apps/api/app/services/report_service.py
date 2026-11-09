"""Monthly billing report (F6.1).

Production reads the ``mv_monthly_billing`` materialized view (refreshed hourly
by the worker); SQLite/test environments have no materialized views, so the same
buckets are aggregated from ``usage_events`` on the fly.  Both paths return the
same :class:`MonthlyRow` shape.

Reversal rows are subtracted (``reversed=True``), matching ``usage_service``, and
``job_count`` counts distinct jobs so a compensating row never inflates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group
from app.models.usage_event import UsageEvent
from app.models.user import User

MATERIALIZED_VIEW = "mv_monthly_billing"


@dataclass(frozen=True)
class MonthlyRow:
    month: str
    user_id: int
    username: str | None
    group_id: int
    group_name: str | None
    gpu_hours: Decimal
    job_count: int


def _is_postgres(db: AsyncSession) -> bool:
    bind = db.bind
    return bind is not None and bind.dialect.name == "postgresql"


async def monthly_billing(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    group_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[MonthlyRow]:
    if _is_postgres(db):
        return await _from_view(db, user_id=user_id, group_id=group_id, since=since, until=until)
    return await _from_events(db, user_id=user_id, group_id=group_id, since=since, until=until)


async def _from_view(
    db: AsyncSession,
    *,
    user_id: int | None,
    group_id: int | None,
    since: datetime | None,
    until: datetime | None,
) -> list[MonthlyRow]:
    rows = await db.execute(
        text(
            f"""
            SELECT to_char(m.month, 'YYYY-MM') AS month,
                   m.user_id,
                   u.username,
                   m.group_id,
                   g.name AS group_name,
                   m.gpu_hours,
                   m.job_count
            FROM {MATERIALIZED_VIEW} m
            LEFT JOIN users u ON u.id = m.user_id
            LEFT JOIN groups g ON g.id = m.group_id
            WHERE (CAST(:user_id AS bigint) IS NULL OR m.user_id = CAST(:user_id AS bigint))
              AND (CAST(:group_id AS bigint) IS NULL OR m.group_id = CAST(:group_id AS bigint))
              AND (CAST(:since AS timestamptz) IS NULL
                   OR m.month >= date_trunc('month', CAST(:since AS timestamptz)))
              AND (CAST(:until AS timestamptz) IS NULL
                   OR m.month <= date_trunc('month', CAST(:until AS timestamptz)))
            ORDER BY m.month DESC, m.gpu_hours DESC
            """
        ),
        {"user_id": user_id, "group_id": group_id, "since": since, "until": until},
    )
    return [
        MonthlyRow(
            month=row.month,
            user_id=row.user_id,
            username=row.username,
            group_id=row.group_id,
            group_name=row.group_name,
            gpu_hours=Decimal(str(row.gpu_hours)),
            job_count=int(row.job_count),
        )
        for row in rows.all()
    ]


async def _from_events(
    db: AsyncSession,
    *,
    user_id: int | None,
    group_id: int | None,
    since: datetime | None,
    until: datetime | None,
) -> list[MonthlyRow]:
    month = func.strftime("%Y-%m", UsageEvent.started_at)
    signed_gpu_hours = case(
        (UsageEvent.reversed.is_(True), -UsageEvent.gpu_hours),
        else_=UsageEvent.gpu_hours,
    )

    stmt = (
        select(
            month.label("month"),
            UsageEvent.user_id,
            User.username,
            UsageEvent.group_id,
            Group.name.label("group_name"),
            func.sum(signed_gpu_hours).label("gpu_hours"),
            func.count(func.distinct(UsageEvent.job_id)).label("job_count"),
        )
        .join(User, User.id == UsageEvent.user_id)
        .join(Group, Group.id == UsageEvent.group_id)
        .group_by(month, UsageEvent.user_id, User.username, UsageEvent.group_id, Group.name)
        .order_by(month.desc(), func.sum(signed_gpu_hours).desc())
    )

    conditions = []
    if user_id is not None:
        conditions.append(UsageEvent.user_id == user_id)
    if group_id is not None:
        conditions.append(UsageEvent.group_id == group_id)
    if since is not None:
        conditions.append(UsageEvent.started_at >= since)
    if until is not None:
        conditions.append(UsageEvent.started_at <= until)
    if conditions:
        stmt = stmt.where(*conditions)

    rows = await db.execute(stmt)
    return [
        MonthlyRow(
            month=row.month,
            user_id=row.user_id,
            username=row.username,
            group_id=row.group_id,
            group_name=row.group_name,
            gpu_hours=Decimal(str(row.gpu_hours)),
            job_count=int(row.job_count),
        )
        for row in rows.all()
    ]


async def refresh_monthly_billing(db: AsyncSession, *, concurrently: bool = True) -> bool:
    """Refresh the materialized view. Returns ``False`` when it does not exist.

    ``REFRESH ... CONCURRENTLY`` cannot run inside a transaction block, so the
    statement goes through an AUTOCOMMIT connection borrowed from the engine.
    """
    bind = db.bind
    if bind is None or bind.dialect.name != "postgresql":
        return False

    statement = (
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {MATERIALIZED_VIEW}"
        if concurrently
        else f"REFRESH MATERIALIZED VIEW {MATERIALIZED_VIEW}"
    )
    autocommit = bind.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit.connect() as conn:
        await conn.execute(text(statement))
    return True
