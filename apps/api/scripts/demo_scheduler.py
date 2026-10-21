"""Demo: push N jobs through the simulated scheduler and prove no leaks.

Run from ``apps/api`` (no Postgres/Redis required):

    .\\.venv\\Scripts\\python.exe scripts\\demo_scheduler.py
    .\\.venv\\Scripts\\python.exe scripts\\demo_scheduler.py --policy priority
    .\\.venv\\Scripts\\python.exe scripts\\demo_scheduler.py --compare

What it proves:

* every job reaches a terminal state (no deadlock);
* node GPU/CPU/memory counters and the group quota return to zero (no leak);
* one ``usage_events`` row per completed job;
* FIFO and priority produce different first-batch orders.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Configure the database before any ``app`` import (Settings is cached).
DEFAULT_DB = ROOT / "demo_scheduler.db"
if "DATABASE_URL" not in os.environ:
    DEFAULT_DB.unlink(missing_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DEFAULT_DB.as_posix()}"

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.models.group import Group  # noqa: E402
from app.models.job import Job  # noqa: E402
from app.models.node import Node  # noqa: E402
from app.models.quota import Quota  # noqa: E402
from app.models.usage_event import UsageEvent  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.job import JobCreate  # noqa: E402
from app.services import job_service, scheduler_service  # noqa: E402
from app.services.scheduling_policy import QueuedJob, get_policy  # noqa: E402

JOBS = 100
NODES = 4
GPUS_PER_NODE = 8
CPU_CORES = 64
MEM_GB = 256
REQUEST_GPUS = 2
REQUEST_CPUS = 8
REQUEST_MEM_GB = 32
WALLTIME_MIN = 1
SIM_STEP = timedelta(minutes=2)
MAX_CYCLES = 500


def _priority_for(index: int) -> int:
    """A deterministic spread so priority order clearly differs from FIFO."""
    return (index * 37) % 100


async def _reset_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def _seed() -> tuple[int, int]:
    async with async_session_factory() as db:
        user = User(email="demo@hpc.local", username="demo", hashed_password="x")
        group = Group(name="demo")
        group.members.append(user)
        db.add_all([user, group])
        await db.flush()
        db.add(
            Quota(
                group_id=group.id,
                gpu_limit=JOBS * REQUEST_GPUS + 100,
                max_nodes_per_job=NODES,
                max_gpus_per_job=GPUS_PER_NODE,
            )
        )
        for index in range(NODES):
            db.add(
                Node(
                    hostname=f"gpu-{index:02d}",
                    cpu_cores=CPU_CORES,
                    mem_gb=MEM_GB,
                    gpu_model="A100-80G" if index % 2 == 0 else "H100",
                    gpu_total=GPUS_PER_NODE,
                )
            )
        await db.commit()
        return user.id, group.id


async def _submit_all(user_id: int, group_id: int) -> None:
    async with async_session_factory() as db:
        user = await db.get(User, user_id)
        for index in range(JOBS):
            await job_service.submit_job(
                db,
                user,
                JobCreate(
                    group_id=group_id,
                    name=f"job-{index:03d}",
                    num_nodes=1,
                    num_gpus=REQUEST_GPUS,
                    num_cpus=REQUEST_CPUS,
                    mem_gb=REQUEST_MEM_GB,
                    priority=_priority_for(index),
                    walltime_min=WALLTIME_MIN,
                    params={"index": index},
                ),
            )


async def _first_batch_order(policy_name: str, limit: int = 10) -> list[int]:
    async with async_session_factory() as db:
        rows = await db.execute(
            select(
                Job.id,
                Job.user_id,
                Job.priority,
                Job.queued_at,
                Job.num_gpus,
                Job.num_cpus,
                Job.mem_gb,
            ).where(Job.status == "queued")
        )
        queued = [
            QueuedJob(
                id=row.id,
                user_id=row.user_id,
                priority=row.priority,
                queued_at=row.queued_at.replace(tzinfo=UTC)
                if row.queued_at.tzinfo is None
                else row.queued_at,
                num_gpus=row.num_gpus,
                num_cpus=row.num_cpus,
                mem_gb=row.mem_gb,
            )
            for row in rows.all()
        ]
    return [job.id for job in get_policy(policy_name).order(queued)[:limit]]


async def _run_scenario(policy_name: str) -> dict:
    await _reset_db()
    user_id, group_id = await _seed()
    await _submit_all(user_id, group_id)

    print(f"\n=== policy={policy_name} ===")
    first_batch = await _first_batch_order(policy_name)
    print(f"first 10 to try: {first_batch}")

    policy = get_policy(policy_name)
    # The demo compresses time: leases must outlive the simulated steps or the
    # reaper would mistake live jobs for orphans.
    settings.scheduler_lease_seconds = 3600

    sim_now = datetime.now(UTC)
    cycles = 0
    while True:
        cycles += 1
        if cycles > MAX_CYCLES:
            raise RuntimeError("deadlock: jobs did not reach a terminal state")

        async with async_session_factory() as db:
            await scheduler_service.renew_leases(db, now=sim_now)
            await scheduler_service.recover_orphaned_jobs(db, now=sim_now)
            started = await scheduler_service.schedule_once(db, policy=policy, now=sim_now)
            finished = await scheduler_service.finish_due_jobs(db, now=sim_now, rng=lambda: 0.0)
            remaining = await db.scalar(
                select(func.count()).select_from(Job).where(Job.status.in_(["queued", "running"]))
            )
        print(
            f"cycle {cycles:02d}: started={started:2d} finished={finished:2d} "
            f"remaining={remaining:3d}"
        )
        sim_now += SIM_STEP
        if remaining == 0:
            break
        await asyncio.sleep(0)

    async with async_session_factory() as db:
        status_rows = (
            await db.execute(select(Job.status, func.count()).group_by(Job.status))
        ).all()
        statuses = dict(status_rows)
        slots = (
            await db.execute(
                select(
                    func.sum(Node.gpu_used),
                    func.sum(Node.cpu_used),
                    func.sum(Node.mem_used),
                )
            )
        ).one()
        quota_used = await db.scalar(select(func.sum(Quota.gpu_used))) or 0
        usage_count = await db.scalar(select(func.count()).select_from(UsageEvent)) or 0
        usage_hours = await db.scalar(select(func.sum(UsageEvent.gpu_hours))) or 0

    gpu_used, cpu_used, mem_used = (int(value or 0) for value in slots)
    assert statuses.get("completed") == JOBS, statuses
    assert (gpu_used, cpu_used, mem_used) == (0, 0, 0), slots
    assert quota_used == 0, quota_used
    assert usage_count == JOBS, usage_count

    print(f"statuses       : {statuses}")
    print(f"node slots left: gpu={gpu_used} cpu={cpu_used} mem={mem_used}")
    print(f"quota used left: {quota_used}")
    print(f"usage events   : {usage_count} (total {usage_hours} GPU·h)")
    print(f"leak check     : PASS after {cycles} cycles")

    return {
        "policy": policy_name,
        "cycles": cycles,
        "statuses": statuses,
        "first_batch": first_batch,
        "usage_hours": str(usage_hours),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Scheduler demo (100 jobs)")
    parser.add_argument("--policy", choices=["fifo", "priority"])
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()

    names = ["fifo", "priority"] if args.compare else [args.policy or "fifo"]
    results = []
    for name in names:
        results.append(await _run_scenario(name))

    if len(results) > 1:
        print("\n=== comparison ===")
        for result in results:
            print(
                f"{result['policy']:<8} cycles={result['cycles']} "
                f"first_batch={result['first_batch']}"
            )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
