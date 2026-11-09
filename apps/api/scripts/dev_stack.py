"""Local demo stack without Docker or a password-protected Redis (D28 aid).

Runs the FastAPI app on SQLite + ``fakeredis`` and drives the scheduler from an
in-process loop, so the full submit -> running -> completed -> billed chain and
the SSE stream work on a laptop with no infrastructure.

    python scripts/dev_stack.py            # API on http://127.0.0.1:8000
    cd ../web && npm run dev               # UI on http://localhost:3000

Then open ``/jobs`` in two browsers (admin + user) to check the <=2s refresh.

This is a demo harness only: real deployments use PostgreSQL + Redis + the ARQ
worker (see docker-compose.yml).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from pathlib import Path

DEMO_DB = Path(__file__).resolve().parent.parent / "dev_stack.db"


def _prepare_env(db_path: Path) -> None:
    os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    os.environ.setdefault("JWT_SECRET_KEY", "dev-stack-secret-not-for-production-use")
    os.environ.setdefault("ENV", "dev")
    # Fast logins while demoing; production keeps the OWASP floor (10).
    os.environ.setdefault("BCRYPT_ROUNDS", "4")


async def _ensure_roles() -> None:
    """Seed the two roles the migrations create (create_all does not run them)."""
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.role import Role

    async with async_session_factory() as db:
        for name, description in (
            ("admin", "Platform administrator"),
            ("user", "Standard user"),
        ):
            if await db.scalar(select(Role).where(Role.name == name)) is None:
                db.add(Role(name=name, description=description))
                print(f"+ created role {name}")
        await db.commit()


async def _ensure_demo_node() -> None:
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.node import Node

    async with async_session_factory() as db:
        existing = await db.scalar(select(Node).where(Node.hostname == "demo-gpu-01"))
        if existing is None:
            db.add(
                Node(
                    hostname="demo-gpu-01",
                    cpu_cores=32,
                    mem_gb=128,
                    gpu_model="A100-80G",
                    gpu_total=8,
                )
            )
            await db.commit()
            print("+ created demo node demo-gpu-01 (8 x A100-80G)")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--reset", action="store_true", help="delete demo DB first")
    args = parser.parse_args()

    if args.reset and DEMO_DB.exists():
        DEMO_DB.unlink()
        print(f"- removed {DEMO_DB.name}")

    _prepare_env(DEMO_DB)

    import uvicorn
    from fakeredis import aioredis as fakeredis_aioredis

    from app import initial_data
    from app.core.redis import get_redis
    from app.db.base import Base
    from app.db.session import async_session_factory, engine
    from app.main import app
    from app.services import scheduler_service

    async with engine.begin() as conn:
        # create_all is enough here; alembic owns the production schema.
        await conn.run_sync(Base.metadata.create_all)

    await _ensure_roles()
    await initial_data.main()
    await _ensure_demo_node()

    redis = fakeredis_aioredis.FakeRedis(decode_responses=True)
    app.dependency_overrides[get_redis] = lambda: redis

    async def poller() -> None:
        while True:
            try:
                async with async_session_factory() as db:
                    await scheduler_service.renew_leases(db)
                    await scheduler_service.recover_orphaned_jobs(db, redis=redis)
                    await scheduler_service.schedule_once(db, redis=redis)
                    await scheduler_service.finish_due_jobs(db, redis=redis)
            except Exception as exc:  # noqa: BLE001 - demo loop must stay alive
                print(f"[poller] {type(exc).__name__}: {exc}")
            await asyncio.sleep(args.interval)

    poller_task = asyncio.create_task(poller())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info"))

    print(f"\nAPI   http://127.0.0.1:{args.port}  (docs: /docs)")
    print(f"DB    {DEMO_DB}")
    print("UI    cd ../web && npm run dev  ->  http://localhost:3000\n")

    try:
        await server.serve()
    finally:
        poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller_task
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
