"""Quota endpoints (F3.4 / F3.5).

``GET /quotas/me`` is the member-facing numeric view of the group's remaining
capacity; the other two are admin-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import CurrentUser, SessionDep, require_roles
from app.schemas.quota import QuotaList, QuotaRead, QuotaUpdate
from app.services import quota_service

router = APIRouter(prefix="/quotas", tags=["quotas"])

admin_only = [Depends(require_roles("admin"))]


# Declared before ``/{group_id}`` so "me" is not parsed as a path id.
@router.get("/me", response_model=QuotaList, summary="Quotas of my groups")
async def my_quotas(db: SessionDep, user: CurrentUser) -> QuotaList:
    quotas = await quota_service.list_user_quotas(db, user.id)
    return QuotaList(items=[QuotaRead.model_validate(quota) for quota in quotas], total=len(quotas))


@router.get(
    "/{group_id}",
    response_model=QuotaRead,
    dependencies=admin_only,
    summary="Get a group's quota (admin)",
)
async def get_group_quota(group_id: int, db: SessionDep) -> QuotaRead:
    quota = await quota_service.get_quota_or_404(db, group_id)
    return QuotaRead.model_validate(quota)


@router.patch(
    "/{group_id}",
    response_model=QuotaRead,
    dependencies=admin_only,
    summary="Adjust a group's quota (admin)",
)
async def update_group_quota(group_id: int, payload: QuotaUpdate, db: SessionDep) -> QuotaRead:
    quota = await quota_service.update_quota(db, group_id, payload)
    return QuotaRead.model_validate(quota)
