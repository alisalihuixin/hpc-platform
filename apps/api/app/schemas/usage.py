from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class UsageEventRead(BaseModel):
    """One usage row; ``signed_gpu_hours`` is negative for reversal rows (F5.1)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    user_id: int
    group_id: int
    gpus: int
    started_at: datetime
    ended_at: datetime
    duration_min: int
    gpu_hours: Decimal
    signed_gpu_hours: Decimal
    rate_snapshot: dict[str, Any]
    reversed: bool
    created_at: datetime


class UserUsage(BaseModel):
    """Personal usage detail (F5.3); ``sum(items.signed_gpu_hours) == total``."""

    items: list[UsageEventRead]
    total: int
    total_gpu_hours: Decimal


class UserUsageRank(BaseModel):
    user_id: int
    username: str | None
    gpu_hours: Decimal
    job_count: int


class GroupUsageRanking(BaseModel):
    """Per-member usage ranking (F5.4)."""

    group_id: int
    group_name: str | None
    total_gpu_hours: Decimal
    items: list[UserUsageRank]
