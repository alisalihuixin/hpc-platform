"""Login / refresh / logout flows."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import (
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    API,
    USER_EMAIL,
    USER_PASSWORD,
    auth_headers,
)


async def test_login_with_email_returns_token_pair(client: AsyncClient, seeded) -> None:
    response = await client.post(
        f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


async def test_login_accepts_username_too(client: AsyncClient, seeded) -> None:
    response = await client.post(
        f"{API}/auth/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200


async def test_login_with_wrong_password_is_401(client: AsyncClient, seeded) -> None:
    response = await client.post(
        f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_login_with_unknown_user_is_401(client: AsyncClient, seeded) -> None:
    response = await client.post(
        f"{API}/auth/login", json={"username": "nobody@hpc.local", "password": "whatever"}
    )
    assert response.status_code == 401


async def test_me_requires_a_token(client: AsyncClient, seeded) -> None:
    assert (await client.get(f"{API}/auth/me")).status_code == 401


async def test_me_rejects_garbage_token(client: AsyncClient, seeded) -> None:
    response = await client.get(
        f"{API}/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


async def test_me_returns_profile_with_roles(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, USER_EMAIL, USER_PASSWORD)
    response = await client.get(f"{API}/auth/me", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == USER_EMAIL
    assert [role["name"] for role in body["roles"]] == ["user"]


async def test_logout_revokes_access_token(client: AsyncClient, seeded) -> None:
    headers = await auth_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 200

    logout = await client.post(f"{API}/auth/logout", headers=headers)
    assert logout.status_code == 200

    # The old access token must now be rejected instantly (blacklisted jti).
    revoked = await client.get(f"{API}/auth/me", headers=headers)
    assert revoked.status_code == 401


async def test_logout_revokes_refresh_token(client: AsyncClient, seeded) -> None:
    login = await client.post(
        f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    tokens = login.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    await client.post(
        f"{API}/auth/logout",
        headers=headers,
        json={"refresh_token": tokens["refresh_token"]},
    )

    reuse = await client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reuse.status_code == 401


async def test_refresh_rotates_and_invalidates_old_token(client: AsyncClient, seeded) -> None:
    login = await client.post(
        f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    original_refresh = login.json()["refresh_token"]

    rotated = await client.post(f"{API}/auth/refresh", json={"refresh_token": original_refresh})
    assert rotated.status_code == 200, rotated.text
    new_access = rotated.json()["access_token"]

    # New access token works …
    me = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200

    # … and the consumed refresh token cannot be replayed.
    replay = await client.post(f"{API}/auth/refresh", json={"refresh_token": original_refresh})
    assert replay.status_code == 401


async def test_refresh_rejects_access_token(client: AsyncClient, seeded) -> None:
    login = await client.post(
        f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    access_token = login.json()["access_token"]

    response = await client.post(f"{API}/auth/refresh", json={"refresh_token": access_token})
    assert response.status_code == 401
