"""Simulated scheduler (F4.3): FIFO placement onto online nodes."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy import select

from app.models.group import Group
from app.models.job import Job
from app.models.node import Node
from app.models.user import User
from app.services import scheduler_service
from tests.conftest import USER_EMAIL


@pytest_asyncio.fixture
async def owner_and_group(session_factory, seeded) -> tuple[int, int]:
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-scheduler")
        db.add(group)
        await db.flush()
        await db.commit()
        return user.id, group.id


async def _add_node(
    session_factory,
    hostname: str,
    gpu_total: int,
    *,
    gpu_used: int = 0,
    status: str = "online",
) -> int:
    async with session_factory() as db:
        node = Node(
            hostname=hostname,
            cpu_cores=32,
            mem_gb=128,
            gpu_total=gpu_total,
            gpu_used=gpu_used,
            status=status,
        )
        db.add(node)
        await db.commit()
        return node.id


async def _add_job(
    session_factory,
    owner_and_group: tuple[int, int],
    *,
    name: str,
    num_gpus: int,
    num_nodes: int = 1,
    queued_at: datetime | None = None,
) -> int:
    user_id, group_id = owner_and_group
    async with session_factory() as db:
        job = Job(
            user_id=user_id,
            group_id=group_id,
            name=name,
            num_nodes=num_nodes,
            num_gpus=num_gpus,
            walltime_min=60,
            params={},
        )
        if queued_at is not None:
            job.queued_at = queued_at
        db.add(job)
        await db.commit()
        return job.id


async def _run(session_factory) -> int:
    async with session_factory() as db:
        return await scheduler_service.schedule_once(db)


async def _job(session_factory, job_id: int) -> Job:
    async with session_factory() as db:
        return await db.get(Job, job_id)


async def _node(session_factory, node_id: int) -> Node:
    async with session_factory() as db:
        return await db.get(Node, node_id)


async def test_queued_job_starts_on_an_available_node(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, "gpu-01", gpu_total=4)
    job_id = await _add_job(session_factory, owner_and_group, name="job-1", num_gpus=2)

    assert await _run(session_factory) == 1

    job = await _job(session_factory, job_id)
    assert job.status == "running"
    assert job.node_id == node_id
    assert job.started_at is not None
    assert (await _node(session_factory, node_id)).gpu_used == 2


async def test_job_waits_when_no_node_has_capacity(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, "gpu-small", gpu_total=2)
    job_id = await _add_job(session_factory, owner_and_group, name="job-big", num_gpus=4)

    assert await _run(session_factory) == 0

    job = await _job(session_factory, job_id)
    assert job.status == "queued"
    assert job.node_id is None
    assert job.started_at is None
    assert (await _node(session_factory, node_id)).gpu_used == 0


async def test_offline_and_draining_nodes_are_skipped(session_factory, owner_and_group) -> None:
    await _add_node(session_factory, "gpu-off", gpu_total=8, status="offline")
    await _add_node(session_factory, "gpu-drain", gpu_total=8, status="draining")
    job_id = await _add_job(session_factory, owner_and_group, name="job-1", num_gpus=2)

    assert await _run(session_factory) == 0
    assert (await _job(session_factory, job_id)).status == "queued"


async def test_fifo_order(session_factory, owner_and_group) -> None:
    await _add_node(session_factory, "gpu-01", gpu_total=4)
    base = datetime(2026, 1, 1, 10, 0, 0)
    first = await _add_job(
        session_factory, owner_and_group, name="first", num_gpus=3, queued_at=base
    )
    second = await _add_job(
        session_factory,
        owner_and_group,
        name="second",
        num_gpus=3,
        queued_at=base + timedelta(seconds=1),
    )

    assert await _run(session_factory) == 1

    assert (await _job(session_factory, first)).status == "running"
    assert (await _job(session_factory, second)).status == "queued"


async def test_least_loaded_node_is_chosen(session_factory, owner_and_group) -> None:
    await _add_node(session_factory, "gpu-busy", gpu_total=8, gpu_used=2)
    idle_id = await _add_node(session_factory, "gpu-idle", gpu_total=8, gpu_used=0)
    job_id = await _add_job(session_factory, owner_and_group, name="job-1", num_gpus=1)

    assert await _run(session_factory) == 1

    job = await _job(session_factory, job_id)
    assert job.node_id == idle_id
    assert (await _node(session_factory, idle_id)).gpu_used == 1


async def test_node_is_filled_until_the_queue_waits(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, "gpu-01", gpu_total=4)
    job_ids = [
        await _add_job(session_factory, owner_and_group, name=f"job-{index}", num_gpus=2)
        for index in range(3)
    ]

    assert await _run(session_factory) == 2

    statuses = [(await _job(session_factory, job_id)).status for job_id in job_ids]
    assert statuses.count("running") == 2
    assert statuses.count("queued") == 1
    assert (await _node(session_factory, node_id)).gpu_used == 4


async def test_second_pass_does_not_double_schedule(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, "gpu-01", gpu_total=4)
    job_id = await _add_job(session_factory, owner_and_group, name="job-1", num_gpus=2)

    assert await _run(session_factory) == 1
    started_at = (await _job(session_factory, job_id)).started_at

    assert await _run(session_factory) == 0

    job = await _job(session_factory, job_id)
    assert job.started_at == started_at
    assert (await _node(session_factory, node_id)).gpu_used == 2


async def test_cpu_only_job_is_placed_without_gpu_usage(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, "cpu-01", gpu_total=0)
    job_id = await _add_job(session_factory, owner_and_group, name="cpu-job", num_gpus=0)

    assert await _run(session_factory) == 1

    job = await _job(session_factory, job_id)
    assert job.status == "running"
    assert job.node_id == node_id
    assert (await _node(session_factory, node_id)).gpu_used == 0
