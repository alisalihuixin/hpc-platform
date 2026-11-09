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


class DashboardTrendPoint(BaseModel):
    month: str
    gpu_hours: Decimal


class DashboardTopEntry(BaseModel):
    id: int
    name: str | None
    gpu_hours: Decimal


class DashboardTotals(BaseModel):
    running_jobs: int
    queued_jobs: int
    online_nodes: int
    total_nodes: int
    gpu_used: int
    gpu_total: int


class DashboardReport(BaseModel):
    """Platform overview for the admin dashboard (D39)."""

    totals: DashboardTotals
    trend: list[DashboardTrendPoint]
    top_users: list[DashboardTopEntry]
    top_groups: list[DashboardTopEntry]
