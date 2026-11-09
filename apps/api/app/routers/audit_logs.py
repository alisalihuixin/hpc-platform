"""Audit log endpoint (F7.3) - admin only, read-only."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.deps import SessionDep, require_roles
from app.schemas.audit import AuditLogList, AuditLogRead
from app.services import audit_service

router = APIRouter(
    prefix="/audit-logs",
    tags=["audit"],
    dependencies=[Depends(require_roles("admin"))],
)


@router.get("", response_model=AuditLogList, summary="List audit rows (admin)")
async def list_audit_logs(
    db: SessionDep,
    table_name: Annotated[str | None, Query(max_length=64)] = None,
    record_id: Annotated[int | None, Query(ge=1)] = None,
    action: Annotated[Literal["update", "delete"] | None, Query()] = None,
    changed_by: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[datetime | None, Query(description="changed_at >= this")] = None,
    until: Annotated[datetime | None, Query(description="changed_at <= this")] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditLogList:
    rows, total = await audit_service.list_logs(
        db,
        table_name=table_name,
        record_id=record_id,
        action=action,
        changed_by=changed_by,
        since=since,
        until=until,
        skip=skip,
        limit=limit,
    )
    return AuditLogList(items=[AuditLogRead.model_validate(row) for row in rows], total=total)
