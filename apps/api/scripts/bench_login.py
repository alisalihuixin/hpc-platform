"""Login latency benchmark (D40 follow-up).

Measures ``POST /auth/login`` in-process (ASGI transport, no network) so the
bcrypt cost and event-loop behaviour are visible.  Two numbers matter:

* absolute p95 - the bcrypt cost at the configured rounds;
* ``p50(concurrency 10) / p50(serial)`` - if bcrypt ran on the event loop this
  ratio would be ~10; with ``asyncio.to_thread`` it stays near 1-2.

Usage::

    python scripts/bench_login.py --serial 10 --concurrency 10 --rounds 12
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import statistics
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

USER_EMAIL = "bench-login@hpc.local"
USER_PASSWORD = "bench-pass-123"

parser = argparse.ArgumentParser()
parser.add_argument("--serial", type=int, default=10)
parser.add_argument("--concurrency", type=int, default=10)
parser.add_argument("--per-worker", type=int, default=4)
parser.add_argument("--rounds", type=int, default=12)
args, _unknown = parser.parse_known_args()

handle, db_path = tempfile.mkstemp(suffix=".db", prefix="hpc-login-bench-")
os.close(handle)

# Settings are cached at import time, so the environment must be ready first.
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
os.environ["JWT_SECRET_KEY"] = "bench-secret-not-for-production"
os.environ["BCRYPT_ROUNDS"] = str(args.rounds)
os.environ["LOGIN_RATE_LIMIT_MAX_ATTEMPTS"] = "1000000"
os.environ["ENV"] = "bench"


def _percentile(samples: list[float], ratio: float) -> float:
    ordered = sorted(samples)
    index = min(int(len(ordered) * ratio) - 1, len(ordered) - 1)
    return ordered[max(index, 0)]


async def _timed(call: Callable[[], Awaitable[None]]) -> float:
    started = time.perf_counter()
    await call()
    return (time.perf_counter() - started) * 1000


async def main() -> None:
    from fakeredis import aioredis as fakeredis_aioredis
    from httpx import ASGITransport, AsyncClient

    from app.core.redis import get_redis
    from app.core.security import hash_password
    from app.db.base import Base
    from app.db.session import async_session_factory, engine, get_db
    from app.main import app
    from app.models.role import Role
    from app.models.user import User

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with async_session_factory() as db:
            role = Role(name="user")
            db.add(role)
            await db.flush()
            db.add(
                User(
                    email=USER_EMAIL,
                    username="bench-login",
                    hashed_password=hash_password(USER_PASSWORD),
                    roles=[role],
                )
            )
            await db.commit()

        async def _get_db():
            async with async_session_factory() as session:
                yield session

        redis = fakeredis_aioredis.FakeRedis(decode_responses=True)
        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_redis] = lambda: redis

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://bench") as client:

            async def login_once() -> None:
                response = await client.post(
                    "/api/v1/auth/login",
                    json={"username": USER_EMAIL, "password": USER_PASSWORD},
                )
                if response.status_code != 200:
                    raise SystemExit(f"login failed: {response.status_code} {response.text}")

            print(
                f"bcrypt rounds={args.rounds}, serial={args.serial}, "
                f"concurrency={args.concurrency}x{args.per_worker}"
            )

            serial: list[float] = []
            for _ in range(args.serial):
                serial.append(await _timed(login_once))

            async def worker() -> list[float]:
                return [await _timed(login_once) for _ in range(args.per_worker)]

            started = time.perf_counter()
            batches = await asyncio.gather(*[worker() for _ in range(args.concurrency)])
            wall = time.perf_counter() - started
            concurrent = [sample for batch in batches for sample in batch]

            def report(label: str, samples: list[float]) -> None:
                print(
                    f"{label:<16} p50={statistics.median(samples):7.1f}ms "
                    f"p95={_percentile(samples, 0.95):7.1f}ms "
                    f"max={max(samples):7.1f}ms"
                )

            report("serial", serial)
            report("concurrent", concurrent)
            ratio = statistics.median(concurrent) / statistics.median(serial)
            rps = len(concurrent) / wall
            print(
                f"\np50 ratio (concurrent/serial) = {ratio:.2f}  (≈1-2 means the "
                f"event loop was not blocked)"
            )
            print(f"throughput = {rps:.1f} logins/s over {wall:.2f}s")

        await redis.aclose()
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        with contextlib.suppress(OSError):
            Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
