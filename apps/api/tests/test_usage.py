"""Usage queries (F5.3 personal detail / F5.4 group ranking)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.models.group import Group
from app.models.job import Job
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.models.user import User
from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)

OTHER_EMAIL = "other-usage@hpc.local"
OTHER_PASSWORD = "other12345"


@pytest_asyncio.fixture
async def team(session_factory, seeded) -> int:
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-usage")
        group.members.append(user)
        db.add(group)
        await db.commit()
        return group.id


@pytest_asyncio.fixture
async def other_user(session_factory, seeded) -> int:
    async with session_factory() as db:
        role = await db.scalar(select(Role).where(Role.name == "user"))
        user = User(
            email=OTHER_EMAIL,
            username="other-usage",
            hashed_password=hash_password(OTHER_PASSWORD),
            roles=[role],
        )
        db.add(user)
        await db.commit()
        return user.id


async def _add_usage(
    session_factory,
    user_id: int,
    group_id: int,
    *,
    gpu_hours: str,
    started_at: datetime,
    reversed: bool = False,
    job_name: str = "billed-job",
) -> int:
    """Create a job plus one usage row (reversal rows reuse the same job)."""
    async with session_factory() as db:
        job = await db.scalar(select(Job).where(Job.user_id == user_id, Job.name == job_name))
        if job is None:
            job = Job(
                user_id=user_id,
                group_id=group_id,
                name=job_name,
                num_nodes=1,
                num_gpus=2,
                walltime_min=30,
                params={},
                status="completed",
            )
            db.add(job)
            await db.flush()
        event = UsageEvent(
            job_id=job.id,
            user_id=user_id,
            group_id=group_id,
            gpus=2,
            started_at=started_at,
            ended_at=started_at,
            duration_min=30,
            gpu_hours=Decimal(gpu_hours),
            rate_snapshot={"gpu_hour": "10.0000"},
            reversed=reversed,
        )
        db.add(event)
        await db.commit()
        return event.id


def _hours(value) -> Decimal:
    return Decimal(str(value))


async def test_anonymous_cannot_read_my_usage(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/usage/me")).status_code == 401


async def test_my_usage_returns_own_rows_and_net_total(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    user_id = (await client.get(f"{API}/auth/me", headers=headers)).json()["id"]

    await _add_usage(
        session_factory, user_id, team, gpu_hours="2.0000", started_at=datetime(2026, 1, 1)
    )
    await _add_usage(
        session_factory,
        user_id,
        team,
        gpu_hours="0.5000",
        started_at=datetime(2026, 1, 1),
        reversed=True,
    )
    await _add_usage(
        session_factory,
        other_user,
        team,
        gpu_hours="5.0000",
        started_at=datetime(2026, 1, 2),
        job_name="their-job",
    )

    body = (await client.get(f"{API}/usage/me", headers=headers)).json()

    assert body["total"] == 2
    assert _hours(body["total_gpu_hours"]) == Decimal("1.5000")
    signed = sum(_hours(item["signed_gpu_hours"]) for item in body["items"])
    assert signed == _hours(body["total_gpu_hours"])
    assert {item["user_id"] for item in body["items"]} == {user_id}
    assert any(item["reversed"] for item in body["items"])


async def test_my_usage_filters_by_time(
    client: AsyncClient, seeded, team, session_factory, other_user
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    user_id = (await client.get(f"{API}/auth/me", headers=headers)).json()["id"]

    await _add_usage(
        session_factory, user_id, team, gpu_hours="1.0000", started_at=datetime(2026, 1, 1)
    )
    await _add_usage(
        session_factory,
        user_id,
        team,
        gpu_hours="2.0000",
        started_at=datetime(2026, 2, 1),
        job_name="feb-job",
    )

    january = (
        await client.get(f"{API}/usage/me?until=2026-01-15T00:00:00", headers=headers)
    ).json()
    february = (
        await client.get(f"{API}/usage/me?since=2026-01-15T00:00:00", headers=headers)
    ).json()

    assert january["total"] == 1
    assert _hours(january["total_gpu_hours"]) == Decimal("1.0000")
    assert february["total"] == 1
    assert _hours(february["total_gpu_hours"]) == Decimal("2.0000")


async def test_group_ranking_orders_by_net_hours(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    me = (await client.get(f"{API}/auth/me", headers=headers)).json()["id"]

    await _add_usage(session_factory, me, team, gpu_hours="2.0000", started_at=datetime(2026, 1, 1))
    await _add_usage(
        session_factory,
        me,
        team,
        gpu_hours="0.5000",
        started_at=datetime(2026, 1, 1),
        reversed=True,
    )
    await _add_usage(
        session_factory,
        other_user,
        team,
        gpu_hours="3.0000",
        started_at=datetime(2026, 1, 2),
        job_name="their-job",
    )

    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.get(f"{API}/usage/groups/{team}", headers=admin_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["group_name"] == "team-usage"
    assert [item["user_id"] for item in body["items"]] == [other_user, me]
    assert [_hours(item["gpu_hours"]) for item in body["items"]] == [
        Decimal("3.0000"),
        Decimal("1.5000"),
    ]
    # Job count counts distinct jobs, so the reversal does not inflate it.
    assert [item["job_count"] for item in body["items"]] == [1, 1]

    # Ranking total reconciles with its own rows.
    assert sum(_hours(item["gpu_hours"]) for item in body["items"]) == _hours(
        body["total_gpu_hours"]
    )
    assert _hours(body["total_gpu_hours"]) == Decimal("4.5000")


async def test_group_ranking_filters_by_time(
    client: AsyncClient, seeded, team, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    me = (await client.get(f"{API}/auth/me", headers=headers)).json()["id"]
    await _add_usage(session_factory, me, team, gpu_hours="1.0000", started_at=datetime(2026, 1, 1))
    await _add_usage(
        session_factory,
        me,
        team,
        gpu_hours="2.0000",
        started_at=datetime(2026, 2, 1),
        job_name="feb-job",
    )

    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    body = (
        await client.get(
            f"{API}/usage/groups/{team}?since=2026-01-15T00:00:00", headers=admin_headers
        )
    ).json()

    assert _hours(body["total_gpu_hours"]) == Decimal("2.0000")
    assert len(body["items"]) == 1


async def test_non_admin_cannot_read_group_ranking(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (await client.get(f"{API}/usage/groups/{team}", headers=headers)).status_code == 403


async def test_unknown_group_ranking_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    assert (await client.get(f"{API}/usage/groups/9999", headers=headers)).status_code == 404


async def test_ranking_excludes_other_groups(
    client: AsyncClient, seeded, team, session_factory
) -> None:
    async with session_factory() as db:
        other_group = Group(name="team-other-usage")
        db.add(other_group)
        await db.flush()
        other_group_id = other_group.id
        me = await db.scalar(select(User).where(User.email == USER_EMAIL))
        user_id = me.id
        await db.commit()

    await _add_usage(
        session_factory, user_id, team, gpu_hours="1.0000", started_at=datetime(2026, 1, 1)
    )
    await _add_usage(
        session_factory,
        user_id,
        other_group_id,
        gpu_hours="9.0000",
        started_at=datetime(2026, 1, 1),
        job_name="other-group-job",
    )

    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    body = (await client.get(f"{API}/usage/groups/{team}", headers=admin_headers)).json()
    assert _hours(body["total_gpu_hours"]) == Decimal("1.0000")
