"""D42: the seeded mock fleet must be byte-identical across runs and idempotent.

Reproducibility is an evaluation precondition, not a nicety: if the 50-node
fleet or the 200 history tickets drifted between two seeds, a hit-rate delta in
``docs/metrics.md`` could come from the data instead of the agent.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.diagnosis.data.mock import (
    CATEGORIES,
    NODE_COUNT,
    SEED,
    TICKET_COUNT,
    Fleet,
    SeedReport,
    build_fleet,
    seed_diagnosis_data,
    snapshot,
)
from app.models.diagnosis import (
    Alert,
    DiagnosisReview,
    DiagnosisRun,
    DiagnosisStep,
    IncidentNode,
    NodeHealth,
)


def test_two_builds_are_byte_identical() -> None:
    first = json.dumps(snapshot(build_fleet()), sort_keys=True, ensure_ascii=False)
    second = json.dumps(snapshot(build_fleet(SEED)), sort_keys=True, ensure_ascii=False)
    assert first == second


def test_changing_the_seed_changes_the_fleet() -> None:
    assert snapshot(build_fleet(SEED)) != snapshot(build_fleet(SEED + 1))


def test_fleet_shape_matches_the_plan() -> None:
    fleet: Fleet = build_fleet()
    assert len(fleet.nodes) == NODE_COUNT == 50
    assert len(fleet.tickets) == TICKET_COUNT == 200
    assert len(fleet.logs) == NODE_COUNT
    assert len({node.hostname for node in fleet.nodes}) == NODE_COUNT

    by_switch: dict[str, list[str]] = {}
    for node in fleet.nodes:
        by_switch.setdefault(node.switch, []).append(node.hostname)
    # 10 switches x 5 nodes -> the M7 "5 alerts on one switch" demo is possible.
    assert len(by_switch) == 10
    assert {len(hostnames) for hostnames in by_switch.values()} == {5}

    # Every ticket class appears 20 times, so history is balanced per category.
    counts: dict[str, int] = {}
    for ticket in fleet.tickets:
        counts[ticket.script_key] = counts.get(ticket.script_key, 0) + 1
    assert counts == {category.key: 20 for category in CATEGORIES}


def test_gpu_cards_and_pinned_ground_truths() -> None:
    fleet = build_fleet()
    for node in fleet.nodes:
        cards = node.tags["gpus"]
        assert [card["index"] for card in cards] == list(range(node.gpu_count))

    assert sum(node.gpu_count for node in fleet.nodes) == 360

    ecc_nodes = {
        node.hostname: card
        for node in fleet.nodes
        for card in node.tags["gpus"]
        if card["ecc_errors"]
    }
    assert list(ecc_nodes) == ["gpu-003"]
    assert ecc_nodes["gpu-003"]["index"] == 2
    assert ecc_nodes["gpu-003"]["ecc_errors"] == 12

    offline = [node for node in fleet.nodes if node.status is NodeHealth.OFFLINE]
    assert [node.hostname for node in offline] == ["gpu-029"]
    assert all(card["status"] == "unreachable" for card in offline[0].tags["gpus"])

    # Script #9 ground truth: the node is alive but cordoned by an operator.
    assert [node.hostname for node in fleet.nodes if not node.schedulable] == ["gpu-009"]
    assert "unschedulable=true" in " ".join(fleet.logs["gpu-009"])
    assert "heartbeat missed" in " ".join(fleet.logs["gpu-029"])
    assert "ECC error count 12" in " ".join(fleet.logs["gpu-003"])


async def test_seed_is_idempotent(session_factory) -> None:
    async with session_factory() as db:
        first = await seed_diagnosis_data(db)
        await db.commit()
    assert first == SeedReport(
        inserted=True, nodes=50, tickets=200, runs=200, steps=600, reviews=200
    )

    async with session_factory() as db:
        second = await seed_diagnosis_data(db)
        await db.commit()
        assert second.inserted is False
        assert await db.scalar(select(func.count()).select_from(IncidentNode)) == NODE_COUNT
        assert await db.scalar(select(func.count()).select_from(Alert)) == TICKET_COUNT
        assert await db.scalar(select(func.count()).select_from(DiagnosisRun)) == TICKET_COUNT
        assert await db.scalar(select(func.count()).select_from(DiagnosisStep)) == 600
        assert await db.scalar(select(func.count()).select_from(DiagnosisReview)) == TICKET_COUNT

        # 29 of 200 reviews disagree with the model -> the review metric is real.
        wrong = await db.scalar(
            select(func.count())
            .select_from(DiagnosisReview)
            .where(DiagnosisReview.verdict == "wrong")
        )
        assert wrong == 29
        # Quota / licence / switch tickets are fleet-wide, so node_id is NULL.
        fleet_wide = await db.scalar(
            select(func.count()).select_from(Alert).where(Alert.node_id.is_(None))
        )
        assert fleet_wide == 60


async def _fresh_factory() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    return engine, factory


async def _dump(factory: async_sessionmaker[AsyncSession]) -> dict[str, list[tuple]]:
    async with factory() as db:
        nodes = (await db.scalars(select(IncidentNode).order_by(IncidentNode.id))).all()
        alerts = (await db.scalars(select(Alert).order_by(Alert.id))).all()
        runs = (await db.scalars(select(DiagnosisRun).order_by(DiagnosisRun.id))).all()
        steps = (await db.scalars(select(DiagnosisStep).order_by(DiagnosisStep.id))).all()
        reviews = (await db.scalars(select(DiagnosisReview).order_by(DiagnosisReview.id))).all()
        return {
            "nodes": [
                (
                    n.id,
                    n.hostname,
                    n.switch,
                    n.rack,
                    n.status,
                    n.schedulable,
                    n.heartbeat_at,
                    n.tags,
                )
                for n in nodes
            ],
            "alerts": [
                (a.id, a.node_id, a.script_key, a.severity, a.summary, a.fired_at, a.created_at)
                for a in alerts
            ],
            "runs": [
                (
                    r.id,
                    r.alert_id,
                    r.trace_id,
                    r.root_cause,
                    r.confidence,
                    r.tokens,
                    r.duration_ms,
                    r.created_at,
                    r.updated_at,
                )
                for r in runs
            ],
            "steps": [
                (s.run_id, s.seq, s.node, s.tool_name, s.args, s.result, s.latency_ms, s.created_at)
                for s in steps
            ],
            "reviews": [(v.run_id, v.verdict, v.corrected_cause) for v in reviews],
        }


async def test_two_seeds_write_identical_rows() -> None:
    dumps: list[dict[str, list[tuple]]] = []
    engines = []
    for _ in range(2):
        engine, factory = await _fresh_factory()
        engines.append(engine)
        async with factory() as db:
            await seed_diagnosis_data(db)
            await db.commit()
        dumps.append(await _dump(factory))
    for engine in engines:
        await engine.dispose()

    assert dumps[0] == dumps[1]
    assert len(dumps[0]["nodes"]) == NODE_COUNT
    assert len(dumps[0]["alerts"]) == TICKET_COUNT
