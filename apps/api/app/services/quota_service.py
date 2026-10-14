"""Quota reservation (F3.2 - the anti-oversell core).

The rules are deliberately expressed as *conditional* ``UPDATE`` statements so
that check-and-increment happens in a single atomic statement; two concurrent
reservations can never both see the same remaining capacity (no read-modify-write
race).  The functions here do not commit - the caller owns the transaction so a
reservation and the job enqueue stay atomic.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError
from app.db.base import user_groups
from app.models.group import Group
from app.models.quota import Quota
from app.schemas.quota import QuotaUpdate


async def get_quota(db: AsyncSession, group_id: int) -> Quota | None:
    return await db.scalar(select(Quota).where(Quota.group_id == group_id))


async def get_quota_or_404(db: AsyncSession, group_id: int) -> Quota:
    quota = await get_quota(db, group_id)
    if quota is None:
        raise NotFoundError("Quota not found for group")
    return quota


async def list_user_quotas(db: AsyncSession, user_id: int) -> list[Quota]:
    """Quotas of every active group the user belongs to (F3.5)."""
    rows = await db.scalars(
        select(Quota)
        .join(Group, Group.id == Quota.group_id)
        .join(user_groups, user_groups.c.group_id == Group.id)
        .where(user_groups.c.user_id == user_id, Group.is_active.is_(True))
        .order_by(Group.name)
    )
    return list(rows.all())


async def update_quota(db: AsyncSession, group_id: int, payload: QuotaUpdate) -> Quota:
    """Admin adjustment (F3.4); only affects future submissions."""
    quota = await get_quota_or_404(db, group_id)

    changes = payload.model_dump(exclude_unset=True)
    new_limit = changes.get("gpu_limit")
    if new_limit is not None and new_limit < quota.gpu_used:
        raise ConflictError(
            f"gpu_limit cannot be lower than the {quota.gpu_used} units already in use"
        )

    for field, value in changes.items():
        setattr(quota, field, value)

    await db.commit()
    await db.refresh(quota)
    return quota


async def reserve(db: AsyncSession, group_id: int, amount: int) -> bool:
    """Atomically reserve ``amount`` from the group's GPU pool.

    Returns ``True`` when the reservation fit, ``False`` when it would exceed
    ``gpu_limit`` (nothing is changed in that case).  The caller is responsible
    for committing.
    """
    if amount <= 0:
        raise AppError("Reservation amount must be positive")

    result = await db.execute(
        update(Quota)
        .where(
            Quota.group_id == group_id,
            Quota.gpu_used + amount <= Quota.gpu_limit,
        )
        .values(gpu_used=Quota.gpu_used + amount)
    )
    return result.rowcount == 1


async def reserve_or_raise(db: AsyncSession, group_id: int, amount: int) -> Quota:
    """Like :func:`reserve` but raises ``ConflictError`` (409) on overshoot."""
    if not await reserve(db, group_id, amount):
        raise ConflictError("Resource quota exceeded")
    quota = await get_quota(db, group_id)
    if quota is None:  # pragma: no cover - guarded by the UPDATE above
        raise NotFoundError("Quota not found for group")
    return quota


async def release(db: AsyncSession, group_id: int, amount: int) -> bool:
    """Return previously reserved GPU units (F3.3).

    Clamped so the pool can never go negative; returns ``False`` when there was
    nothing left to release.  The caller is responsible for committing.
    """
    if amount <= 0:
        raise AppError("Release amount must be positive")

    result = await db.execute(
        update(Quota)
        .where(Quota.group_id == group_id, Quota.gpu_used >= amount)
        .values(gpu_used=Quota.gpu_used - amount)
    )
    return result.rowcount == 1
