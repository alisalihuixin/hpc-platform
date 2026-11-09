from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class MonthlyBillingRow(BaseModel):
    """One month x user x group bucket (F6.1)."""

    model_config = ConfigDict(from_attributes=True)

    month: str
    user_id: int
    username: str | None
    group_id: int
    group_name: str | None
    gpu_hours: Decimal
    job_count: int


class MonthlyBillingReport(BaseModel):
    items: list[MonthlyBillingRow]
    total_gpu_hours: Decimal


class RefreshResult(BaseModel):
    refreshed: bool
    detail: str
