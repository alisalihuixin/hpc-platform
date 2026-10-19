"""Job endpoints (F4.2 / F4.4 / F4.5).

Submitting and listing are open to any signed-in user; a non-admin only sees
their own jobs.  Cancellation is allowed for the owner or an admin.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.deps import CurrentUser, SessionDep
from app.models.job import JobStatus
from app.schemas.job import JobCreate, JobDetail, JobList, JobRead
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


@router.get("", response_model=JobList, summary="List jobs (admin sees all)")
async def list_jobs(
    db: SessionDep,
    user: CurrentUser,
    status_filter: Annotated[
        JobStatus | None, Query(alias="status", description="Filter by job status")
    ] = None,
    since: Annotated[
        datetime | None, Query(description="Only jobs queued at or after this time")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Only jobs queued at or before this time")
    ] = None,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobList:
    jobs, total = await job_service.list_jobs(
        db,
        user,
        status=status_filter.value if status_filter else None,
        since=since,
        until=until,
        skip=skip,
        limit=limit,
    )
    return JobList(items=[JobRead.model_validate(job) for job in jobs], total=total)


@router.get("/{job_id}", response_model=JobDetail, summary="Get a job's detail")
async def get_job_detail(job_id: int, db: SessionDep, user: CurrentUser) -> JobDetail:
    job = await job_service.get_visible_job(db, user, job_id)
    return JobDetail(
        **JobRead.model_validate(job).model_dump(),
        gpu_minutes=job_service.used_gpu_minutes(job),
        log=job_service.build_log(job),
    )


@router.post("/{job_id}/cancel", response_model=JobRead, summary="Cancel a job")
async def cancel_job(job_id: int, db: SessionDep, user: CurrentUser) -> JobRead:
    job = await job_service.cancel_job(db, user, job_id)
    return JobRead.model_validate(job)
