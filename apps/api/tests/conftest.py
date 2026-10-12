"""Shared pytest fixtures.

The app is wired to SQLite (in-memory, shared connection) and ``fakeredis`` via
``dependency_overrides`` so the whole suite runs without Postgres or Redis.
"""

from __future__ import annotations

import os

# Must be set before ``app.core.config`` is imported (Settings is cached).
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ["LOGIN_RATE_LIMIT_MAX_ATTEMPTS"] = "5"
os.environ["LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = "60"
os.environ["ENV"] = "test"

from collections.abc import AsyncGenerator, Callable  # noqa: E402

import pytest_asyncio  # noqa: E402
from fakeredis import aioredis as fakeredis_aioredis  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.redis import get_redis  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.role import Role  # noqa: E402
from app.models.user import User  # noqa: E402

ADMIN_EMAIL = "admin@hpc.local"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin12345"

USER_EMAIL = "user@hpc.local"
USER_USERNAME = "user"
USER_PASSWORD = "user12345"

API = "/api/v1"


@pytest_asyncio.fixture
async def engine():
    # StaticPool keeps every session on the same in-memory SQLite database.
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def seeded(session_factory) -> None:
    async with session_factory() as db:
        admin_role = Role(name="admin", description="Platform administrator")
        user_role = Role(name="user", description="Standard user")
        db.add_all([admin_role, user_role])
        await db.flush()
        db.add_all(
            [
                User(
                    email=ADMIN_EMAIL,
                    username=ADMIN_USERNAME,
                    full_name="Platform Admin",
                    hashed_password=hash_password(ADMIN_PASSWORD),
                    roles=[admin_role],
                ),
                User(
                    email=USER_EMAIL,
                    username=USER_USERNAME,
                    full_name="Normal User",
                    hashed_password=hash_password(USER_PASSWORD),
                    roles=[user_role],
                ),
            ]
        )
        await db.commit()


@pytest_asyncio.fixture
async def redis_client() -> AsyncGenerator:
    client = fakeredis_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def client(session_factory, redis_client) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_redis] = lambda: redis_client

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def login(client: AsyncClient) -> Callable:
    async def _login(username: str = ADMIN_EMAIL, password: str = ADMIN_PASSWORD):
        return await client.post(
            f"{API}/auth/login",
            json={"username": username, "password": password},
        )

    return _login


async def auth_headers(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    response = await client.post(
        f"{API}/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
