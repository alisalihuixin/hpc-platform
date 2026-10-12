"""User administration endpoints - restricted to the ``admin`` role."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.errors import NotFoundError
from app.deps import SessionDep, require_roles
from app.schemas.user import UserCreate, UserList, UserRead, UserUpdate
from app.services import user_service

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_roles("admin"))],
)


@router.get("", response_model=UserList, summary="List users (admin)")
async def list_users(
    db: SessionDep,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> UserList:
    users, total = await user_service.list_users(db, skip=skip, limit=limit)
    return UserList(items=[UserRead.model_validate(u) for u in users], total=total)


@router.post(
    "", response_model=UserRead, status_code=status.HTTP_201_CREATED, summary="Create user (admin)"
)
async def create_user(payload: UserCreate, db: SessionDep) -> UserRead:
    user = await user_service.create_user(db, payload)
    return UserRead.model_validate(user)


@router.get("/{user_id}", response_model=UserRead, summary="Get user (admin)")
async def get_user(user_id: int, db: SessionDep) -> UserRead:
    user = await user_service.get_by_id(db, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return UserRead.model_validate(user)


@router.patch("/{user_id}", response_model=UserRead, summary="Update user (admin)")
async def update_user(user_id: int, payload: UserUpdate, db: SessionDep) -> UserRead:
    user = await user_service.update_user(db, user_id, payload)
    return UserRead.model_validate(user)
