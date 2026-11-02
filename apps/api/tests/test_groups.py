"""Group administration (F8.2): CRUD, membership replacement, soft delete."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from app.models.quota import Quota
from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)


async def _user_ids(client: AsyncClient, headers: dict[str, str]) -> tuple[int, int]:
    listing = await client.get(f"{API}/users", headers=headers)
    items = listing.json()["items"]
    admin_id = next(item["id"] for item in items if item["email"] == ADMIN_EMAIL)
    user_id = next(item["id"] for item in items if item["email"] == USER_EMAIL)
    return admin_id, user_id


async def test_non_admin_cannot_manage_groups(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/groups")).status_code == 401

    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    assert (await client.get(f"{API}/groups", headers=headers)).status_code == 403


async def test_create_group_creates_quota_row(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    _, user_id = await _user_ids(client, headers)

    created = await client.post(
        f"{API}/groups",
        headers=headers,
        json={
            "name": "lab-a",
            "description": "Lab A",
            "gpu_limit": 32,
            "max_nodes_per_job": 2,
            "max_gpus_per_job": 4,
            "member_ids": [user_id],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "lab-a"
    assert [member["id"] for member in body["members"]] == [user_id]
    assert body["quota"]["gpu_limit"] == 32
    assert body["quota"]["gpu_used"] == 0
    assert body["is_active"] is True


async def test_duplicate_group_name_is_409(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    payload = {"name": "lab-a", "gpu_limit": 8}
    assert (await client.post(f"{API}/groups", headers=headers, json=payload)).status_code == 201

    duplicate = await client.post(f"{API}/groups", headers=headers, json=payload)
    assert duplicate.status_code == 409


async def test_unknown_member_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        f"{API}/groups",
        headers=headers,
        json={"name": "lab-b", "member_ids": [9999]},
    )
    assert response.status_code == 404


async def test_replace_members_is_idempotent(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    admin_id, user_id = await _user_ids(client, headers)

    created = await client.post(
        f"{API}/groups", headers=headers, json={"name": "lab-c", "member_ids": [user_id]}
    )
    group_id = created.json()["id"]

    cleared = await client.put(
        f"{API}/groups/{group_id}/members", headers=headers, json={"user_ids": []}
    )
    assert cleared.status_code == 200
    assert cleared.json()["members"] == []

    restored = await client.put(
        f"{API}/groups/{group_id}/members",
        headers=headers,
        json={"user_ids": [admin_id, user_id, user_id]},
    )
    assert restored.status_code == 200
    assert {member["id"] for member in restored.json()["members"]} == {admin_id, user_id}


async def test_rename_group_and_reject_taken_name(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    first = await client.post(f"{API}/groups", headers=headers, json={"name": "lab-d"})
    await client.post(f"{API}/groups", headers=headers, json={"name": "lab-e"})
    group_id = first.json()["id"]

    renamed = await client.patch(
        f"{API}/groups/{group_id}", headers=headers, json={"name": "lab-d2"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "lab-d2"

    taken = await client.patch(f"{API}/groups/{group_id}", headers=headers, json={"name": "lab-e"})
    assert taken.status_code == 409


async def test_delete_is_soft_and_blocked_while_quota_in_use(
    client: AsyncClient, seeded, session_factory
) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    created = await client.post(
        f"{API}/groups", headers=headers, json={"name": "lab-f", "gpu_limit": 10}
    )
    group_id = created.json()["id"]

    async with session_factory() as db:
        quota = await db.scalar(select(Quota).where(Quota.group_id == group_id))
        quota.gpu_used = 3
        await db.commit()

    blocked = await client.delete(f"{API}/groups/{group_id}", headers=headers)
    assert blocked.status_code == 409

    async with session_factory() as db:
        quota = await db.scalar(select(Quota).where(Quota.group_id == group_id))
        quota.gpu_used = 0
        await db.commit()

    deleted = await client.delete(f"{API}/groups/{group_id}", headers=headers)
    assert deleted.status_code == 204

    fetched = await client.get(f"{API}/groups/{group_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["is_active"] is False
