"""Job model + state machine (F4.1)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.group import Group
from app.models.job import ALLOWED_TRANSITIONS, TERMINAL_STATUSES, Job, JobStatus
from app.models.user import User
from tests.conftest import USER_EMAIL


@pytest_asyncio.fixture
async def job_env(session_factory, seeded) -> tuple[int, int]:
    """(user_id, group_id) for a user that belongs to one group."""
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-jobs")
        group.members.append(user)
        db.add(group)
        await db.commit()
        return user.id, group.id


def _job(**overrides) -> Job:
    base = {"name": "train-a", "num_nodes": 1, "num_gpus": 4, "walltime_min": 60}
    return Job(**{**base, **overrides})


async def test_job_defaults_to_queued(session_factory, job_env) -> None:
    user_id, group_id = job_env
    async with session_factory() as db:
        job = _job(user_id=user_id, group_id=group_id)
        db.add(job)
        await db.commit()
        await db.refresh(job)

        assert job.status == JobStatus.QUEUED.value
        assert job.params == {}
        assert job.node_id is None
        assert job.started_at is None
        assert job.ended_at is None
        assert job.queued_at is not None
        assert job.is_terminal is False
        assert job.can_be_canceled is True


async def test_unknown_status_is_rejected(session_factory, job_env) -> None:
    user_id, group_id = job_env
    async with session_factory() as db:
        db.add(_job(user_id=user_id, group_id=group_id, status="bogus"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_num_nodes_must_be_positive(session_factory, job_env) -> None:
    user_id, group_id = job_env
    async with session_factory() as db:
        db.add(_job(user_id=user_id, group_id=group_id, num_nodes=0))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_walltime_must_be_positive(session_factory, job_env) -> None:
    user_id, group_id = job_env
    async with session_factory() as db:
        db.add(_job(user_id=user_id, group_id=group_id, walltime_min=0))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_terminal_states_are_not_cancelable(session_factory, job_env) -> None:
    user_id, group_id = job_env
    async with session_factory() as db:
        job = _job(user_id=user_id, group_id=group_id, status=JobStatus.CANCELED.value)
        db.add(job)
        await db.commit()
        await db.refresh(job)

        assert job.is_terminal is True
        assert job.can_be_canceled is False


def test_transition_map_is_restrictive() -> None:
    assert ALLOWED_TRANSITIONS[JobStatus.QUEUED] == frozenset(
        {JobStatus.RUNNING, JobStatus.CANCELED}
    )
    assert ALLOWED_TRANSITIONS[JobStatus.RUNNING] == frozenset(
        {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}
    )
    # Terminal states go nowhere.
    assert ALLOWED_TRANSITIONS[JobStatus.COMPLETED] == frozenset()
    assert ALLOWED_TRANSITIONS[JobStatus.FAILED] == frozenset()
    assert ALLOWED_TRANSITIONS[JobStatus.CANCELED] == frozenset()
    assert (
        frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}) == TERMINAL_STATUSES
    )
