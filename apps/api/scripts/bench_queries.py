"""Query benchmark for the read-heavy endpoints (F6.3 / D40).

Builds a throw-away SQLite database with ``--jobs`` rows plus matching usage
events, then times the queries behind the list / report / dashboard pages.

Usage::

    python scripts/bench_queries.py            # 10k jobs, 10 runs
    python scripts/bench_queries.py --jobs 20000 --runs 20

SQLite numbers are a lower bound for PostgreSQL (single process, no network);
the point is to catch accidental full scans and regressions between commits.
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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.db.base import Base
from app.models.group import Group
from app.models.job import Job
from app.models.node import Node
from app.models.quota import Quota
from app.models.role import Role
from app.models.usage_event import UsageEvent
from app.models.user import User
from app.services import report_service, usage_service

JOB_STATUSES = ["queued", "running", "completed", "failed", "canceled"]
BATCH = 1000


async def _seed(
    session_factory: async_sessionmaker[AsyncSession], *, total_jobs: int, users: int, nodes: int
) -> tuple[list[tuple[int, int]], list[tuple[int, int, int]]]:
    async with session_factory() as db:
        role = Role(name="user")
        db.add(role)
        await db.flush()

        groups: list[Group] = []
        for index in range(users):
            # Build the whole aggregate before flushing: appending to an already
            # persisted ``group.members`` would trigger a lazy load in async.
            group = Group(
                name=f"bench-group-{index}",
                quota=Quota(
                    gpu_limit=1_000_000,
                    max_nodes_per_job=8,
                    max_gpus_per_job=16,
                ),
                members=[
                    User(
                        email=f"bench-{index}@hpc.local",
                        username=f"bench-{index}",
                        hashed_password=hash_password("bench-pass-1"),
                        roles=[role],
                    )
                ],
            )
            groups.append(group)
            db.add(group)

        db.add_all(
            [
                Node(hostname=f"bench-node-{index}", cpu_cores=64, mem_gb=256, gpu_total=8)
                for index in range(nodes)
            ]
        )
        await db.commit()
        owners = [(group.id, group.members[0].id) for group in groups]

    for start in range(0, total_jobs, BATCH):
        async with session_factory() as db:
            db.add_all(
                [
                    Job(
                        user_id=owners[n % len(owners)][1],
                        group_id=owners[n % len(owners)][0],
                        name=f"bench-job-{n}",
                        num_nodes=1,
                        num_gpus=2,
                        num_cpus=8,
                        mem_gb=32,
                        walltime_min=60,
                        params={},
                        status=JOB_STATUSES[n % len(JOB_STATUSES)],
                    )
                    for n in range(start, min(start + BATCH, total_jobs))
                ]
            )
            await db.commit()

    async with session_factory() as db:
        result = await db.execute(select(Job.id, Job.user_id, Job.group_id))
        jobs = [(row.id, row.user_id, row.group_id) for row in result.all()]

    # Spread usage over six months so the trend window has work to do.
    now = datetime.now(UTC)
    for start in range(0, len(jobs), BATCH):
        async with session_factory() as db:
            rows = []
            for offset, (job_id, user_id, group_id) in enumerate(
                jobs[start : start + BATCH], start=start
            ):
                started = now.replace(day=1) - timedelta(days=30 * (offset % 6))
                rows.append(
                    UsageEvent(
                        job_id=job_id,
                        user_id=user_id,
                        group_id=group_id,
                        gpus=2,
                        started_at=started,
                        ended_at=started,
                        duration_min=60,
                        gpu_hours=Decimal("2.0000"),
                        rate_snapshot={"gpu_hour": "10.0000"},
                        reversed=offset % 20 == 0,
                    )
                )
            db.add_all(rows)
            await db.commit()

    return owners, jobs


async def _sample(
    session_factory: async_sessionmaker[AsyncSession],
    fn: Callable[[AsyncSession], Awaitable[object]],
    runs: int,
) -> tuple[float, float, float]:
    samples: list[float] = []
    for _ in range(runs):
        async with session_factory() as db:
            started = time.perf_counter()
            await fn(db)
            samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    p95_index = max(int(len(samples) * 0.95) - 1, 0)
    return statistics.median(samples), samples[p95_index], samples[-1]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=10_000)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--nodes", type=int, default=100)
    parser.add_argument("--keep", action="store_true", help="keep the temp database")
    args = parser.parse_args()

    handle, path = tempfile.mkstemp(suffix=".db", prefix="hpc-bench-")
    os.close(handle)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        print(f"seeding {args.jobs} jobs / {args.users} users / {args.nodes} nodes ...")
        owners, jobs = await _seed(
            session_factory, total_jobs=args.jobs, users=args.users, nodes=args.nodes
        )
        user_id = owners[0][1]
        group_id = owners[0][0]
        print(f"seeded {len(jobs)} jobs\n")

        async def job_list_user(db: AsyncSession) -> object:
            return await db.scalars(
                select(Job).where(Job.user_id == user_id).order_by(Job.created_at.desc()).limit(50)
            )

        async def job_list_status(db: AsyncSession) -> object:
            return await db.scalars(
                select(Job).where(Job.status == "queued").order_by(Job.queued_at, Job.id).limit(50)
            )

        async def usage_detail(db: AsyncSession) -> object:
            return await usage_service.list_user_usage(db, user_id)

        async def group_ranking(db: AsyncSession) -> object:
            return await usage_service.group_ranking(db, group_id)

        async def monthly_report(db: AsyncSession) -> object:
            return await report_service.monthly_billing(db, group_id=group_id)

        async def dashboard(db: AsyncSession) -> object:
            return await report_service.dashboard_overview(db)

        cases = [
            ("作业列表（用户 + 分页 50）", job_list_user),
            ("作业列表（状态 + 分页 50）", job_list_status),
            ("个人用量明细", usage_detail),
            ("组用量排行", group_ranking),
            ("月结聚合（按组）", monthly_report),
            ("平台看板（totals/趋势/TOP）", dashboard),
        ]

        print(f"{'查询':<28}{'p50(ms)':>10}{'p95(ms)':>10}{'max(ms)':>10}")
        failures = 0
        for label, fn in cases:
            p50, p95, worst = await _sample(session_factory, fn, args.runs)
            mark = "" if p95 < 200 else "  <-- over 200ms"
            failures += 0 if p95 < 200 else 1
            print(f"{label:<28}{p50:>10.2f}{p95:>10.2f}{worst:>10.2f}{mark}")

        print(
            f"\n{len(cases) - failures}/{len(cases)} queries p95 < 200ms "
            f"({args.jobs} jobs, {args.runs} runs, SQLite)"
        )

        async with session_factory() as db:
            plan = await db.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT * FROM jobs "
                    "WHERE user_id = :uid ORDER BY created_at DESC LIMIT 50"
                ),
                {"uid": user_id},
            )
            print("\nEXPLAIN jobs(user_id, created_at):")
            for row in plan.all():
                print(f"  {row[-1]}")

        if failures:
            raise SystemExit(1)
    finally:
        await engine.dispose()
        if args.keep:
            print(f"\nkept database: {path}")
        else:
            with contextlib.suppress(OSError):
                Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
