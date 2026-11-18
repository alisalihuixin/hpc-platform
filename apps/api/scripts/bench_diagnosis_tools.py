"""Latency benchmark for the diagnosis tools (D50).

Builds a throw-away SQLite database with the seeded mock fleet / scripts /
runbooks, then replays each tool exactly as the graph will call it.  The
response already carries its own ``latency_ms``, so the numbers are the tool's
measured work, not the harness's overhead.

Usage::

    python scripts/bench_diagnosis_tools.py             # 20 runs per tool
    python scripts/bench_diagnosis_tools.py --runs 50

The M5 gate is p95 < 50ms against these mocks (SQLite, single process).  Real
SSH/DB latency belongs to M8's integration environment.
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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.diagnosis.data.mock import seed_diagnosis_data
from app.diagnosis.data.runbooks import seed_runbooks
from app.diagnosis.data.scripts import seed_fault_scripts
from app.diagnosis.tools import ToolResponse, get_job_logs, get_metrics, get_node_gpus
from app.diagnosis.tools.node import get_node_status
from app.diagnosis.tools.runbooks import search_runbooks
from app.models.diagnosis import (
    Alert,
    DiagnosisReview,
    DiagnosisRun,
    DiagnosisStep,
    EvalCase,
    IncidentNode,
    Runbook,
)

P95_BUDGET_MS = 50


async def _data_scale(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    models = {
        "incident_nodes": IncidentNode,
        "alerts": Alert,
        "diagnosis_runs": DiagnosisRun,
        "diagnosis_steps": DiagnosisStep,
        "diagnosis_reviews": DiagnosisReview,
        "eval_cases": EvalCase,
        "runbooks": Runbook,
    }
    async with session_factory() as db:
        counts = {
            name: int(await db.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in models.items()
        }
        counts["gpus"] = int(
            sum((node.gpu_count or 0) for node in (await db.scalars(select(IncidentNode))).all())
        )
    return counts


async def _sample(
    session_factory: async_sessionmaker[AsyncSession],
    fn: Callable[[AsyncSession], Awaitable[ToolResponse]],
    runs: int,
) -> tuple[list[int], bool]:
    samples: list[int] = []
    all_ok = True
    for _ in range(runs):
        async with session_factory() as db:
            started = time.perf_counter()
            response = await fn(db)
            # The harness clock guards against a tool that forgets to time itself.
            if response.latency_ms == 0:
                response = ToolResponse(
                    tool=response.tool,
                    ok=response.ok,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    data=response.data,
                    error=response.error,
                )
        samples.append(response.latency_ms)
        all_ok = all_ok and response.ok
    samples.sort()
    return samples, all_ok


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--keep", action="store_true", help="keep the temp database")
    args = parser.parse_args()

    handle, path = tempfile.mkstemp(suffix=".db", prefix="hpc-diagnosis-bench-")
    os.close(handle)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with session_factory() as db:
            await seed_diagnosis_data(db)
            await seed_fault_scripts(db)
            await seed_runbooks(db)
            await db.commit()

        scale = await _data_scale(session_factory)
        print("data: " + " / ".join(f"{name}={value}" for name, value in scale.items()) + "\n")

        async def node_status(db: AsyncSession) -> ToolResponse:
            return await get_node_status(db, "gpu-003")

        async def node_gpus(db: AsyncSession) -> ToolResponse:
            return await get_node_gpus(db, "gpu-003")

        async def job_logs(db: AsyncSession) -> ToolResponse:
            return await get_job_logs(db, "gpu-029", limit=50)

        async def metrics(db: AsyncSession) -> ToolResponse:
            return await get_metrics(db, window=60)

        async def runbooks(db: AsyncSession) -> ToolResponse:
            return await search_runbooks(db, tags="gpu,ecc")

        cases = [
            ("get_node_status", node_status),
            ("get_node_gpus", node_gpus),
            ("get_job_logs", job_logs),
            ("get_metrics", metrics),
            ("search_runbooks", runbooks),
        ]

        print(f"{'tool':<20}{'p50(ms)':>10}{'p95(ms)':>10}{'max(ms)':>10}{'ok':>6}")
        failures = 0
        for label, fn in cases:
            samples, all_ok = await _sample(session_factory, fn, args.runs)
            p50 = statistics.median(samples)
            p95 = samples[max(int(len(samples) * 0.95) - 1, 0)]
            mark = "" if p95 < P95_BUDGET_MS and all_ok else "  <-- over budget"
            failures += 0 if p95 < P95_BUDGET_MS and all_ok else 1
            print(f"{label:<20}{p50:>10.1f}{p95:>10.1f}{samples[-1]:>10}{str(all_ok):>6}{mark}")

        print(
            f"\n{len(cases) - failures}/{len(cases)} tools p95 < {P95_BUDGET_MS}ms "
            f"({args.runs} runs each, SQLite)"
        )
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
