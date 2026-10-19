"""Job detail (F4.6): resources, timing, consumed GPU-minutes and mock log."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.security import hash_password
from app.models.group import Group
from app.models.job import Job
from app.models.quota import Quota
from app.models.role import Role
from app.models.user import User
from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)

OTHER_EMAIL = "other-detail@hpc.local"
OTHER_PASSWORD = "other12345"


@pytest_asyncio.fixture
async def team(session_factory, seeded) -> int:
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-detail")
        group.members.append(user)
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=8, max_nodes_per_job=2, max_gpus_per_job=4))
        await db.commit()
        return group.id


@pytest_asyncio.fixture
async def other_user(session_factory, seeded) -> int:
    async with session_factory() as db:
        role = await db.scalar(select(Role).where(Role.name == "user"))
        user = User(
            email=OTHER_EMAIL,
            username="other-detail",
            hashed_password=hash_password(OTHER_PASSWORD),
            roles=[role],
        )
        db.add(user)
        await db.commit()
        return user.id


async def _submit(client: AsyncClient, headers: dict[str, str], group_id: int, **overrides) -> dict:
    payload = {
        "group_id": group_id,
        "name": "train-resnet",
        "num_nodes": 1,
        "num_gpus": 2,
        "walltime_min": 60,
        "params": {"image": "pytorch:2.4"},
    }
    payload.update(overrides)
    response = await client.post(f"{API}/jobs", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_anonymous_cannot_read_detail(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team)

    assert (await client.get(f"{API}/jobs/{job['id']}")).status_code == 401


async def test_owner_reads_queued_job_detail(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team)

    response = await client.get(f"{API}/jobs/{job['id']}", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == job["id"]
    assert body["status"] == "queued"
    assert body["num_nodes"] == 1
    assert body["num_gpus"] == 2
    assert body["walltime_min"] == 60
    assert body["params"] == {"image": "pytorch:2.4"}
    assert body["started_at"] is None and body["ended_at"] is None
    # Nothing has run yet.
    assert body["gpu_minutes"] == 0
    assert "[queued]" in body["log"]


async def test_completed_job_reports_consumed_gpu_minutes(
    client: AsyncClient, seeded, team, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team, num_gpus=2)

    started = datetime(2026, 1, 1, 10, 0, 0)
    async with session_factory() as db:
        await db.execute(
            update(Job)
            .where(Job.id == job["id"])
            .values(
                status="completed", started_at=started, ended_at=started + timedelta(minutes=30)
            )
        )
        await db.commit()

    body = (await client.get(f"{API}/jobs/{job['id']}", headers=headers)).json()

    assert body["status"] == "completed"
    assert body["gpu_minutes"] == 2 * 30  # 2 GPUs x 30 minutes
    assert "[queued]" in body["log"]
    assert "[running]" in body["log"]
    assert "[completed]" in body["log"]


async def test_running_job_counts_minutes_so_far(
    client: AsyncClient, seeded, team, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team, num_gpus=1)

    started = datetime(2026, 1, 1, 10, 0, 0)
    async with session_factory() as db:
        await db.execute(
            update(Job).where(Job.id == job["id"]).values(status="running", started_at=started)
        )
        await db.commit()

    body = (await client.get(f"{API}/jobs/{job['id']}", headers=headers)).json()

    assert body["status"] == "running"
    assert body["ended_at"] is None
    assert body["gpu_minutes"] > 0
    assert "[running]" in body["log"]


async def test_admin_reads_another_users_job(client: AsyncClient, seeded, team) -> None:
    user_headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, user_headers, team)

    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.get(f"{API}/jobs/{job['id']}", headers=admin_headers)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == job["id"]


async def test_non_owner_cannot_read_detail(client: AsyncClient, seeded, team, other_user) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team)

    other_headers = await auth_headers(client, OTHER_EMAIL, OTHER_PASSWORD)
    response = await client.get(f"{API}/jobs/{job['id']}", headers=other_headers)

    assert response.status_code == 403, response.text


async def test_unknown_job_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (await client.get(f"{API}/jobs/9999", headers=headers)).status_code == 404
