"""Job submission (F4.2): validation -> quota reservation -> enqueue."""

from __future__ import annotations

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models.group import Group
from app.models.job import Job
from app.models.quota import Quota
from app.models.user import User
from tests.conftest import API, USER_EMAIL, USER_PASSWORD, auth_headers

GROUP_GPU_LIMIT = 8
MAX_NODES_PER_JOB = 2
MAX_GPUS_PER_JOB = 4


async def _job_count(session_factory) -> int:
    async with session_factory() as db:
        return await db.scalar(select(func.count()).select_from(Job)) or 0


@pytest_asyncio.fixture
async def team(session_factory, seeded) -> int:
    """Group the standard user belongs to: 8 GPUs, max 2 nodes / 4 GPUs a job."""
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-jobs")
        group.members.append(user)
        db.add(group)
        await db.flush()
        db.add(
            Quota(
                group_id=group.id,
                gpu_limit=GROUP_GPU_LIMIT,
                max_nodes_per_job=MAX_NODES_PER_JOB,
                max_gpus_per_job=MAX_GPUS_PER_JOB,
            )
        )
        await db.commit()
        return group.id


@pytest_asyncio.fixture
async def foreign_group(session_factory, seeded) -> int:
    """A group the standard user does *not* belong to."""
    async with session_factory() as db:
        group = Group(name="team-foreign")
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=10, max_nodes_per_job=2, max_gpus_per_job=4))
        await db.commit()
        return group.id


def _payload(group_id: int, **overrides) -> dict:
    body = {
        "group_id": group_id,
        "name": "train-resnet",
        "num_nodes": 1,
        "num_gpus": 2,
        "walltime_min": 60,
        "params": {"image": "pytorch:2.4"},
    }
    body.update(overrides)
    return body


async def test_anonymous_cannot_submit(client: AsyncClient, seeded, team) -> None:
    response = await client.post(f"{API}/jobs", json=_payload(team))
    assert response.status_code == 401


async def test_submit_returns_queued_job_and_reserves_quota(
    client: AsyncClient, seeded, team
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/jobs", headers=headers, json=_payload(team, num_gpus=3))

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["group_id"] == team
    assert body["num_gpus"] == 3
    assert body["params"] == {"image": "pytorch:2.4"}
    assert body["node_id"] is None
    assert body["started_at"] is None and body["ended_at"] is None

    quotas = (await client.get(f"{API}/quotas/me", headers=headers)).json()
    assert quotas["items"][0]["gpu_used"] == 3
    assert quotas["items"][0]["gpu_available"] == GROUP_GPU_LIMIT - 3


async def test_cpu_only_job_does_not_touch_the_pool(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/jobs", headers=headers, json=_payload(team, num_gpus=0))

    assert response.status_code == 201, response.text
    quotas = (await client.get(f"{API}/quotas/me", headers=headers)).json()
    assert quotas["items"][0]["gpu_used"] == 0


async def test_gpus_over_group_ceiling_is_422_with_field(
    client: AsyncClient, seeded, team, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(
        f"{API}/jobs", headers=headers, json=_payload(team, num_gpus=MAX_GPUS_PER_JOB + 1)
    )

    assert response.status_code == 422, response.text
    fields = [error["loc"] for error in response.json()["detail"]]
    assert ["body", "num_gpus"] in fields
    assert await _job_count(session_factory) == 0


async def test_nodes_over_group_ceiling_is_422_with_field(
    client: AsyncClient, seeded, team
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(
        f"{API}/jobs", headers=headers, json=_payload(team, num_nodes=MAX_NODES_PER_JOB + 1)
    )

    assert response.status_code == 422, response.text
    fields = [error["loc"] for error in response.json()["detail"]]
    assert ["body", "num_nodes"] in fields


async def test_walltime_above_static_max_is_422(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(
        f"{API}/jobs", headers=headers, json=_payload(team, walltime_min=7 * 24 * 60 + 1)
    )

    assert response.status_code == 422, response.text
    fields = [error["loc"] for error in response.json()["detail"]]
    assert ["body", "walltime_min"] in fields


async def test_quota_exhaustion_is_409_and_nothing_is_persisted(
    client: AsyncClient, seeded, team, session_factory
) -> None:
    async with session_factory() as db:
        quota = await db.scalar(select(Quota).where(Quota.group_id == team))
        quota.gpu_used = GROUP_GPU_LIMIT - 1  # only 1 GPU left
        await db.commit()

    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/jobs", headers=headers, json=_payload(team, num_gpus=2))

    assert response.status_code == 409, response.text
    assert "quota" in response.json()["detail"].lower()

    # The reservation rolled back together with the (never-created) job row.
    async with session_factory() as db:
        quota = await db.scalar(select(Quota).where(Quota.group_id == team))
    assert quota.gpu_used == GROUP_GPU_LIMIT - 1
    assert await _job_count(session_factory) == 0


async def test_submitting_to_a_foreign_group_is_403(
    client: AsyncClient, seeded, foreign_group
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/jobs", headers=headers, json=_payload(foreign_group))

    assert response.status_code == 403, response.text


async def test_submitting_to_an_unknown_group_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.post(f"{API}/jobs", headers=headers, json=_payload(9999))

    assert response.status_code == 404, response.text
