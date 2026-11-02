"""Group management use-cases (F8.2).

A group owns exactly one quota row.  Groups are *soft* deleted - jobs and usage
rows reference them by id, so flipping ``is_active`` keeps history readable.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.group import Group
from app.models.quota import Quota
from app.models.user import User
from app.schemas.group import GroupCreate, GroupUpdate


async def get_group(db: AsyncSession, group_id: int) -> Group | None:
    return await db.get(Group, group_id)


async def get_group_or_404(db: AsyncSession, group_id: int) -> Group:
    group = await get_group(db, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    return group


async def list_groups(
    db: AsyncSession, *, skip: int = 0, limit: int = 100
) -> tuple[list[Group], int]:
    total = await db.scalar(select(func.count()).select_from(Group)) or 0
    rows = await db.scalars(select(Group).order_by(Group.name).offset(skip).limit(limit))
    return list(rows.all()), int(total)


async def _resolve_members(db: AsyncSession, user_ids: list[int]) -> list[User]:
    unique = list(dict.fromkeys(user_ids))
    if not unique:
        return []
    users = list((await db.scalars(select(User).where(User.id.in_(unique)))).all())
    missing = set(unique) - {user.id for user in users}
    if missing:
        raise NotFoundError(f"Unknown user id(s): {', '.join(str(i) for i in sorted(missing))}")
    return users


async def _ensure_name_free(db: AsyncSession, name: str, *, exclude_id: int | None = None) -> None:
    stmt = select(Group.id).where(Group.name == name)
    if exclude_id is not None:
        stmt = stmt.where(Group.id != exclude_id)
    if await db.scalar(stmt) is not None:
        raise ConflictError("Group name is already taken")


async def create_group(db: AsyncSession, payload: GroupCreate) -> Group:
    await _ensure_name_free(db, payload.name)

    group = Group(
        name=payload.name,
        description=payload.description,
        members=await _resolve_members(db, payload.member_ids),
        quota=Quota(
            gpu_limit=payload.gpu_limit,
            max_nodes_per_job=payload.max_nodes_per_job,
            max_gpus_per_job=payload.max_gpus_per_job,
        ),
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    return group


async def update_group(db: AsyncSession, group_id: int, payload: GroupUpdate) -> Group:
    group = await get_group_or_404(db, group_id)

    if payload.name is not None and payload.name != group.name:
        await _ensure_name_free(db, payload.name, exclude_id=group.id)
        group.name = payload.name
    if payload.description is not None:
        group.description = payload.description
    if payload.is_active is not None:
        group.is_active = payload.is_active

    await db.commit()
    await db.refresh(group)
    return group


async def deactivate_group(db: AsyncSession, group_id: int) -> None:
    """Soft delete: refuse while the group still holds reserved GPU units."""
    group = await get_group_or_404(db, group_id)
    if group.quota is not None and group.quota.gpu_used > 0:
        raise ConflictError(
            f"Group still holds {group.quota.gpu_used} GPU unit(s); cancel or finish its jobs first"
        )
    group.is_active = False
    await db.commit()


async def set_members(db: AsyncSession, group_id: int, user_ids: list[int]) -> Group:
    group = await get_group_or_404(db, group_id)
    group.members = await _resolve_members(db, user_ids)
    await db.commit()
    await db.refresh(group)
    return group
