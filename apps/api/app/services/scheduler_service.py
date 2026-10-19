"""Simulated scheduler (F4.3).

One cycle has two passes:

**Start** - read ``queued`` jobs in FIFO order, pick the least-loaded ``online``
node with enough free GPUs, claim its GPUs with a conditional ``UPDATE`` (same
anti-oversell pattern as quotas), then flip the job to ``running`` with
``started_at``.

**Finish** - read ``running`` jobs, and when walltime is exhausted (or an
injected random failure fires) move them to ``completed`` / ``failed``, release
the node's GPUs, and append one ``usage_events`` row for billing (F5.1).

Quota was already reserved at submission time (F4.2), so scheduling never
touches ``quotas``; it only accounts physical node capacity.  Each job is its
own transaction so one job can never block the rest of the queue.

MVP simplification: a job is placed on a single node even when ``num_nodes`` >
1, because ``jobs`` carries a single ``node_id``.  Completion can thus always
release exactly the node it took.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.job import Job, JobStatus
from app.models.node import Node, NodeStatus
from app.models.usage_event import UsageEvent
from app.services import billing_service


async def _claim_node(db: AsyncSession, num_gpus: int) -> int | None:
    """Reserve ``num_gpus`` on one online node; ``None`` when none fits."""
    candidates = (
        await db.scalars(
            select(Node)
            .where(
                Node.status == NodeStatus.ONLINE.value,
                Node.gpu_total - Node.gpu_used >= num_gpus,
            )
            .order_by(Node.gpu_used.asc(), Node.hostname.asc())
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
            )
            .values(gpu_used=Node.gpu_used + num_gpus)
        )
        if result.rowcount == 1:
            return node.id
    return None


async def schedule_once(db: AsyncSession) -> int:
    """Run a single scheduling pass; returns how many jobs were started."""
    queued = await db.execute(
        select(Job.id, Job.num_gpus)
        .where(Job.status == JobStatus.QUEUED.value)
        .order_by(Job.queued_at.asc(), Job.id.asc())
    )

    started = 0
    for job_id, num_gpus in queued.all():
        node_id = await _claim_node(db, num_gpus)
        if node_id is None:
            # Nothing changed; close the transaction so the next job starts
            # from a clean snapshot.
            await db.rollback()
            continue

        transition = await db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.QUEUED.value)
            .values(
                status=JobStatus.RUNNING.value,
                node_id=node_id,
                started_at=func.now(),
            )
        )
        if transition.rowcount != 1:
            # Another scheduler instance got there first: give the GPUs back.
            await db.rollback()
            continue

        await db.commit()
        started += 1

    return started


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; normalise before arithmetic."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def finish_due_jobs(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    rng: Callable[[], float] = random.random,
) -> int:
    """Finish running jobs that hit walltime (or an injected failure).

    For each finished job: flip the status, release the node's GPUs and append
    one ``usage_events`` row.  ``rng`` is injectable so tests stay deterministic;
    the failure probability comes from ``settings.scheduler_failure_rate``
    (0 by default).  Returns how many jobs were finished.
    """
    current = _as_utc(now or datetime.now(UTC))

    running = await db.execute(
        select(
            Job.id,
            Job.user_id,
            Job.group_id,
            Job.node_id,
            Job.num_gpus,
            Job.started_at,
            Job.walltime_min,
            Node.gpu_model,
        )
        .outerjoin(Node, Node.id == Job.node_id)
        .where(Job.status == JobStatus.RUNNING.value)
    )

    finished = 0
    for row in running.all():
        if row.started_at is None:  # pragma: no cover - running jobs always have one
            continue
        elapsed_seconds = max((current - _as_utc(row.started_at)).total_seconds(), 0)
        duration_min = math.ceil(elapsed_seconds / 60)

        failed = rng() < settings.scheduler_failure_rate
        if duration_min < row.walltime_min and not failed:
            continue

        status = JobStatus.FAILED if failed else JobStatus.COMPLETED
        transition = await db.execute(
            update(Job)
            .where(Job.id == row.id, Job.status == JobStatus.RUNNING.value)
            .values(status=status.value, ended_at=func.now())
        )
        if transition.rowcount != 1:
            # Someone else finished it first; undo nothing and move on.
            await db.rollback()
            continue

        if row.node_id is not None and row.num_gpus > 0:
            await db.execute(
                update(Node)
                .where(Node.id == row.node_id, Node.gpu_used >= row.num_gpus)
                .values(gpu_used=Node.gpu_used - row.num_gpus)
            )

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
        finished += 1

    return finished
