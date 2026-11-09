"""Audit log queries (F7.3).

The table is written exclusively by database triggers; this module only reads.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def list_logs(
    db: AsyncSession,
    *,
    table_name: str | None = None,
    record_id: int | None = None,
    action: str | None = None,
    changed_by: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[AuditLog], int]:
    conditions = []
    if table_name is not None:
        conditions.append(AuditLog.table_name == table_name)
    if record_id is not None:
        conditions.append(AuditLog.record_id == record_id)
    if action is not None:
        conditions.append(AuditLog.action == action)
    if changed_by is not None:
        conditions.append(AuditLog.changed_by == changed_by)
    if since is not None:
        conditions.append(AuditLog.changed_at >= since)
    if until is not None:
        conditions.append(AuditLog.changed_at <= until)

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*conditions)) or 0
    rows = await db.scalars(
        select(AuditLog)
        .where(*conditions)
        .order_by(AuditLog.changed_at.desc(), AuditLog.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(rows.all()), int(total)
