"""Quota endpoints (F3.4 / F3.5)."""

from __future__ import annotations

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models.group import Group
from app.models.quota import Quota
from app.models.user import User
from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)


@pytest_asyncio.fixture
async def team_group(session_factory, seeded) -> int:
    """A group of 100 GPU units that the standard user belongs to."""
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-quota")
        group.members.append(user)
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=100, max_nodes_per_job=2, max_gpus_per_job=8))
        await db.commit()
        return group.id


async def test_anonymous_cannot_read_my_quotas(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/quotas/me")).status_code == 401


async def test_member_sees_own_group_quota(client: AsyncClient, seeded, team_group) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.get(f"{API}/quotas/me", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["group_id"] == team_group
    assert item["group_name"] == "team-quota"
    assert item["gpu_limit"] == 100
    assert item["gpu_used"] == 0
    assert item["gpu_available"] == 100


async def test_user_without_groups_sees_nothing(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    body = (await client.get(f"{API}/quotas/me", headers=headers)).json()
    assert body == {"items": [], "total": 0}


async def test_non_admin_cannot_read_group_quota(client: AsyncClient, seeded, team_group) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (await client.get(f"{API}/quotas/{team_group}", headers=headers)).status_code == 403


async def test_admin_reads_group_quota(client: AsyncClient, seeded, team_group) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.get(f"{API}/quotas/{team_group}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["group_name"] == "team-quota"


async def test_admin_gets_404_for_unknown_group(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert (await client.get(f"{API}/quotas/9999", headers=headers)).status_code == 404


async def test_admin_adjusts_quota(client: AsyncClient, seeded, team_group) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.patch(
        f"{API}/quotas/{team_group}",
        headers=headers,
        json={"gpu_limit": 250, "max_gpus_per_job": 16},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gpu_limit"] == 250
    assert body["max_gpus_per_job"] == 16
    assert body["gpu_available"] == 250


async def test_admin_cannot_lower_limit_below_usage(
    client: AsyncClient, seeded, team_group, session_factory
) -> None:
    async with session_factory() as db:
        quota = await db.scalar(select(Quota).where(Quota.group_id == team_group))
        quota.gpu_used = 40
        await db.commit()

    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.patch(
        f"{API}/quotas/{team_group}", headers=headers, json={"gpu_limit": 30}
    )
    assert response.status_code == 409


async def test_non_admin_cannot_adjust_quota(client: AsyncClient, seeded, team_group) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.patch(
        f"{API}/quotas/{team_group}", headers=headers, json={"gpu_limit": 250}
    )
    assert response.status_code == 403
