"""Diagnosis-domain schema (D41): table registration + constraint coverage.

The migration is exercised separately by ``alembic upgrade head``; here we pin
the contract the rest of project 2 builds on - the seven tables exist and their
CHECK/UNIQUE constraints actually reject bad rows.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    Alert,
    DiagnosisReview,
    DiagnosisRun,
    DiagnosisStep,
    EvalCase,
    IncidentNode,
    Runbook,
)

EXPECTED_TABLES = {
    "incident_nodes",
    "alerts",
    "diagnosis_runs",
    "diagnosis_steps",
    "diagnosis_reviews",
    "runbooks",
    "eval_cases",
}


def test_diagnosis_tables_registered() -> None:
    from app.db.base import Base

    assert set(Base.metadata.tables) >= EXPECTED_TABLES


def test_diagnosis_package_layout_imports() -> None:
    import app.diagnosis as diagnosis
    from app.diagnosis import data, eval, graph, tools

    assert diagnosis.__name__ == "app.diagnosis"
    assert [module.__name__ for module in (data, eval, graph, tools)] == [
        "app.diagnosis.data",
        "app.diagnosis.eval",
        "app.diagnosis.graph",
        "app.diagnosis.tools",
    ]


async def test_full_evidence_chain_round_trip(session_factory) -> None:
    async with session_factory() as db:
        node = IncidentNode(
            hostname="gpu-001",
            node_model="DGX-A100",
            switch="sw-01",
            rack="rack-a",
            gpu_model="A100-80G",
            gpu_count=8,
        )
        db.add(node)
        await db.flush()

        alert = Alert(node_id=node.id, script_key="gpu_ecc", summary="GPU ECC errors")
        db.add(alert)
        await db.flush()

        run = DiagnosisRun(alert_id=alert.id, status="running", batch_id="batch-1")
        db.add(run)
        await db.flush()

        db.add_all(
            [
                DiagnosisStep(
                    run_id=run.id, seq=1, node="plan", result={"plan": ["get_node_gpus"]}
                ),
                DiagnosisStep(
                    run_id=run.id,
                    seq=2,
                    node="tool",
                    tool_name="get_node_gpus",
                    args={"hostname": "gpu-001"},
                    result={"ecc_errors": 12},
                    latency_ms=3,
                ),
            ]
        )
        db.add(DiagnosisReview(run_id=run.id, verdict="correct"))
        db.add(Runbook(title="GPU ECC 处置", body="...", tags="gpu,ecc"))
        db.add(
            EvalCase(
                script_key="gpu_ecc",
                expected_cause="GPU ECC 错误",
                trap=False,
                expected_evidence=[{"tool": "get_node_gpus", "hostname": "gpu-001"}],
            )
        )
        await db.commit()

    async with session_factory() as db:
        steps = (
            await db.scalars(
                select(DiagnosisStep).where(DiagnosisStep.run_id == run.id).order_by("seq")
            )
        ).all()
        assert [step.node for step in steps] == ["plan", "tool"]
        assert steps[1].args == {"hostname": "gpu-001"}
        review = await db.scalar(select(DiagnosisReview).where(DiagnosisReview.run_id == run.id))
        assert review is not None and review.verdict == "correct"


async def test_run_status_check_rejects_unknown_value(session_factory) -> None:
    async with session_factory() as db:
        alert = Alert(script_key="x", summary="x")
        db.add(alert)
        await db.flush()
        db.add(DiagnosisRun(alert_id=alert.id, status="exploded"))
        with pytest.raises(IntegrityError):
            await db.flush()


async def test_confidence_must_be_normalised(session_factory) -> None:
    async with session_factory() as db:
        alert = Alert(script_key="x", summary="x")
        db.add(alert)
        await db.flush()
        db.add(DiagnosisRun(alert_id=alert.id, confidence=1.5))
        with pytest.raises(IntegrityError):
            await db.flush()


async def test_step_seq_is_unique_per_run(session_factory) -> None:
    async with session_factory() as db:
        alert = Alert(script_key="x", summary="x")
        db.add(alert)
        await db.flush()
        run = DiagnosisRun(alert_id=alert.id)
        db.add(run)
        await db.flush()
        db.add_all(
            [
                DiagnosisStep(run_id=run.id, seq=1, node="plan"),
                DiagnosisStep(run_id=run.id, seq=1, node="reflect"),
            ]
        )
        with pytest.raises(IntegrityError):
            await db.flush()


async def test_eval_case_script_key_is_unique(session_factory) -> None:
    async with session_factory() as db:
        db.add_all(
            [
                EvalCase(script_key="oom", expected_cause="显存超限"),
                EvalCase(script_key="oom", expected_cause="重复"),
            ]
        )
        with pytest.raises(IntegrityError):
            await db.flush()
