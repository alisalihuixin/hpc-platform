"""D34 regression sweep.

These cases deliberately span more than one module (HTTP -> service -> DB) so a
regression in any layer surfaces here instead of leaking silently:

* submit -> schedule -> finish -> billing -> quota returned (full lifecycle);
* cancel a *running* job returns the quota **and** every node slot;
* out-of-range submit payloads fail with field-level 422 before any DB write;
* pagination / query boundaries stay 422;
* a deactivated group disappears from the member's quota list and rejects submits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models.group import Group
from app.models.node import Node
from app.models.quota import Quota
from app.models.user import User
from app.services import scheduler_service
from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)


@pytest_asyncio.fixture
async def team(session_factory, seeded) -> int:
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == USER_EMAIL))
        group = Group(name="team-regression")
        group.members.append(user)
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=8, max_nodes_per_job=2, max_gpus_per_job=4))
        await db.commit()
        return group.id


@pytest_asyncio.fixture
async def node(session_factory, seeded) -> int:
    async with session_factory() as db:
        row = Node(hostname="gpu-regression", cpu_cores=32, mem_gb=128, gpu_total=4)
        db.add(row)
        await db.commit()
        return row.id


async def _submit(client: AsyncClient, headers: dict[str, str], team: int, **overrides):
    payload = {
        "group_id": team,
        "name": "regression",
        "num_nodes": 1,
        "num_gpus": 2,
        "num_cpus": 8,
        "mem_gb": 32,
        "walltime_min": 60,
        "params": {},
    }
    payload.update(overrides)
    return await client.post(f"{API}/jobs", headers=headers, json=payload)


async def _slots(session_factory, node_id: int) -> tuple[int, int, int]:
    async with session_factory() as db:
        row = await db.get(Node, node_id)
        return row.gpu_used, row.cpu_used, row.mem_used


async def _quota_used(session_factory, group_id: int) -> int:
    async with session_factory() as db:
        quota = await db.scalar(select(Quota).where(Quota.group_id == group_id))
        return quota.gpu_used


async def test_submit_schedule_finish_and_bill_end_to_end(
    client: AsyncClient, seeded, team, node, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    created = await _submit(client, headers, team)
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    base = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    async with session_factory() as db:
        assert await scheduler_service.schedule_once(db, now=base) == 1
    assert await _slots(session_factory, node) == (2, 8, 32)

    async with session_factory() as db:
        assert (
            await scheduler_service.finish_due_jobs(
                db, now=base + timedelta(minutes=60), rng=lambda: 1.0
            )
            == 1
        )

    body = (await client.get(f"{API}/jobs/{job_id}", headers=headers)).json()
    assert body["status"] == "completed"
    assert body["gpu_minutes"] == 2 * 60

    usage = (await client.get(f"{API}/usage/me", headers=headers)).json()
    assert usage["total"] == 1
    assert Decimal(usage["total_gpu_hours"]) == Decimal("2.0000")

    quotas = (await client.get(f"{API}/quotas/me", headers=headers)).json()
    assert quotas["items"][0]["gpu_used"] == 0

    assert await _slots(session_factory, node) == (0, 0, 0)


async def test_cancel_running_job_returns_quota_and_every_node_slot(
    client: AsyncClient, seeded, team, node, session_factory
) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    created = await _submit(client, headers, team)
    job_id = created.json()["id"]
    assert await _quota_used(session_factory, team) == 2

    async with session_factory() as db:
        assert await scheduler_service.schedule_once(db, now=datetime.now(UTC)) == 1
    assert await _slots(session_factory, node) == (2, 8, 32)

    canceled = await client.post(f"{API}/jobs/{job_id}/cancel", headers=headers)
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "canceled"

    # A running job leaks capacity unless GPU *and* CPU / memory are released.
    assert await _slots(session_factory, node) == (0, 0, 0)
    assert await _quota_used(session_factory, team) == 0

    # Cancelling is not billed, and a second attempt is a no-op 409.
    assert (await client.get(f"{API}/usage/me", headers=headers)).json()["total"] == 0
    assert (await client.post(f"{API}/jobs/{job_id}/cancel", headers=headers)).status_code == 409


async def test_out_of_range_submit_payloads_are_422(client: AsyncClient, seeded, team) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)

    invalid: list[dict[str, object]] = [
        {"num_nodes": 0},
        {"num_gpus": -1},
        {"num_cpus": -1},
        {"mem_gb": -1},
        {"walltime_min": 0},
        {"priority": -1},
        {"name": ""},
        {"group_id": 0},
    ]
    for override in invalid:
        response = await _submit(client, headers, team, **override)
        assert response.status_code == 422, (override, response.text)


async def test_pagination_bounds_are_422(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    for path in (
        "/jobs?limit=0",
        "/jobs?limit=201",
        "/jobs?skip=-1",
        "/users?limit=201",
        "/groups?limit=0",
        "/quotas?limit=201",
        "/jobs?status=unknown",
    ):
        response = await client.get(f"{API}{path}", headers=headers)
        assert response.status_code == 422, (path, response.text)


async def test_deactivated_group_hides_quota_and_rejects_submission(
    client: AsyncClient, seeded, team
) -> None:
    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)

    assert (await client.get(f"{API}/quotas/me", headers=headers)).json()["total"] == 1

    patched = await client.patch(
        f"{API}/groups/{team}", headers=admin_headers, json={"is_active": False}
    )
    assert patched.status_code == 200, patched.text

    assert (await client.get(f"{API}/quotas/me", headers=headers)).json()["total"] == 0
    assert (await _submit(client, headers, team)).status_code == 404
