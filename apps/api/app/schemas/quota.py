from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QuotaUpdate(BaseModel):
    """Admin adjustment of a group's ceiling (F3.4).

    ``gpu_used`` is deliberately absent - the pool is only moved by the
    reservation/release path, never by hand.  Lowering ``gpu_limit`` never kills
    running jobs; it only constrains future submissions.
    """

    gpu_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    max_nodes_per_job: int | None = Field(default=None, ge=0, le=4096)
    max_gpus_per_job: int | None = Field(default=None, ge=0, le=4096)
    detail: dict[str, Any] | None = None


class QuotaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    group_name: str | None
    gpu_limit: int
    gpu_used: int
    gpu_available: int
    max_nodes_per_job: int
    max_gpus_per_job: int
    detail: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class QuotaList(BaseModel):
    items: list[QuotaRead]
    total: int
