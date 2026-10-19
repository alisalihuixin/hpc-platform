"""Scheduler finish pass (F4.3): walltime/failure -> terminal + usage_events."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import func, select

from app.core.config import settings
from app.models.group import Group
from app.models.job import Job
from app.models.node import Node
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services import scheduler_service
from tests.conftest import USER_EMAIL


@pytest_asyncio.fixture
async def owner_and_group(session_factory, seeded) -> tuple[int, int]:
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-finish")
        group.members.append(user)
        db.add(group)
        await db.flush()
        await db.commit()
        return user.id, group.id


async def _add_node(session_factory, *, gpu_total: int, gpu_used: int) -> int:
    async with session_factory() as db:
        node = Node(
            hostname="gpu-finish",
            cpu_cores=32,
            mem_gb=128,
            gpu_total=gpu_total,
            gpu_used=gpu_used,
        )
        db.add(node)
        await db.commit()
        return node.id


async def _add_running_job(
    session_factory,
    owner_and_group: tuple[int, int],
    *,
    node_id: int,
    num_gpus: int,
    started_at: datetime,
    walltime_min: int = 60,
) -> int:
    user_id, group_id = owner_and_group
    async with session_factory() as db:
        job = Job(
            user_id=user_id,
            group_id=group_id,
            name="running-job",
            num_nodes=1,
            num_gpus=num_gpus,
            walltime_min=walltime_min,
            params={},
            status="running",
            node_id=node_id,
            started_at=started_at,
        )
        db.add(job)
        await db.commit()
        return job.id


async def _finish(session_factory, *, now: datetime, rng=None) -> int:
    async with session_factory() as db:
        if rng is None:
            return await scheduler_service.finish_due_jobs(db, now=now)
        return await scheduler_service.finish_due_jobs(db, now=now, rng=rng)


async def _job(session_factory, job_id: int) -> Job:
    async with session_factory() as db:
        return await db.get(Job, job_id)


async def _node(session_factory, node_id: int) -> Node:
    async with session_factory() as db:
        return await db.get(Node, node_id)


async def _events(session_factory) -> list[UsageEvent]:
    async with session_factory() as db:
        return list((await db.scalars(select(UsageEvent))).all())


async def test_walltime_completion_writes_event_and_releases_node(
    session_factory, owner_and_group
) -> None:
    node_id = await _add_node(session_factory, gpu_total=4, gpu_used=2)
    started = datetime(2026, 1, 1, 10, 0, 0)
    job_id = await _add_running_job(
        session_factory,
        owner_and_group,
        node_id=node_id,
        num_gpus=2,
        started_at=started,
        walltime_min=60,
    )

    # 61 minutes later: walltime exhausted, duration rounds up to 61.
    assert await _finish(session_factory, now=started + timedelta(minutes=61)) == 1

    job = await _job(session_factory, job_id)
    assert job.status == "completed"
    assert job.ended_at is not None
    assert (await _node(session_factory, node_id)).gpu_used == 0

    events = await _events(session_factory)
    assert len(events) == 1
    event = events[0]
    assert event.job_id == job_id
    assert event.gpus == 2
    assert event.duration_min == 61
    assert event.gpu_hours == Decimal("2.0333")  # 2 GPUs x 61 min / 60
    assert event.reversed is False
    # Node has no gpu_model: the default rate is snapshotted.
    assert event.rate_snapshot["rate_key"] == "default"
    assert event.rate_snapshot["gpu_hour"] == "10.0000"
    assert event.rate_snapshot["currency"] == "CNY"


async def test_job_is_not_finished_before_walltime(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, gpu_total=4, gpu_used=2)
    started = datetime(2026, 1, 1, 10, 0, 0)
    job_id = await _add_running_job(
        session_factory, owner_and_group, node_id=node_id, num_gpus=2, started_at=started
    )

    assert await _finish(session_factory, now=started + timedelta(minutes=10)) == 0

    assert (await _job(session_factory, job_id)).status == "running"
    assert (await _node(session_factory, node_id)).gpu_used == 2
    assert await _events(session_factory) == []


async def test_exact_walltime_finishes(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, gpu_total=4, gpu_used=1)
    started = datetime(2026, 1, 1, 10, 0, 0)
    job_id = await _add_running_job(
        session_factory, owner_and_group, node_id=node_id, num_gpus=1, started_at=started
    )

    assert await _finish(session_factory, now=started + timedelta(minutes=60)) == 1
    assert (await _job(session_factory, job_id)).status == "completed"


async def test_failure_injection_marks_job_failed(
    session_factory, owner_and_group, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "scheduler_failure_rate", 1.0)
    node_id = await _add_node(session_factory, gpu_total=4, gpu_used=2)
    started = datetime(2026, 1, 1, 10, 0, 0)
    job_id = await _add_running_job(
        session_factory, owner_and_group, node_id=node_id, num_gpus=2, started_at=started
    )

    assert await _finish(session_factory, now=started + timedelta(minutes=5), rng=lambda: 0.0) == 1

    job = await _job(session_factory, job_id)
    assert job.status == "failed"
    assert (await _node(session_factory, node_id)).gpu_used == 0

    events = await _events(session_factory)
    assert len(events) == 1
    assert events[0].duration_min == 5
    assert events[0].gpu_hours == Decimal("0.1667")  # 2 x 5 / 60


async def test_finished_job_is_processed_once(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, gpu_total=4, gpu_used=2)
    started = datetime(2026, 1, 1, 10, 0, 0)
    await _add_running_job(
        session_factory, owner_and_group, node_id=node_id, num_gpus=2, started_at=started
    )
    now = started + timedelta(minutes=61)

    assert await _finish(session_factory, now=now) == 1
    assert await _finish(session_factory, now=now) == 0

    assert len(await _events(session_factory)) == 1
    assert (await _node(session_factory, node_id)).gpu_used == 0


async def test_queued_and_terminal_jobs_are_ignored(session_factory, owner_and_group) -> None:
    user_id, group_id = owner_and_group
    async with session_factory() as db:
        db.add_all(
            [
                Job(
                    user_id=user_id,
                    group_id=group_id,
                    name="still-queued",
                    num_nodes=1,
                    num_gpus=1,
                    walltime_min=60,
                    params={},
                ),
                Job(
                    user_id=user_id,
                    group_id=group_id,
                    name="already-canceled",
                    num_nodes=1,
                    num_gpus=1,
                    walltime_min=60,
                    params={},
                    status="canceled",
                    ended_at=datetime(2026, 1, 1, 9, 0, 0),
                ),
            ]
        )
        await db.commit()

    assert await _finish(session_factory, now=datetime(2026, 1, 2, 0, 0, 0)) == 0
    assert await _events(session_factory) == []


async def test_event_count_matches_finished_jobs(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory, gpu_total=8, gpu_used=4)
    started = datetime(2026, 1, 1, 10, 0, 0)
    for _ in range(2):
        await _add_running_job(
            session_factory,
            owner_and_group,
            node_id=node_id,
            num_gpus=2,
            started_at=started,
            walltime_min=30,
        )

    assert await _finish(session_factory, now=started + timedelta(minutes=30)) == 2

    async with session_factory() as db:
        total = await db.scalar(select(func.count()).select_from(UsageEvent))
    assert total == 2
    assert (await _node(session_factory, node_id)).gpu_used == 0
