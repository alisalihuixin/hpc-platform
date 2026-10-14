from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus

#: ``params`` and per-job resource ceilings are validated twice: statically here
#: (hard upper bounds) and dynamically against the group's quota in
#: ``app.services.job_service`` (which knows the per-group ceilings).
MAX_NODES = 4096
MAX_GPUS_PER_JOB = 4096
MAX_WALLTIME_MIN = 7 * 24 * 60  # one week


class JobCreate(BaseModel):
    """A job submission (F4.2).

    Only the *absolute* limits are enforced here so the request fails with a
    field-level 422 before any DB work; the group-specific ceilings
    (``max_nodes_per_job`` / ``max_gpus_per_job``) are enforced in the service,
    where the quota is loaded.
    """

    group_id: int = Field(gt=0, examples=[1])
    name: str = Field(min_length=1, max_length=255, examples=["train-resnet"])
    num_nodes: int = Field(ge=1, le=MAX_NODES, examples=[1])
    num_gpus: int = Field(default=0, ge=0, le=MAX_GPUS_PER_JOB, examples=[4])
    walltime_min: int = Field(ge=1, le=MAX_WALLTIME_MIN, examples=[120])
    params: dict[str, Any] = Field(default_factory=dict, examples=[{"image": "pytorch:2.4"}])


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    group_id: int
    name: str
    num_nodes: int
    num_gpus: int
    walltime_min: int
    params: dict[str, Any]
    status: JobStatus
    node_id: int | None
    queued_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JobList(BaseModel):
    items: list[JobRead]
    total: int
