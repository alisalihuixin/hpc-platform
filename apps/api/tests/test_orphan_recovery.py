"""Crash recovery: expired job leases are failed and their slots reclaimed."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy import func, select

from app.core.config import settings
from app.core.events import JOB_EVENTS_CHANNEL
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
        group = Group(name="team-recovery")
        group.members.append(user)
        db.add(group)
        await db.flush()
        await db.commit()
        return user.id, group.id


async def _add_node(
    session_factory,
    *,
    gpu_total: int = 4,
    cpu_cores: int = 16,
    mem_gb: int = 32,
    gpu_used: int = 2,
    cpu_used: int = 4,
    mem_used: int = 8,
) -> int:
    async with session_factory() as db:
        node = Node(
            hostname="gpu-recovery",
            cpu_cores=cpu_cores,
            mem_gb=mem_gb,
            gpu_total=gpu_total,
            gpu_used=gpu_used,
            cpu_used=cpu_used,
            mem_used=mem_used,
        )
        db.add(node)
        await db.commit()
        return node.id


async def _add_running_job(
    session_factory,
    owner_and_group: tuple[int, int],
    *,
    node_id: int,
    started_at: datetime,
    lease_expires_at: datetime | None,
    num_gpus: int = 2,
    num_cpus: int = 4,
    mem_gb: int = 8,
) -> int:
    user_id, group_id = owner_and_group
    async with session_factory() as db:
        job = Job(
            user_id=user_id,
            group_id=group_id,
            name="orphan",
            num_nodes=1,
            num_gpus=num_gpus,
            num_cpus=num_cpus,
            mem_gb=mem_gb,
            walltime_min=60,
            params={},
            status="running",
            node_id=node_id,
            started_at=started_at,
            lease_expires_at=lease_expires_at,
        )
        db.add(job)
        await db.commit()
        return job.id


async def _read_message(pubsub, timeout: float = 2.0) -> dict:
    async def _loop() -> dict:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
            if message is not None:
                return message
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _job(session_factory, job_id: int) -> Job:
    async with session_factory() as db:
        return await db.get(Job, job_id)


async def _node(session_factory, node_id: int) -> Node:
    async with session_factory() as db:
        return await db.get(Node, node_id)


async def test_expired_lease_is_failed_and_slots_recovered(
    session_factory, owner_and_group, redis_client
) -> None:
    node_id = await _add_node(session_factory)
    started = datetime(2026, 1, 1, 10, 0, 0)
    job_id = await _add_running_job(
        session_factory,
        owner_and_group,
        node_id=node_id,
        started_at=started,
        lease_expires_at=started + timedelta(minutes=5),
    )

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(JOB_EVENTS_CHANNEL)
    try:
        async with session_factory() as db:
            recovered = await scheduler_service.recover_orphaned_jobs(
                db, now=started + timedelta(minutes=10), redis=redis_client
            )
        payload = json.loads((await _read_message(pubsub))["data"])
    finally:
        await pubsub.aclose()

    assert recovered == 1
    job = await _job(session_factory, job_id)
    assert job.status == "failed"
    assert job.ended_at is not None

    node = await _node(session_factory, node_id)
    assert (node.gpu_used, node.cpu_used, node.mem_used) == (0, 0, 0)

    async with session_factory() as db:
        event = await db.scalar(select(UsageEvent).where(UsageEvent.job_id == job_id))
    assert event is not None
    assert event.gpus == 2
    assert event.duration_min == 10  # 10 minutes of work still gets billed

    assert payload["job_id"] == job_id
    assert payload["status"] == "failed"


async def test_fresh_lease_is_not_recovered_and_gets_renewed(
    session_factory, owner_and_group
) -> None:
    node_id = await _add_node(session_factory)
    started = datetime(2026, 1, 1, 10, 0, 0)
    now = started + timedelta(minutes=10)
    job_id = await _add_running_job(
        session_factory,
        owner_and_group,
        node_id=node_id,
        started_at=started,
        lease_expires_at=now + timedelta(minutes=5),
    )

    async with session_factory() as db:
        assert await scheduler_service.recover_orphaned_jobs(db, now=now) == 0
        assert await scheduler_service.renew_leases(db, now=now) == 1

    job = await _job(session_factory, job_id)
    assert job.status == "running"
    assert job.lease_expires_at is not None
    assert job.lease_expires_at > now.replace(tzinfo=None)


async def test_recovery_is_idempotent(session_factory, owner_and_group) -> None:
    node_id = await _add_node(session_factory)
    started = datetime(2026, 1, 1, 10, 0, 0)
    await _add_running_job(
        session_factory,
        owner_and_group,
        node_id=node_id,
        started_at=started,
        lease_expires_at=started + timedelta(minutes=5),
    )
    now = started + timedelta(minutes=10)

    async with session_factory() as db:
        assert await scheduler_service.recover_orphaned_jobs(db, now=now) == 1
        assert await scheduler_service.recover_orphaned_jobs(db, now=now) == 0

    async with session_factory() as db:
        total = await db.scalar(select(func.count()).select_from(UsageEvent))
    assert total == 1
    node = await _node(session_factory, node_id)
    assert (node.gpu_used, node.cpu_used, node.mem_used) == (0, 0, 0)


async def test_legacy_running_job_without_lease_is_adopted(
    session_factory, owner_and_group
) -> None:
    node_id = await _add_node(session_factory)
    started = datetime(2026, 1, 1, 10, 0, 0)
    job_id = await _add_running_job(
        session_factory,
        owner_and_group,
        node_id=node_id,
        started_at=started,
        lease_expires_at=None,
    )
    now = started + timedelta(minutes=10)

    async with session_factory() as db:
        assert await scheduler_service.recover_orphaned_jobs(db, now=now) == 0
        assert await scheduler_service.renew_leases(db, now=now) == 1

    job = await _job(session_factory, job_id)
    assert job.status == "running"
    assert job.lease_expires_at is not None


def test_lease_default_is_positive() -> None:
    assert settings.scheduler_lease_seconds >= 5
