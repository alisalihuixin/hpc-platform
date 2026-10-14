"""Job submission (F4.2).

The order is deliberate:

1. membership check - you may only spend your groups' capacity;
2. per-job ceiling check against the group's quota (422, field-level);
3. atomic GPU reservation (409 on overshoot);
4. enqueue the ``queued`` job.

Steps 3 and 4 share one transaction: ``reserve`` issues a conditional ``UPDATE``
but never commits, so if the insert fails the reservation rolls back with it and
the pool can never leak.  Nothing here reads-then-writes ``gpu_used``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError, UnprocessableEntityError
from app.db.base import user_groups
from app.models.group import Group
from app.models.job import Job
from app.models.user import User
from app.schemas.job import JobCreate
from app.services import quota_service


async def _member_group(db: AsyncSession, user_id: int, group_id: int) -> Group:
    """Return the active group, asserting the caller is a member."""
    group = await db.scalar(select(Group).where(Group.id == group_id, Group.is_active.is_(True)))
    if group is None:
        raise NotFoundError("Group not found")

    membership = await db.scalar(
        select(user_groups.c.user_id).where(
            user_groups.c.user_id == user_id,
            user_groups.c.group_id == group.id,
        )
    )
    if membership is None:
        raise PermissionDeniedError("You are not a member of this group")
    return group


def _ceiling_errors(payload: JobCreate, *, max_nodes: int, max_gpus: int) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if payload.num_nodes > max_nodes:
        errors.append(
            {
                "loc": ["body", "num_nodes"],
                "msg": f"num_nodes exceeds the group ceiling of {max_nodes}",
                "type": "value_error.ceiling",
            }
        )
    if payload.num_gpus > max_gpus:
        errors.append(
            {
                "loc": ["body", "num_gpus"],
                "msg": f"num_gpus exceeds the group ceiling of {max_gpus}",
                "type": "value_error.ceiling",
            }
        )
    return errors


async def submit_job(db: AsyncSession, user: User, payload: JobCreate) -> Job:
    """Validate, reserve the GPUs and enqueue - all in one transaction."""
    group = await _member_group(db, user.id, payload.group_id)
    quota = await quota_service.get_quota_or_404(db, group.id)

    errors = _ceiling_errors(
        payload,
        max_nodes=quota.max_nodes_per_job,
        max_gpus=quota.max_gpus_per_job,
    )
    if errors:
        raise UnprocessableEntityError(errors)

    # ``num_gpus == 0`` is a CPU-only job: nothing to reserve, and reserve()
    # rejects non-positive amounts.
    if payload.num_gpus > 0:
        await quota_service.reserve_or_raise(db, group.id, payload.num_gpus)

    job = Job(
        user_id=user.id,
        group_id=group.id,
        name=payload.name,
        num_nodes=payload.num_nodes,
        num_gpus=payload.num_gpus,
        walltime_min=payload.walltime_min,
        params=payload.params,
    )
    db.add(job)
    # Commits the reservation and the job row together.
    await db.commit()
    await db.refresh(job)
    return job
