"""Monthly billing report (F6.1).

SQLite has no materialized views, so these cases exercise the live-aggregation
fallback that must stay shape-compatible with ``mv_monthly_billing``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.models.group import Group
from app.models.job import Job
from app.models.node import Node
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

OTHER_EMAIL = "other-report@hpc.local"
OTHER_PASSWORD = "other12345"


@pytest_asyncio.fixture
async def team(session_factory, seeded) -> int:
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-report")
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
            username="other-report",
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
    job_name: str = "report-job",
) -> None:
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
        db.add(
            UsageEvent(
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
        )
        await db.commit()


def _hours(value) -> Decimal:
    return Decimal(str(value))


async def _seed_rows(session_factory, team: int, other_user: int) -> int:
    async with session_factory() as db:
        user_id = (await db.scalar(select(User).where(User.email == USER_EMAIL))).id

    await _add_usage(
        session_factory, user_id, team, gpu_hours="2.0000", started_at=datetime(2026, 1, 15)
    )
    # Reversal in the same month folds into the bucket: 2.0 - 0.5 = 1.5.
    await _add_usage(
        session_factory,
        user_id,
        team,
        gpu_hours="0.5000",
        started_at=datetime(2026, 1, 16),
        reversed=True,
    )
    await _add_usage(
        session_factory,
        other_user,
        team,
        gpu_hours="5.0000",
        started_at=datetime(2026, 2, 10),
        job_name="other-report-job",
    )
    return user_id


async def test_admin_sees_every_month_and_user(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    user_id = await _seed_rows(session_factory, team, other_user)
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    body = (await client.get(f"{API}/reports/monthly", headers=headers)).json()

    assert len(body["items"]) == 2
    buckets = {(item["month"], item["user_id"]): item for item in body["items"]}
    assert _hours(buckets[("2026-01", user_id)]["gpu_hours"]) == Decimal("1.5000")
    assert buckets[("2026-01", user_id)]["job_count"] == 1
    assert _hours(buckets[("2026-02", other_user)]["gpu_hours"]) == Decimal("5.0000")
    assert _hours(body["total_gpu_hours"]) == Decimal("6.5000")


async def test_non_admin_only_sees_own_buckets(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    await _seed_rows(session_factory, team, other_user)
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)

    body = (await client.get(f"{API}/reports/monthly", headers=headers)).json()

    assert len(body["items"]) == 1
    assert _hours(body["items"][0]["gpu_hours"]) == Decimal("1.5000")
    assert _hours(body["total_gpu_hours"]) == Decimal("1.5000")


async def test_non_admin_cannot_read_another_user(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    await _seed_rows(session_factory, team, other_user)
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)

    response = await client.get(f"{API}/reports/monthly?user_id={other_user}", headers=headers)

    assert response.status_code == 403


async def test_filter_by_group(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    await _seed_rows(session_factory, team, other_user)
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    body = (await client.get(f"{API}/reports/monthly?group_id={team}", headers=headers)).json()
    assert len(body["items"]) == 2

    body = (
        await client.get(f"{API}/reports/monthly?group_id={team + 999}", headers=headers)
    ).json()
    assert body["items"] == []


async def test_refresh_is_a_noop_without_materialized_view(client: AsyncClient, seeded) -> None:
    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    user_headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)

    assert (
        await client.post(f"{API}/reports/monthly/refresh", headers=user_headers)
    ).status_code == 403

    response = await client.post(f"{API}/reports/monthly/refresh", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["refreshed"] is False
    assert "aggregated live" in body["detail"]


async def test_dashboard_totals_trend_and_top(
    client: AsyncClient, seeded, team, other_user, session_factory
) -> None:
    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    async with session_factory() as db:
        user_id = (await db.scalar(select(User).where(User.email == USER_EMAIL))).id
        db.add_all(
            [
                Node(
                    hostname="dash-online",
                    cpu_cores=8,
                    mem_gb=32,
                    gpu_total=4,
                    status="online",
                ),
                Node(
                    hostname="dash-offline",
                    cpu_cores=8,
                    mem_gb=32,
                    gpu_total=4,
                    status="offline",
                ),
                Job(
                    user_id=user_id,
                    group_id=team,
                    name="dash-running",
                    num_nodes=1,
                    num_gpus=1,
                    walltime_min=10,
                    params={},
                    status="running",
                ),
                Job(
                    user_id=user_id,
                    group_id=team,
                    name="dash-queued",
                    num_nodes=1,
                    num_gpus=1,
                    walltime_min=10,
                    params={},
                    status="queued",
                ),
            ]
        )
        await db.commit()

    await _add_usage(session_factory, user_id, team, gpu_hours="2.0000", started_at=month_start)
    await _add_usage(
        session_factory,
        other_user,
        team,
        gpu_hours="5.0000",
        started_at=month_start,
        job_name="other-dash-job",
    )

    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    user_headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (await client.get(f"{API}/reports/dashboard", headers=user_headers)).status_code == 403

    body = (await client.get(f"{API}/reports/dashboard", headers=admin_headers)).json()

    assert body["totals"]["running_jobs"] == 1
    assert body["totals"]["queued_jobs"] == 1
    assert body["totals"]["online_nodes"] == 1
    assert body["totals"]["total_nodes"] == 2
    assert body["totals"]["gpu_total"] == 8

    assert len(body["trend"]) == 6
    assert body["trend"][-1]["month"] == f"{now.year:04d}-{now.month:02d}"
    assert _hours(body["trend"][-1]["gpu_hours"]) == Decimal("7.0000")

    assert body["top_users"][0]["id"] == other_user
    assert _hours(body["top_users"][0]["gpu_hours"]) == Decimal("5.0000")
    assert body["top_groups"][0]["id"] == team
    assert _hours(body["top_groups"][0]["gpu_hours"]) == Decimal("7.0000")
