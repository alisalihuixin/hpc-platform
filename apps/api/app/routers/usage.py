"""Usage endpoints (F5.3 / F5.4).

``/usage/me`` is the member-facing detail; the group ranking is admin-only
(the PRD's "组管" role does not exist yet, so admins stand in for it).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.deps import CurrentUser, SessionDep, require_roles
from app.schemas.usage import (
    GroupUsageRanking,
    UsageEventRead,
    UserUsage,
    UserUsageRank,
)
from app.services import usage_service

router = APIRouter(prefix="/usage", tags=["usage"])

admin_only = [Depends(require_roles("admin"))]


@router.get("/me", response_model=UserUsage, summary="My usage detail")
async def my_usage(
    db: SessionDep,
    user: CurrentUser,
    since: Annotated[
        datetime | None, Query(description="Only events started at or after this time")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Only events started at or before this time")
    ] = None,
) -> UserUsage:
    events, total = await usage_service.list_user_usage(db, user.id, since=since, until=until)
    return UserUsage(
        items=[UsageEventRead.model_validate(event) for event in events],
        total=len(events),
        total_gpu_hours=total,
    )


@router.get(
    "/groups/{group_id}",
    response_model=GroupUsageRanking,
    dependencies=admin_only,
    summary="Group usage ranking (admin)",
)
async def group_usage(
    group_id: int,
    db: SessionDep,
    since: Annotated[
        datetime | None, Query(description="Only events started at or after this time")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Only events started at or before this time")
    ] = None,
) -> GroupUsageRanking:
    group, rows, total = await usage_service.group_ranking(db, group_id, since=since, until=until)
    return GroupUsageRanking(
        group_id=group.id,
        group_name=group.name,
        total_gpu_hours=total,
        items=[
            UserUsageRank(
                user_id=user_id,
                username=username,
                gpu_hours=gpu_hours,
                job_count=job_count,
            )
            for user_id, username, gpu_hours, job_count in rows
        ],
    )
