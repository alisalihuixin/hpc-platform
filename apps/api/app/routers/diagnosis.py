"""Diagnosis endpoints (D49): alert intake + run records.

Executing the agent is M6's job - these endpoints persist the two records the
graph will later write into, and let the UI poll a run by id.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.deps import CurrentUser, SessionDep
from app.schemas.diagnosis import (
    AlertCreate,
    AlertRead,
    DiagnosisCreate,
    DiagnosisRead,
    DiagnosisStepRead,
)
from app.services import diagnosis_service

router = APIRouter(tags=["diagnosis"])


@router.post(
    "/alerts",
    response_model=AlertRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record an incoming alert",
)
async def create_alert(payload: AlertCreate, db: SessionDep, user: CurrentUser) -> AlertRead:
    alert = await diagnosis_service.create_alert(db, payload)
    return AlertRead.model_validate(alert)


@router.post(
    "/diagnosis",
    response_model=DiagnosisRead,
    status_code=status.HTTP_201_CREATED,
    summary="Open a diagnosis run for an alert",
)
async def start_diagnosis(
    payload: DiagnosisCreate, db: SessionDep, user: CurrentUser
) -> DiagnosisRead:
    run = await diagnosis_service.start_diagnosis(db, payload)
    return DiagnosisRead.model_validate(run)


@router.get("/diagnosis/{run_id}", response_model=DiagnosisRead, summary="Get a run + evidence")
async def get_diagnosis(run_id: int, db: SessionDep, user: CurrentUser) -> DiagnosisRead:
    run = await diagnosis_service.get_run(db, run_id)
    steps = await diagnosis_service.list_steps(db, run_id)
    return DiagnosisRead.model_validate(run).model_copy(
        update={"steps": [DiagnosisStepRead.model_validate(step) for step in steps]}
    )
