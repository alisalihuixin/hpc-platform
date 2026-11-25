"""Alert intake and diagnosis-run records (D49).

The endpoints only persist here: an alert is recorded and a ``pending`` run is
opened so the M6 graph has an addressable id to stream into.  Keeping the
intake cheap also means a duplicate alert cannot start two investigations - an
alert that is already ``diagnosing`` is rejected with 409.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models.diagnosis import (
    Alert,
    AlertStatus,
    DiagnosisRun,
    DiagnosisStep,
    IncidentNode,
    RunStatus,
)
from app.schemas.diagnosis import AlertCreate, DiagnosisCreate


async def create_alert(db: AsyncSession, payload: AlertCreate) -> Alert:
    """Record an alert; a ``node_id`` must point at a known incident node."""
    if payload.node_id is not None and await db.get(IncidentNode, payload.node_id) is None:
        raise NotFoundError("Incident node not found")

    alert = Alert(
        node_id=payload.node_id,
        script_key=payload.script_key,
        severity=payload.severity.value,
        summary=payload.summary,
        status=AlertStatus.OPEN.value,
        fired_at=payload.fired_at or datetime.now(UTC),
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return alert


async def start_diagnosis(db: AsyncSession, payload: DiagnosisCreate) -> DiagnosisRun:
    """Open a pending run for the alert and mark the alert as diagnosing."""
    alert = await db.get(Alert, payload.alert_id)
    if alert is None:
        raise NotFoundError("Alert not found")
    if alert.status == AlertStatus.DIAGNOSING.value:
        raise ConflictError("Alert is already being diagnosed")

    run = DiagnosisRun(alert_id=alert.id, status=RunStatus.PENDING.value)
    alert.status = AlertStatus.DIAGNOSING.value
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def get_run(db: AsyncSession, run_id: int) -> DiagnosisRun:
    run = await db.get(DiagnosisRun, run_id)
    if run is None:
        raise NotFoundError("Diagnosis run not found")
    return run


async def list_steps(db: AsyncSession, run_id: int) -> list[DiagnosisStep]:
    """Evidence chain in replay order (``seq`` is unique per run)."""
    result = await db.execute(
        select(DiagnosisStep).where(DiagnosisStep.run_id == run_id).order_by(DiagnosisStep.seq)
    )
    return list(result.scalars())


async def list_runs(
    db: AsyncSession,
    *,
    status: RunStatus | None = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[tuple[DiagnosisRun, Alert]], int]:
    """Runs newest first, paired with the alert that triggered them (D55)."""
    filters = [DiagnosisRun.status == status.value] if status is not None else []

    result = await db.execute(
        select(DiagnosisRun, Alert)
        .join(Alert, DiagnosisRun.alert_id == Alert.id)
        .where(*filters)
        .order_by(DiagnosisRun.created_at.desc(), DiagnosisRun.id.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = [(run, alert) for run, alert in result.all()]

    total = await db.scalar(select(func.count()).select_from(DiagnosisRun).where(*filters))
    return rows, int(total or 0)
