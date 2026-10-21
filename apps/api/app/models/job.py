"""Jobs (F4.1) - the core entity of the platform.

State machine (enforced by ``UPDATE ... WHERE status IN (...)`` in the service
layer, and backed by a CHECK constraint here)::

    queued ──> running ──> completed
       │          │
       │          └──────> failed
       └──────> canceled <──┘

Queue position is not stored: it is ``ORDER BY (queued_at, id)`` among queued
jobs, which keeps FIFO ordering deterministic without an extra counter column.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONDict


class JobStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED})

#: Statuses a user may still cancel; anything else is already final (F4.4).
CANCELABLE_STATUSES = frozenset({JobStatus.QUEUED, JobStatus.RUNNING})

#: Legal transitions; anything else is a bug and must be rejected.
ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELED}),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELED: frozenset(),
}


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'canceled')",
            name="ck_jobs_status",
        ),
        CheckConstraint("num_nodes > 0 AND num_gpus >= 0", name="ck_jobs_resources"),
        CheckConstraint("num_cpus >= 0 AND mem_gb >= 0", name="ck_jobs_cpu_mem"),
        CheckConstraint("walltime_min > 0", name="ck_jobs_walltime"),
        # Feeds the scheduler's "next queued job" scan.
        Index("ix_jobs_status_queued_at", "status", "queued_at"),
        # Feeds the per-user job history page.
        Index("ix_jobs_user_created_at", "user_id", "created_at"),
        # Feeds the orphan reaper (running jobs with expired leases).
        Index("ix_jobs_status_lease", "status", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # No ON DELETE CASCADE: job rows are billing/audit evidence and must outlive
    # the account or group they belong to.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)

    name: Mapped[str] = mapped_column(String(255))
    num_nodes: Mapped[int] = mapped_column(Integer)
    num_gpus: Mapped[int] = mapped_column(Integer, default=0)
    # CPU cores and memory are per-node slot requests (placement is single-node).
    num_cpus: Mapped[int] = mapped_column(Integer, default=0)
    mem_gb: Mapped[int] = mapped_column(Integer, default=0)
    # Higher runs first under the priority policy; FIFO ignores it.
    priority: Mapped[int] = mapped_column(Integer, default=0)
    walltime_min: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED.value)

    # Cleared if the node is removed; the billing truth lives in usage_events.
    node_id: Mapped[int | None] = mapped_column(
        ForeignKey("nodes.id", ondelete="SET NULL"), default=None
    )
    # Renewed every scheduler cycle; an expired lease means the worker died and
    # the job is reclaimed by ``recover_orphaned_jobs`` (F4.3).
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def is_terminal(self) -> bool:
        return JobStatus(self.status) in TERMINAL_STATUSES

    @property
    def can_be_canceled(self) -> bool:
        return self.status in (JobStatus.QUEUED.value, JobStatus.RUNNING.value)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Job {self.id} {self.name} {self.status}>"
