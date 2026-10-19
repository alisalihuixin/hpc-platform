"""Simulated scheduler (F4.3).

One cycle:

1. read ``queued`` jobs in FIFO order (``queued_at``, then ``id``);
2. pick the least-loaded ``online`` node with enough free GPUs;
3. claim the node's GPUs with a conditional ``UPDATE`` (same anti-oversell
   pattern as quotas - the scheduler may later run as multiple instances);
4. flip the job to ``running`` with ``started_at``.

Quota was already reserved at submission time (F4.2), so scheduling never
touches ``quotas``; it only accounts physical node capacity.  Each job is its
own transaction so one unschedulable job never blocks the rest of the queue.

MVP simplification: a job is placed on a single node even when ``num_nodes`` >
1, because ``jobs`` carries a single ``node_id``.  Completion (D22) can thus
always release exactly the node it took.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job, JobStatus
from app.models.node import Node, NodeStatus


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
