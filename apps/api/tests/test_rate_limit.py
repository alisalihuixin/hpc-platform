"""Sliding-window login rate limiting."""

from __future__ import annotations

from httpx import AsyncClient

from app.core.config import settings
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, API

MAX_ATTEMPTS = settings.login_rate_limit_max_attempts


async def test_burst_of_failed_logins_is_throttled(client: AsyncClient, seeded) -> None:
    payload = {"username": ADMIN_EMAIL, "password": "wrong-password"}

    # The first MAX_ATTEMPTS requests pass through (rejected on credentials, 401).
    for _ in range(MAX_ATTEMPTS):
        response = await client.post(f"{API}/auth/login", json=payload)
        assert response.status_code == 401

    # The next one is blocked by the limiter, before the password is checked.
    blocked = await client.post(f"{API}/auth/login", json=payload)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]
    assert int(blocked.headers["Retry-After"]) >= 1


async def test_limiter_is_keyed_per_identity(client: AsyncClient, seeded) -> None:
    for _ in range(MAX_ATTEMPTS + 1):
        await client.post(
            f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": "wrong-password"}
        )

    # A different account is unaffected by the first account's window.
    other = await client.post(
        f"{API}/auth/login", json={"username": "user@hpc.local", "password": "user12345"}
    )
    assert other.status_code == 200


async def test_successful_login_resets_the_window(client: AsyncClient, seeded) -> None:
    # Burn most of the budget with failures …
    for _ in range(MAX_ATTEMPTS - 1):
        await client.post(
            f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": "wrong-password"}
        )

    # … then a correct login clears the window.
    ok = await client.post(
        f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert ok.status_code == 200

    # The counter is back to zero, so failures can start again without a 429.
    again = await client.post(
        f"{API}/auth/login", json={"username": ADMIN_EMAIL, "password": "wrong-password"}
    )
    assert again.status_code == 401
