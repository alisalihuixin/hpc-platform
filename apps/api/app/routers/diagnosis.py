"""Diagnosis endpoints (D49/D54): alert intake, run records and the live stream.

Opening a diagnosis only records intent; the graph runs when a viewer subscribes
to ``/diagnosis/{id}/stream``, and every graph node is persisted as one
``diagnosis_steps`` row before its SSE frame is sent.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from app.deps import CurrentUser, DiagnosisLLMDep, SessionDep
from app.schemas.diagnosis import (
    AlertCreate,
    AlertRead,
    DiagnosisCreate,
    DiagnosisRead,
    DiagnosisStepRead,
)
from app.services import diagnosis_service, diagnosis_stream

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


@router.get("/diagnosis/{run_id}/stream", summary="Diagnosis progress stream (SSE)")
async def stream_diagnosis_run(
    run_id: int, db: SessionDep, user: CurrentUser, llm: DiagnosisLLMDep
) -> StreamingResponse:
    """Execute a pending run and stream every step as it is persisted.

    A terminal run is replayed from ``diagnosis_steps`` instead of spending
    another model call, so a page refresh cannot restart the investigation.
    """
    run = await diagnosis_service.get_run(db, run_id)
    return StreamingResponse(
        diagnosis_stream.stream_diagnosis(db, run, llm=llm),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
