"""RBAC: role-gated endpoints and the 403 path."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)


async def test_anonymous_cannot_list_users(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/users")).status_code == 401


async def test_standard_user_gets_403_on_admin_endpoint(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.get(f"{API}/users", headers=headers)
    assert response.status_code == 403


async def test_admin_can_list_users(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.get(f"{API}/users", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {user["email"] for user in body["items"]} == {ADMIN_EMAIL, USER_EMAIL}


async def test_admin_creates_user_and_new_user_can_log_in(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    created = await client.post(
        f"{API}/users",
        headers=headers,
        json={
            "email": "alice@hpc.local",
            "username": "alice",
            "password": "alice-pass-123",
            "full_name": "Alice",
            "roles": ["user"],
        },
    )
    assert created.status_code == 201, created.text
    assert [role["name"] for role in created.json()["roles"]] == ["user"]

    login = await client.post(
        f"{API}/auth/login", json={"username": "alice@hpc.local", "password": "alice-pass-123"}
    )
    assert login.status_code == 200


async def test_duplicate_user_is_409(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        f"{API}/users",
        headers=headers,
        json={"email": ADMIN_EMAIL, "username": "admin2", "password": "whatever-123"},
    )
    assert response.status_code == 409


async def test_unknown_role_is_404(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    response = await client.post(
        f"{API}/users",
        headers=headers,
        json={
            "email": "bob@hpc.local",
            "username": "bob",
            "password": "bob-pass-123",
            "roles": ["nonexistent"],
        },
    )
    assert response.status_code == 404


async def test_deactivated_user_is_rejected(client: AsyncClient, seeded) -> None:
    admin_headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    # Grab the user's session *before* it is disabled.
    user_headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)

    listing = await client.get(f"{API}/users", headers=admin_headers)
    user_id = next(u["id"] for u in listing.json()["items"] if u["email"] == USER_EMAIL)

    patched = await client.patch(
        f"{API}/users/{user_id}", headers=admin_headers, json={"is_active": False}
    )
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False

    # Existing sessions stop working, and new logins are refused.
    assert (await client.get(f"{API}/auth/me", headers=user_headers)).status_code == 403

    relogin = await client.post(
        f"{API}/auth/login", json={"username": USER_EMAIL, "password": USER_PASSWORD}
    )
    assert relogin.status_code == 403
