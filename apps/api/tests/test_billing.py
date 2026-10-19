"""Billing rules (F5.2) and append-only reversal (F5.1)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import settings
from app.models.group import Group
from app.models.job import Job
from app.models.node import Node
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services import billing_service, scheduler_service
from tests.conftest import USER_EMAIL


@pytest_asyncio.fixture
async def owner_and_group(session_factory, seeded) -> tuple[int, int]:
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-billing")
        group.members.append(user)
        db.add(group)
        await db.flush()
        await db.commit()
        return user.id, group.id


async def _add_finished_job(
    session_factory, owner_and_group: tuple[int, int], *, status: str = "completed"
) -> int:
    user_id, group_id = owner_and_group
    async with session_factory() as db:
        job = Job(
            user_id=user_id,
            group_id=group_id,
            name="billed-job",
            num_nodes=1,
            num_gpus=2,
            walltime_min=60,
            params={},
            status=status,
            ended_at=datetime(2026, 1, 1, 10, 30, 0),
        )
        db.add(job)
        await db.commit()
        return job.id


async def _add_event(
    session_factory,
    owner_and_group: tuple[int, int],
    job_id: int,
    *,
    gpu_hours: str = "1.0000",
    rate_snapshot: dict | None = None,
) -> int:
    user_id, group_id = owner_and_group
    started = datetime(2026, 1, 1, 10, 0, 0)
    async with session_factory() as db:
        event = UsageEvent(
            job_id=job_id,
            user_id=user_id,
            group_id=group_id,
            gpus=2,
            started_at=started,
            ended_at=started + timedelta(minutes=30),
            duration_min=30,
            gpu_hours=Decimal(gpu_hours),
            rate_snapshot=rate_snapshot or {"gpu_hour": "10.0000"},
            reversed=False,
        )
        db.add(event)
        await db.commit()
        return event.id


async def _events(session_factory) -> list[UsageEvent]:
    async with session_factory() as db:
        return list((await db.scalars(select(UsageEvent).order_by(UsageEvent.id))).all())


# --------------------------------------------------------------- billing rules


@pytest.mark.parametrize(
    ("gpus", "minutes", "expected"),
    [
        (1, 1, Decimal("0.0167")),  # 1 GPU x 1 min
        (2, 61, Decimal("2.0333")),  # 2 GPUs x 61 min
        (4, 30, Decimal("2.0000")),  # 4 GPUs x 30 min
    ],
)
def test_gpu_hours_hand_calculated_cases(gpus: int, minutes: int, expected: Decimal) -> None:
    assert billing_service.gpu_hours(gpus, minutes) == expected


def test_rate_snapshot_for_known_and_unknown_models(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gpu_hour_rates", {"default": 10.0, "A100-80G": 30.0})
    monkeypatch.setattr(settings, "billing_currency", "CNY")

    known = billing_service.rate_snapshot("A100-80G")
    assert known == {
        "gpu_model": "A100-80G",
        "rate_key": "A100-80G",
        "gpu_hour": "30.0000",
        "currency": "CNY",
    }

    unknown = billing_service.rate_snapshot("MI300")
    assert unknown["rate_key"] == "default"
    assert unknown["gpu_hour"] == "10.0000"

    missing = billing_service.rate_snapshot(None)
    assert missing["gpu_model"] == "default"


async def test_finish_snapshots_the_node_rate(session_factory, owner_and_group) -> None:
    user_id, group_id = owner_and_group
    started = datetime(2026, 1, 1, 10, 0, 0)
    async with session_factory() as db:
        node = Node(
            hostname="gpu-a100",
            cpu_cores=64,
            mem_gb=512,
            gpu_model="A100-80G",
            gpu_total=4,
            gpu_used=2,
        )
        db.add(node)
        await db.flush()
        job = Job(
            user_id=user_id,
            group_id=group_id,
            name="a100-job",
            num_nodes=1,
            num_gpus=2,
            walltime_min=60,
            params={},
            status="running",
            node_id=node.id,
            started_at=started,
        )
        db.add(job)
        await db.commit()
        node_id = node.id

    async with session_factory() as db:
        assert await scheduler_service.finish_due_jobs(db, now=started + timedelta(minutes=61)) == 1

    events = await _events(session_factory)
    assert len(events) == 1
    assert events[0].rate_snapshot["gpu_model"] == "A100-80G"
    assert events[0].rate_snapshot["gpu_hour"] == "30.0000"
    assert events[0].gpu_hours == Decimal("2.0333")

    async with session_factory() as db:
        assert (await db.get(Node, node_id)).gpu_used == 0


# -------------------------------------------------------------- reversal (F5.1)


async def test_reversal_appends_compensating_row_and_nets_to_zero(
    session_factory, owner_and_group
) -> None:
    job_id = await _add_finished_job(session_factory, owner_and_group)
    original_id = await _add_event(session_factory, owner_and_group, job_id)

    async with session_factory() as db:
        reversals = await billing_service.reverse_job_usage(db, job_id, reason="billing correction")

    assert len(reversals) == 1
    assert reversals[0].reversed is True
    assert reversals[0].gpu_hours == Decimal("1.0000")
    assert reversals[0].rate_snapshot["reversal_reason"] == "billing correction"

    events = await _events(session_factory)
    assert len(events) == 2  # append-only: the original stays

    original = next(event for event in events if event.id == original_id)
    assert original.reversed is False
    assert "reversal_reason" not in original.rate_snapshot

    assert billing_service.net_gpu_hours(events) == Decimal("0.0000")


async def test_reversal_is_idempotent(session_factory, owner_and_group) -> None:
    job_id = await _add_finished_job(session_factory, owner_and_group)
    await _add_event(session_factory, owner_and_group, job_id)

    async with session_factory() as db:
        assert len(await billing_service.reverse_job_usage(db, job_id)) == 1
        assert await billing_service.reverse_job_usage(db, job_id) == []

    assert len(await _events(session_factory)) == 2


async def test_reversal_without_usage_is_a_noop(session_factory, owner_and_group) -> None:
    job_id = await _add_finished_job(session_factory, owner_and_group)

    async with session_factory() as db:
        assert await billing_service.reverse_job_usage(db, job_id) == []

    assert await _events(session_factory) == []
