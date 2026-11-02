"""Group administration endpoints (F8.2) - admin only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.deps import SessionDep, require_roles
from app.schemas.group import GroupCreate, GroupList, GroupMembersUpdate, GroupRead, GroupUpdate
from app.services import group_service

router = APIRouter(
    prefix="/groups",
    tags=["groups"],
    dependencies=[Depends(require_roles("admin"))],
)


@router.get("", response_model=GroupList, summary="List groups (admin)")
async def list_groups(
    db: SessionDep,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> GroupList:
    groups, total = await group_service.list_groups(db, skip=skip, limit=limit)
    return GroupList(items=[GroupRead.model_validate(group) for group in groups], total=total)


@router.post(
    "",
    response_model=GroupRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create group (admin)",
)
async def create_group(payload: GroupCreate, db: SessionDep) -> GroupRead:
    group = await group_service.create_group(db, payload)
    return GroupRead.model_validate(group)


@router.get("/{group_id}", response_model=GroupRead, summary="Get group (admin)")
async def get_group(group_id: int, db: SessionDep) -> GroupRead:
    group = await group_service.get_group_or_404(db, group_id)
    return GroupRead.model_validate(group)


@router.patch("/{group_id}", response_model=GroupRead, summary="Update group (admin)")
async def update_group(group_id: int, payload: GroupUpdate, db: SessionDep) -> GroupRead:
    group = await group_service.update_group(db, group_id, payload)
    return GroupRead.model_validate(group)


@router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate group (soft delete, admin)",
)
async def delete_group(group_id: int, db: SessionDep) -> Response:
    await group_service.deactivate_group(db, group_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/{group_id}/members",
    response_model=GroupRead,
    summary="Replace group members (admin)",
)
async def replace_members(group_id: int, payload: GroupMembersUpdate, db: SessionDep) -> GroupRead:
    group = await group_service.set_members(db, group_id, payload.user_ids)
    return GroupRead.model_validate(group)
