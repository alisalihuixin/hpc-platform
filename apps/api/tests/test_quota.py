"""Quota reservation (F3.2 / F3.3).

The headline test is ``test_concurrent_reservations_never_oversell``: it runs
real concurrent transactions against a file-backed SQLite database (the shared
in-memory ``StaticPool`` fixture serialises everything through one connection,
which would not exercise the race).
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import AppError, ConflictError
from app.db.base import Base
from app.models.group import Group
from app.models.quota import Quota
from app.services import quota_service


@pytest_asyncio.fixture
async def group_id(session_factory) -> int:
    async with session_factory() as db:
        group = Group(name="team-a")
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=100, max_nodes_per_job=2, max_gpus_per_job=8))
        await db.commit()
        return group.id


async def _used(session_factory, group_id: int) -> int:
    async with session_factory() as db:
        quota = await quota_service.get_quota(db, group_id)
        assert quota is not None
        return quota.gpu_used


async def test_reserve_within_limit(session_factory, group_id) -> None:
    async with session_factory() as db:
        assert await quota_service.reserve(db, group_id, 30) is True
        await db.commit()

    assert await _used(session_factory, group_id) == 30


async def test_reserve_exactly_to_the_limit(session_factory, group_id) -> None:
    async with session_factory() as db:
        assert await quota_service.reserve(db, group_id, 100) is True
        await db.commit()

    assert await _used(session_factory, group_id) == 100


async def test_reserve_beyond_limit_changes_nothing(session_factory, group_id) -> None:
    async with session_factory() as db:
        assert await quota_service.reserve(db, group_id, 60) is True
        await db.commit()
    async with session_factory() as db:
        assert await quota_service.reserve(db, group_id, 50) is False
        await db.commit()

    assert await _used(session_factory, group_id) == 60


async def test_reserve_or_raise_conflicts(session_factory, group_id) -> None:
    async with session_factory() as db:
        with pytest.raises(ConflictError):
            await quota_service.reserve_or_raise(db, group_id, 101)


async def test_reserve_rejects_non_positive_amount(session_factory, group_id) -> None:
    async with session_factory() as db:
        with pytest.raises(AppError):
            await quota_service.reserve(db, group_id, 0)


async def test_release_refunds(session_factory, group_id) -> None:
    async with session_factory() as db:
        await quota_service.reserve(db, group_id, 40)
        await db.commit()
    async with session_factory() as db:
        assert await quota_service.release(db, group_id, 25) is True
        await db.commit()

    assert await _used(session_factory, group_id) == 15


async def test_release_never_goes_negative(session_factory, group_id) -> None:
    async with session_factory() as db:
        assert await quota_service.release(db, group_id, 10) is False
        await db.commit()

    assert await _used(session_factory, group_id) == 0


async def test_check_constraint_blocks_overshoot(session_factory, group_id) -> None:
    async with session_factory() as db:
        with pytest.raises(IntegrityError):
            await db.execute(update(Quota).where(Quota.group_id == group_id).values(gpu_used=999))
            await db.commit()


@pytest_asyncio.fixture
async def file_session_factory():
    handle, path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    url = "sqlite+aiosqlite:///" + path.replace("\\", "/")
    engine = create_async_engine(url, connect_args={"timeout": 30})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield factory
    await engine.dispose()
    os.remove(path)


async def test_concurrent_reservations_never_oversell(file_session_factory) -> None:
    factory = file_session_factory
    async with factory() as db:
        group = Group(name="burst")
        db.add(group)
        await db.flush()
        db.add(Quota(group_id=group.id, gpu_limit=100, max_nodes_per_job=1, max_gpus_per_job=10))
        await db.commit()
        group = group.id

    async def worker() -> bool:
        async with factory() as db:
            granted = await quota_service.reserve(db, group, 10)
            await db.commit()
            return granted

    results = await asyncio.gather(*(worker() for _ in range(20)))

    async with factory() as db:
        quota = await db.scalar(select(Quota).where(Quota.group_id == group))

    assert quota is not None
    assert sum(results) == 10  # 100 / 10 - the other 10 must be rejected
    assert quota.gpu_used == 100
    assert quota.gpu_used <= quota.gpu_limit
