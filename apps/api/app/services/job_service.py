"""Job submission, listing and cancellation (F4.2 / F4.4 / F4.5).

The submission order is deliberate:

1. membership check - you may only spend your groups' capacity;
2. per-job ceiling check against the group's quota (422, field-level);
3. atomic GPU reservation (409 on overshoot);
4. enqueue the ``queued`` job.

Steps 3 and 4 share one transaction: ``reserve`` issues a conditional ``UPDATE``
but never commits, so if the insert fails the reservation rolls back with it and
the pool can never leak.  Nothing here reads-then-writes ``gpu_used``.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)
from app.core.events import publish_job_event
from app.db.base import user_groups
from app.models.group import Group
from app.models.job import CANCELABLE_STATUSES, Job, JobStatus
from app.models.user import User
from app.schemas.job import JobCreate
from app.services import node_service, quota_service


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


async def submit_job(
    db: AsyncSession, user: User, payload: JobCreate, *, redis: Redis | None = None
) -> Job:
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
        num_cpus=payload.num_cpus,
        mem_gb=payload.mem_gb,
        priority=payload.priority,
        walltime_min=payload.walltime_min,
        params=payload.params,
    )
    db.add(job)
    # Commits the reservation and the job row together.
    await db.commit()
    await db.refresh(job)
    await publish_job_event(redis, job_id=job.id, user_id=job.user_id, status=job.status)
    return job


async def get_job(db: AsyncSession, job_id: int) -> Job | None:
    return await db.get(Job, job_id)


async def get_visible_job(db: AsyncSession, user: User, job_id: int) -> Job:
    """Load a job the caller may see: its owner or an admin (F4.6)."""
    job = await get_job(db, job_id)
    if job is None:
        raise NotFoundError("Job not found")
    if job.user_id != user.id and not user.has_role("admin"):
        raise PermissionDeniedError("You can only access your own jobs")
    return job


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; normalise before arithmetic."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def used_gpu_minutes(job: Job) -> int:
    """Consumed GPU-minutes (F4.6): GPU count x elapsed minutes, rounded up.

    Matches the billing rule in F5.2 (per-minute ceiling).  A queued job has
    consumed nothing; a running job counts up to "now".
    """
    if job.started_at is None:
        return 0
    end = _as_utc(job.ended_at or datetime.now(UTC))
    elapsed_seconds = max((end - _as_utc(job.started_at)).total_seconds(), 0)
    return job.num_gpus * math.ceil(elapsed_seconds / 60)


def _log_time(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def build_log(job: Job) -> str:
    """Mock job log derived from the state machine (real logs arrive later)."""
    lines = [
        f"{_log_time(job.queued_at)} [queued] job accepted "
        f"(nodes={job.num_nodes}, gpus={job.num_gpus}, walltime={job.walltime_min}m)"
    ]
    if job.started_at is not None:
        lines.append(
            f"{_log_time(job.started_at)} [running] allocated "
            f"{job.num_gpus} GPU(s) on node {job.node_id or 'pending'}"
        )
    if job.ended_at is not None:
        lines.append(f"{_log_time(job.ended_at)} [{job.status}] job {job.status}")
    return "\n".join(lines)


async def list_jobs(
    db: AsyncSession,
    user: User,
    *,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[Job], int]:
    """Paginated job list (F4.5); non-admins only see their own jobs.

    Filters apply to ``queued_at`` so "jobs submitted in a window" is a single
    index range scan on ``(status, queued_at)`` / ``(user_id, created_at)``.
    """
    conditions = []
    if not user.has_role("admin"):
        conditions.append(Job.user_id == user.id)
    if status is not None:
        conditions.append(Job.status == status)
    if since is not None:
        conditions.append(Job.queued_at >= since)
    if until is not None:
        conditions.append(Job.queued_at <= until)

    total = await db.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0
    rows = await db.scalars(
        select(Job)
        .where(*conditions)
        .order_by(Job.queued_at.desc(), Job.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(rows.all()), int(total)


async def cancel_job(
    db: AsyncSession, user: User, job_id: int, *, redis: Redis | None = None
) -> Job:
    """Cancel a queued/running job (F4.4) and return its reserved GPUs.

    The state check and the write are one conditional ``UPDATE`` so two
    concurrent cancels cannot both succeed: the loser sees ``rowcount == 0`` and
    gets a 409 - it must not release quota a second time.
    """
    job = await get_visible_job(db, user, job_id)
    was_running = job.status == JobStatus.RUNNING.value

    result = await db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status.in_([s.value for s in CANCELABLE_STATUSES]))
        .values(status=JobStatus.CANCELED.value, ended_at=func.now())
    )
    if result.rowcount != 1:
        raise ConflictError("Job is already in a terminal state")

    # The reservation was taken at submit time; a canceled job frees it.  Any
    # GPU-minutes actually consumed are billed from usage_events (D22), not
    # from the quota pool.
    if job.num_gpus > 0:
        await quota_service.release(db, job.group_id, job.num_gpus)

    # A running job also holds physical node slots; release them so the node
    # does not leak capacity when the scheduler never sees a terminal finish.
    if was_running:
        await node_service.release_slots(
            db,
            node_id=job.node_id,
            num_gpus=job.num_gpus,
            num_cpus=job.num_cpus,
            mem_gb=job.mem_gb,
        )

    await db.commit()
    await db.refresh(job)
    await publish_job_event(redis, job_id=job.id, user_id=job.user_id, status=job.status)
    return job
