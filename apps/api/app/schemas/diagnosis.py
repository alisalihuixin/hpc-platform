"""Alert intake + diagnosis-run payloads (D49)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.diagnosis import AlertSeverity, AlertStatus, RunStatus, StepPhase


class AlertCreate(BaseModel):
    """One alert posted by monitoring (or by a test/curl session)."""

    node_id: int | None = Field(default=None, gt=0, examples=[3])
    script_key: str = Field(min_length=1, max_length=64, examples=["gpu-ecc-01"])
    severity: AlertSeverity = AlertSeverity.WARNING
    summary: str = Field(
        min_length=1,
        max_length=512,
        examples=["GPU2 ECC error count rising on gpu-003"],
    )
    #: Omitted -> server time; lets a replay keep the original fire time.
    fired_at: datetime | None = None


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: int | None
    script_key: str
    severity: AlertSeverity
    summary: str
    status: AlertStatus
    fired_at: datetime
    created_at: datetime


class DiagnosisCreate(BaseModel):
    alert_id: int = Field(gt=0, examples=[1])


class DiagnosisStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    seq: int
    node: StepPhase
    tool_name: str | None
    args: dict[str, Any] | None
    result: dict[str, Any] | None
    latency_ms: int
    created_at: datetime


class DiagnosisRead(BaseModel):
    """A run and its evidence chain (empty until the agent fills it in M6)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: int
    batch_id: str | None
    status: RunStatus
    root_cause: str | None
    confidence: float | None
    iterations: int
    tokens: int
    trace_id: str | None
    duration_ms: int | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    steps: list[DiagnosisStepRead] = Field(default_factory=list)


class DiagnosisListItem(BaseModel):
    """One row of the diagnosis list (D55); flattens the triggering alert."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: int
    status: RunStatus
    alert_summary: str
    alert_severity: AlertSeverity
    root_cause: str | None
    confidence: float | None
    iterations: int
    tokens: int
    duration_ms: int | None
    created_at: datetime


class DiagnosisList(BaseModel):
    items: list[DiagnosisListItem]
    total: int
