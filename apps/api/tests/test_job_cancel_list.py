"""Job cancellation (F4.4) and listing (F4.5)."""

from __future__ import annotations

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

OTHER_EMAIL = "other@hpc.local"
OTHER_PASSWORD = "other12345"


@pytest_asyncio.fixture
async def team(session_factory, seeded) -> int:
    """Group the standard user belongs to: 8 GPUs, max 2 nodes / 4 GPUs a job."""
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-cancel")
        group.members.append(user)
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=8, max_nodes_per_job=2, max_gpus_per_job=4))
        await db.commit()
        return group.id


@pytest_asyncio.fixture
async def other_user(session_factory, seeded) -> int:
    """A second standard user (not a member of ``team``)."""
    async with session_factory() as db:
        role = await db.scalar(select(Role).where(Role.name == "user"))
        user = User(
            email=OTHER_EMAIL,
            username="other",
            full_name="Other User",
            hashed_password=hash_password(OTHER_PASSWORD),
            roles=[role],
        )
        db.add(user)
        await db.commit()
        return user.id


def _payload(group_id: int, **overrides) -> dict:
    body = {
        "group_id": group_id,
        "name": "train-resnet",
        "num_nodes": 1,
        "num_gpus": 2,
        "walltime_min": 60,
        "params": {},
    }
    body.update(overrides)
    return body


async def _submit(client: AsyncClient, headers: dict[str, str], group_id: int, **overrides):
    response = await client.post(
        f"{API}/jobs", headers=headers, json=_payload(group_id, **overrides)
    )
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------- cancellation


async def test_owner_cancels_queued_job_and_quota_is_returned(
    client: AsyncClient, seeded, team
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team, num_gpus=3)

    response = await client.post(f"{API}/jobs/{job['id']}/cancel", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "canceled"
    assert body["ended_at"] is not None

    quotas = (await client.get(f"{API}/quotas/me", headers=headers)).json()
    assert quotas["items"][0]["gpu_used"] == 0


async def test_second_cancel_is_409(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team)

    assert (await client.post(f"{API}/jobs/{job['id']}/cancel", headers=headers)).status_code == 200
    response = await client.post(f"{API}/jobs/{job['id']}/cancel", headers=headers)

    assert response.status_code == 409, response.text
    quotas = (await client.get(f"{API}/quotas/me", headers=headers)).json()
    assert quotas["items"][0]["gpu_used"] == 0  # not released twice


async def test_completed_job_cannot_be_canceled(
    client: AsyncClient, seeded, team, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team)
    async with session_factory() as db:
        await db.execute(update(Job).where(Job.id == job["id"]).values(status="completed"))
        await db.commit()

    response = await client.post(f"{API}/jobs/{job['id']}/cancel", headers=headers)
    assert response.status_code == 409, response.text


async def test_admin_can_cancel_another_users_job(client: AsyncClient, seeded, team) -> None:
    user_headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, user_headers, team)

    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(f"{API}/jobs/{job['id']}/cancel", headers=admin_headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "canceled"


async def test_non_owner_cannot_cancel(client: AsyncClient, seeded, team, other_user) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team)

    other_headers = await auth_headers(client, OTHER_EMAIL, OTHER_PASSWORD)
    response = await client.post(f"{API}/jobs/{job['id']}/cancel", headers=other_headers)

    assert response.status_code == 403, response.text


async def test_cancel_unknown_job_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/jobs/9999/cancel", headers=headers)
    assert response.status_code == 404


async def test_anonymous_cannot_cancel(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    job = await _submit(client, headers, team)

    assert (await client.post(f"{API}/jobs/{job['id']}/cancel")).status_code == 401


# --------------------------------------------------------------------- listing


async def test_user_only_sees_own_jobs(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    await _submit(client, headers, team, name="mine-1")
    await _submit(client, headers, team, name="mine-2")
    async with session_factory() as db:
        db.add(
            Job(
                user_id=other_user,
                group_id=team,
                name="theirs",
                num_nodes=1,
                num_gpus=0,
                walltime_min=30,
            )
        )
        await db.commit()

    response = await client.get(f"{API}/jobs", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"mine-1", "mine-2"}


async def test_admin_sees_every_job(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    await _submit(client, headers, team)
    async with session_factory() as db:
        db.add(
            Job(
                user_id=other_user,
                group_id=team,
                name="theirs",
                num_nodes=1,
                num_gpus=0,
                walltime_min=30,
            )
        )
        await db.commit()

    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    body = (await client.get(f"{API}/jobs", headers=admin_headers)).json()
    assert body["total"] == 2


async def test_list_filters_by_status(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    await _submit(client, headers, team, name="stays-queued")
    canceled = await _submit(client, headers, team, name="gets-canceled")
    await client.post(f"{API}/jobs/{canceled['id']}/cancel", headers=headers)

    body = (await client.get(f"{API}/jobs?status=canceled", headers=headers)).json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "gets-canceled"


async def test_list_paginates_and_reports_total(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    for index in range(3):
        await _submit(client, headers, team, name=f"job-{index}")

    first = (await client.get(f"{API}/jobs?skip=0&limit=2", headers=headers)).json()
    second = (await client.get(f"{API}/jobs?skip=2&limit=2", headers=headers)).json()

    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )


async def test_list_filters_by_queued_time(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    await _submit(client, headers, team)

    future = (await client.get(f"{API}/jobs?since=2999-01-01T00:00:00", headers=headers)).json()
    past = (await client.get(f"{API}/jobs?until=2000-01-01T00:00:00", headers=headers)).json()
    covering = (
        await client.get(
            f"{API}/jobs?since=2000-01-01T00:00:00&until=2999-01-01T00:00:00", headers=headers
        )
    ).json()

    assert future["total"] == 0
    assert past["total"] == 0
    assert covering["total"] == 1


async def test_list_rejects_unknown_status(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.get(f"{API}/jobs?status=running-away", headers=headers)
    assert response.status_code == 422


async def test_anonymous_cannot_list(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/jobs")).status_code == 401
