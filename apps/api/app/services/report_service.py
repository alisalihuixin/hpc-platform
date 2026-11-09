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
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group
from app.models.job import Job, JobStatus
from app.models.node import Node, NodeStatus
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


@dataclass(frozen=True)
class DashboardSnapshot:
    running_jobs: int
    queued_jobs: int
    online_nodes: int
    total_nodes: int
    gpu_used: int
    gpu_total: int
    #: ``(month, net_gpu_hours)`` oldest first, zero-filled.
    trend: list[tuple[str, Decimal]]
    top_users: list[tuple[int, str | None, Decimal]]
    top_groups: list[tuple[int, str | None, Decimal]]


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


async def dashboard_overview(
    db: AsyncSession, *, now: datetime | None = None, months: int = 6, top: int = 5
) -> DashboardSnapshot:
    """Platform-wide numbers for the admin dashboard (D39).

    Reuses :func:`monthly_billing`, so the trend and TOP lists follow the same
    reversal-aware rule as the report page. Missing months are zero-filled so
    the chart never has gaps.
    """
    moment = now or datetime.now(UTC)

    status_counts = dict(
        (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    )

    node_row = (
        await db.execute(
            select(
                func.count().label("total"),
                func.sum(case((Node.status == NodeStatus.ONLINE.value, 1), else_=0)).label(
                    "online"
                ),
                func.coalesce(func.sum(Node.gpu_used), 0).label("gpu_used"),
                func.coalesce(func.sum(Node.gpu_total), 0).label("gpu_total"),
            )
        )
    ).one()

    start_year, start_month = _shift_month(moment.year, moment.month, -(months - 1))
    buckets: dict[str, Decimal] = {}
    for index in range(months):
        year, month = _shift_month(start_year, start_month, index)
        buckets[f"{year:04d}-{month:02d}"] = Decimal(0)
    for row in await monthly_billing(db, since=datetime(start_year, start_month, 1, tzinfo=UTC)):
        if row.month in buckets:
            buckets[row.month] += row.gpu_hours

    user_totals: dict[int, tuple[str | None, Decimal]] = {}
    group_totals: dict[int, tuple[str | None, Decimal]] = {}
    for row in await monthly_billing(db, since=datetime(moment.year, moment.month, 1, tzinfo=UTC)):
        name, hours = user_totals.get(row.user_id, (row.username, Decimal(0)))
        user_totals[row.user_id] = (name, hours + row.gpu_hours)
        name, hours = group_totals.get(row.group_id, (row.group_name, Decimal(0)))
        group_totals[row.group_id] = (name, hours + row.gpu_hours)

    return DashboardSnapshot(
        running_jobs=int(status_counts.get(JobStatus.RUNNING.value, 0)),
        queued_jobs=int(status_counts.get(JobStatus.QUEUED.value, 0)),
        online_nodes=int(node_row.online or 0),
        total_nodes=int(node_row.total or 0),
        gpu_used=int(node_row.gpu_used or 0),
        gpu_total=int(node_row.gpu_total or 0),
        trend=list(buckets.items()),
        top_users=[
            (user_id, name, hours)
            for user_id, (name, hours) in sorted(
                user_totals.items(), key=lambda item: item[1][1], reverse=True
            )[:top]
        ],
        top_groups=[
            (group_id, name, hours)
            for group_id, (name, hours) in sorted(
                group_totals.items(), key=lambda item: item[1][1], reverse=True
            )[:top]
        ],
    )
