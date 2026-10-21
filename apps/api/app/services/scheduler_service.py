"""Simulated scheduler (F4.3).

A cycle has four passes, in this order:

**Renew** - extend the lease of every running job whose lease has not expired.
Legacy rows without a lease are adopted.  Expired leases are deliberately not
renewed: they belong to a worker that died.

**Recover** - running jobs with an expired lease are failed, their node slots
are released and their consumed time is billed.  This is the crash-safety net.

**Start** - order queued jobs through a pluggable ``SchedulingPolicy`` (FIFO or
priority), then claim GPU + CPU + memory slots on the least-loaded online node
with one conditional ``UPDATE`` per job.  If no node fits, the job stays queued.

**Finish** - running jobs that hit walltime (or an injected random failure)
become completed/failed, release their slots and append one ``usage_events``
row.

Quota was already reserved at submission (F4.2); the scheduler only accounts
physical node capacity.  Each job is its own transaction, so one unschedulable
job never blocks the queue.

MVP simplification: a job is placed on a single node even when ``num_nodes`` >
1, because ``jobs`` carries a single ``node_id``.  CPU/memory requests are the
totals placed on that node.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import settings
from app.core.events import publish_job_event
from app.models.job import Job, JobStatus
from app.models.node import Node, NodeStatus
from app.models.usage_event import UsageEvent
from app.services import billing_service, node_service, quota_service
from app.services.scheduling_policy import QueuedJob, SchedulingPolicy, get_policy


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; normalise before arithmetic."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _claim_node(db: AsyncSession, *, num_gpus: int, num_cpus: int, mem_gb: int) -> int | None:
    """Reserve GPU + CPU + memory on one online node; ``None`` when none fits."""
    candidates = (
        await db.scalars(
            select(Node)
            .where(
                Node.status == NodeStatus.ONLINE.value,
                Node.gpu_total - Node.gpu_used >= num_gpus,
                Node.cpu_cores - Node.cpu_used >= num_cpus,
                Node.mem_gb - Node.mem_used >= mem_gb,
            )
            .order_by(Node.gpu_used.asc(), Node.cpu_used.asc(), Node.hostname.asc())
            .limit(10)
        )
    ).all()

    for node in candidates:
        result = await db.execute(
            update(Node)
            .where(
                Node.id == node.id,
                Node.status == NodeStatus.ONLINE.value,
                Node.gpu_used + num_gpus <= Node.gpu_total,
                Node.cpu_used + num_cpus <= Node.cpu_cores,
                Node.mem_used + mem_gb <= Node.mem_gb,
            )
            .values(
                gpu_used=Node.gpu_used + num_gpus,
                cpu_used=Node.cpu_used + num_cpus,
                mem_used=Node.mem_used + mem_gb,
            )
        )
        if result.rowcount == 1:
            return node.id
    return None


async def schedule_once(
    db: AsyncSession,
    *,
    redis: Redis | None = None,
    policy: SchedulingPolicy | None = None,
    now: datetime | None = None,
) -> int:
    """Run a single scheduling pass; returns how many jobs were started."""
    current = _as_utc(now or datetime.now(UTC))
    resolved = policy or get_policy()
    lease_expires_at = current + timedelta(seconds=settings.scheduler_lease_seconds)

    rows = await db.execute(
        select(
            Job.id,
            Job.user_id,
            Job.priority,
            Job.queued_at,
            Job.num_gpus,
            Job.num_cpus,
            Job.mem_gb,
        ).where(Job.status == JobStatus.QUEUED.value)
    )
    queued = [
        QueuedJob(
            id=row.id,
            user_id=row.user_id,
            priority=row.priority,
            queued_at=_as_utc(row.queued_at),
            num_gpus=row.num_gpus,
            num_cpus=row.num_cpus,
            mem_gb=row.mem_gb,
        )
        for row in rows.all()
    ]

    started = 0
    for job in resolved.order(queued):
        node_id = await _claim_node(
            db, num_gpus=job.num_gpus, num_cpus=job.num_cpus, mem_gb=job.mem_gb
        )
        if node_id is None:
            # Nothing changed; close the transaction before the next job.
            await db.rollback()
            continue

        transition = await db.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.RUNNING.value,
                node_id=node_id,
                started_at=current,
                lease_expires_at=lease_expires_at,
            )
        )
        if transition.rowcount != 1:
            # Another scheduler instance got there first: give the slots back.
            await db.rollback()
            continue

        await db.commit()
        await publish_job_event(
            redis, job_id=job.id, user_id=job.user_id, status=JobStatus.RUNNING.value
        )
        started += 1

    return started


async def renew_leases(
    db: AsyncSession, *, now: datetime | None = None, ttl_seconds: int | None = None
) -> int:
    """Extend leases of live running jobs; expired ones are left for recovery."""
    current = _as_utc(now or datetime.now(UTC))
    lease_expires_at = current + timedelta(seconds=ttl_seconds or settings.scheduler_lease_seconds)
    result = await db.execute(
        update(Job)
        .where(
            Job.status == JobStatus.RUNNING.value,
            or_(Job.lease_expires_at.is_(None), Job.lease_expires_at >= current),
        )
        .values(lease_expires_at=lease_expires_at)
    )
    await db.commit()
    return int(result.rowcount or 0)


async def _finalize_job(
    db: AsyncSession,
    *,
    row,
    status: JobStatus,
    current: datetime,
    redis: Redis | None,
    extra_conditions: Sequence[ColumnElement[bool]] = (),
) -> bool:
    """Flip one running job to a terminal state, release slots, write usage."""
    transition = await db.execute(
        update(Job)
        .where(
            Job.id == row.id,
            Job.status == JobStatus.RUNNING.value,
            *extra_conditions,
        )
        .values(status=status.value, ended_at=func.now())
    )
    if transition.rowcount != 1:
        # Someone else finished it first; undo nothing and move on.
        await db.rollback()
        return False

    await node_service.release_slots(
        db,
        node_id=row.node_id,
        num_gpus=row.num_gpus,
        num_cpus=row.num_cpus,
        mem_gb=row.mem_gb,
    )

    # The group pool is a reservation for the job's lifetime; a terminal job
    # returns it.  Actual consumption is captured by ``usage_events`` below.
    if row.num_gpus > 0:
        await quota_service.release(db, row.group_id, row.num_gpus)

    if row.started_at is not None:
        elapsed_seconds = max((current - _as_utc(row.started_at)).total_seconds(), 0)
        duration_min = math.ceil(elapsed_seconds / 60)
        await db.execute(
            insert(UsageEvent).values(
                job_id=row.id,
                user_id=row.user_id,
                group_id=row.group_id,
                gpus=row.num_gpus,
                started_at=row.started_at,
                ended_at=func.now(),
                duration_min=duration_min,
                gpu_hours=billing_service.gpu_hours(row.num_gpus, duration_min),
                rate_snapshot=billing_service.rate_snapshot(row.gpu_model),
                reversed=False,
            )
        )

    await db.commit()
    await publish_job_event(redis, job_id=row.id, user_id=row.user_id, status=status.value)
    return True


_TERMINAL_ROW_FIELDS = (
    Job.id,
    Job.user_id,
    Job.group_id,
    Job.node_id,
    Job.num_gpus,
    Job.num_cpus,
    Job.mem_gb,
    Job.started_at,
    Node.gpu_model,
)


async def recover_orphaned_jobs(
    db: AsyncSession, *, now: datetime | None = None, redis: Redis | None = None
) -> int:
    """Fail running jobs whose lease expired (worker crash) and reclaim slots."""
    current = _as_utc(now or datetime.now(UTC))
    rows = await db.execute(
        select(*_TERMINAL_ROW_FIELDS)
        .outerjoin(Node, Node.id == Job.node_id)
        .where(
            Job.status == JobStatus.RUNNING.value,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < current,
        )
    )

    recovered = 0
    for row in rows.all():
        if await _finalize_job(
            db,
            row=row,
            status=JobStatus.FAILED,
            current=current,
            redis=redis,
            extra_conditions=(Job.lease_expires_at < current,),
        ):
            recovered += 1
    return recovered


async def finish_due_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    rng: Callable[[], float] = random.random,
    redis: Redis | None = None,
) -> int:
    """Finish running jobs that hit walltime (or an injected failure).

    For each finished job: flip the status, release node slots and append one
    ``usage_events`` row.  ``rng`` is injectable so tests stay deterministic;
    the failure probability comes from ``settings.scheduler_failure_rate``
    (0 by default).  Returns how many jobs were finished.
    """
    current = _as_utc(now or datetime.now(UTC))

    rows = await db.execute(
        select(*_TERMINAL_ROW_FIELDS, Job.walltime_min)
        .outerjoin(Node, Node.id == Job.node_id)
        .where(Job.status == JobStatus.RUNNING.value)
    )

    finished = 0
    for row in rows.all():
        if row.started_at is None:  # pragma: no cover - running jobs always have one
            continue
        elapsed_seconds = max((current - _as_utc(row.started_at)).total_seconds(), 0)
        duration_min = math.ceil(elapsed_seconds / 60)

        failed = rng() < settings.scheduler_failure_rate
        if duration_min < row.walltime_min and not failed:
            continue

        status = JobStatus.FAILED if failed else JobStatus.COMPLETED
        if await _finalize_job(db, row=row, status=status, current=current, redis=redis):
            finished += 1

    return finished
