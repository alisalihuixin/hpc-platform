"""Billing reports (F6.1).

Members may read their own monthly buckets; admins may read everything and can
force a materialized-view refresh.  Refresh is a no-op (``refreshed=false``) on
SQLite, where the report is aggregated live instead.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.errors import PermissionDeniedError
from app.deps import CurrentUser, SessionDep, require_roles
from app.schemas.report import MonthlyBillingReport, MonthlyBillingRow, RefreshResult
from app.services import report_service

router = APIRouter(prefix="/reports", tags=["reports"])

admin_only = [Depends(require_roles("admin"))]


@router.get(
    "/monthly",
    response_model=MonthlyBillingReport,
    summary="Monthly billing by user x group (admin sees all)",
)
async def monthly_report(
    db: SessionDep,
    user: CurrentUser,
    user_id: Annotated[int | None, Query(ge=1, description="Admin only: filter by user")] = None,
    group_id: Annotated[int | None, Query(ge=1)] = None,
    since: Annotated[datetime | None, Query(description="First month to include")] = None,
    until: Annotated[datetime | None, Query(description="Last month to include")] = None,
) -> MonthlyBillingReport:
    is_admin = user.has_role("admin")
    if not is_admin:
        if user_id is not None and user_id != user.id:
            raise PermissionDeniedError("You can only read your own billing report")
        user_id = user.id

    rows = await report_service.monthly_billing(
        db, user_id=user_id, group_id=group_id, since=since, until=until
    )
    total = sum((row.gpu_hours for row in rows), Decimal(0))
    return MonthlyBillingReport(
        items=[
            MonthlyBillingRow(
                month=row.month,
                user_id=row.user_id,
                username=row.username,
                group_id=row.group_id,
                group_name=row.group_name,
                gpu_hours=row.gpu_hours,
                job_count=row.job_count,
            )
            for row in rows
        ],
        total_gpu_hours=total,
    )


@router.post(
    "/monthly/refresh",
    response_model=RefreshResult,
    dependencies=admin_only,
    summary="Refresh the monthly materialized view (admin)",
)
async def refresh_monthly(db: SessionDep) -> RefreshResult:
    refreshed = await report_service.refresh_monthly_billing(db)
    return RefreshResult(
        refreshed=refreshed,
        detail=(
            "materialized view refreshed"
            if refreshed
            else "no materialized view on this database; reports are aggregated live"
        ),
    )
