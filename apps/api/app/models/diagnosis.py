"""Cluster fault-diagnosis domain (project 2, F1/F3/F7).

Seven tables, deliberately independent of the billing domain: ``incident_nodes``
is a *mock* fleet injected by the fault scripts, so a diagnostic experiment can
never move a real node's ``gpu_used`` counter or leave an audit trail on the
billing side.

Flow: ``alerts`` (case intake) -> ``diagnosis_runs`` (one investigation) ->
``diagnosis_steps`` (evidence chain, one row per graph node) ->
``diagnosis_reviews`` (human verdict).  ``runbooks`` feed ``search_runbooks`` and
``eval_cases`` hold ground truth, kept separate from ``alerts`` so the same
script can be replayed without touching production alert rows.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONDict


class NodeHealth(enum.StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class AlertSeverity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(enum.StrEnum):
    OPEN = "open"
    DIAGNOSING = "diagnosing"
    RESOLVED = "resolved"


class RunStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    #: Budget / iteration exhausted; a rule-based conclusion was still produced.
    DEGRADED = "degraded"
    FAILED = "failed"


class StepPhase(enum.StrEnum):
    PLAN = "plan"
    TOOL = "tool"
    OBSERVE = "observe"
    REFLECT = "reflect"
    REPORT = "report"
    #: Rule-only conclusion written when the agent runs out of budget/steps.
    FALLBACK = "fallback"


class ReviewVerdict(enum.StrEnum):
    CORRECT = "correct"
    WRONG = "wrong"


class IncidentNode(Base):
    """Mock compute node of the incident fleet (never billed)."""

    __tablename__ = "incident_nodes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('online', 'degraded', 'offline')",
            name="ck_incident_nodes_status",
        ),
        CheckConstraint("gpu_count >= 0", name="ck_incident_nodes_gpus"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    node_model: Mapped[str] = mapped_column(String(128))
    #: Aggregation key for the M7 correlation ("5 nodes on the same switch").
    switch: Mapped[str] = mapped_column(String(64), index=True)
    rack: Mapped[str] = mapped_column(String(64), index=True)
    gpu_model: Mapped[str | None] = mapped_column(String(64), default=None)
    gpu_count: Mapped[int] = mapped_column(Integer, default=8)
    status: Mapped[str] = mapped_column(String(16), default=NodeHealth.ONLINE.value, index=True)
    #: ``False`` is the ground truth behind script #9 (scheduler refuses the node).
    schedulable: Mapped[bool] = mapped_column(default=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    tags: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<IncidentNode {self.hostname} {self.status}>"


class Alert(Base):
    """One incoming alert; ``script_key`` ties it to a fault script."""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_alerts_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'diagnosing', 'resolved')",
            name="ck_alerts_status",
        ),
        Index("ix_alerts_status_fired_at", "status", "fired_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Nullable: quota/licence scripts are not tied to one node.
    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("incident_nodes.id", ondelete="SET NULL"), default=None, index=True
    )
    script_key: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default=AlertSeverity.WARNING.value)
    summary: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default=AlertStatus.OPEN.value)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Alert {self.id} {self.script_key} {self.status}>"


class DiagnosisRun(Base):
    """One investigation of one alert (or one child of a batch)."""

    __tablename__ = "diagnosis_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'degraded', 'failed')",
            name="ck_diagnosis_runs_status",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_diagnosis_runs_confidence",
        ),
        CheckConstraint("iterations >= 0 AND tokens >= 0", name="ck_diagnosis_runs_counters"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    #: Groups the parallel children of one batch (M7); NULL for single runs.
    batch_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.PENDING.value, index=True)
    root_cause: Mapped[str | None] = mapped_column(String(255), default=None)
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    iterations: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    #: Langfuse trace id, so a report page can deep-link to the full trace (F4).
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    error: Mapped[str | None] = mapped_column(String(512), default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<DiagnosisRun {self.id} alert={self.alert_id} {self.status}>"


class DiagnosisStep(Base):
    """One row of the evidence chain - the audit trail of the agent's reasoning."""

    __tablename__ = "diagnosis_steps"
    __table_args__ = (
        CheckConstraint(
            "node IN ('plan', 'tool', 'observe', 'reflect', 'report', 'fallback')",
            name="ck_diagnosis_steps_node",
        ),
        CheckConstraint("seq > 0 AND latency_ms >= 0", name="ck_diagnosis_steps_seq"),
        # Ordered replay of one run; also stops a retried step from duplicating.
        UniqueConstraint("run_id", "seq", name="uq_diagnosis_steps_run_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("diagnosis_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(16))
    tool_name: Mapped[str | None] = mapped_column(String(64), default=None)
    args: Mapped[dict[str, Any] | None] = mapped_column(JSONDict, default=None)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONDict, default=None)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<DiagnosisStep run={self.run_id} seq={self.seq} {self.node}>"


class DiagnosisReview(Base):
    """Human verdict - overwrites the model's self-assessment in eval (F7)."""

    __tablename__ = "diagnosis_reviews"
    __table_args__ = (
        CheckConstraint("verdict IN ('correct', 'wrong')", name="ck_diagnosis_reviews_verdict"),
        # One verdict per run; a re-review updates the row.
        UniqueConstraint("run_id", name="uq_diagnosis_reviews_run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("diagnosis_runs.id", ondelete="CASCADE"), index=True
    )
    verdict: Mapped[str] = mapped_column(String(16))
    #: Mandatory when ``verdict='wrong'`` - enforced in the API layer.
    corrected_cause: Mapped[str | None] = mapped_column(String(255), default=None)
    reviewer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<DiagnosisReview run={self.run_id} {self.verdict}>"


class Runbook(Base):
    """Knowledge base behind ``search_runbooks`` (RAG upgrade in M8)."""

    __tablename__ = "runbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    body: Mapped[str] = mapped_column(Text)
    #: Comma-separated keywords; matched with ILIKE by the tool layer.
    tags: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Runbook {self.id} {self.title}>"


class EvalCase(Base):
    """Ground truth for the 20-case evaluation set (F5).

    Decoupled from ``alerts`` on purpose: alerts are incident records, eval cases
    are the fixed benchmark replayed by ``scripts/eval_diagnosis.py``.
    """

    __tablename__ = "eval_cases"
    __table_args__ = (UniqueConstraint("script_key", name="uq_eval_cases_script_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    script_key: Mapped[str] = mapped_column(String(64), index=True)
    expected_cause: Mapped[str] = mapped_column(String(255))
    #: The five trap scripts (7/12/17/18/20) where the first alert misleads.
    trap: Mapped[bool] = mapped_column(default=False)
    #: Tools + arguments an agent must consult to reach the true cause.
    expected_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONDict, default=list)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<EvalCase {self.script_key} trap={self.trap}>"
