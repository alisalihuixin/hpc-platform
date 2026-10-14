"""Job endpoints (F4.2).

Submitting a job (``POST /jobs``) is open to any signed-in user, but only for
groups they belong to.  List/cancel/detail arrive on D19/D20.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.deps import CurrentUser, SessionDep
from app.schemas.job import JobCreate, JobRead
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a job (validate + reserve quota + enqueue)",
)
async def submit_job(payload: JobCreate, db: SessionDep, user: CurrentUser) -> JobRead:
    job = await job_service.submit_job(db, user, payload)
    return JobRead.model_validate(job)
