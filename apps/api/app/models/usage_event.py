"""GPU billing events (F5.1).

The table is **append-only**: a finished job inserts one row and rows are never
edited.  Corrections are compensating rows flagged with ``reversed`` (D23).

D22 writes the event when a running job reaches a terminal state, using the same
per-minute ceiling rule as the job detail page (F5.2)::

    duration_min = ceil(elapsed_seconds / 60)
    gpu_hours    = gpus * duration_min / 60
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONDict


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint("gpus >= 0", name="ck_usage_gpus"),
        CheckConstraint("duration_min >= 0", name="ck_usage_duration"),
        CheckConstraint("gpu_hours >= 0", name="ck_usage_gpu_hours"),
        # Feed the per-group and per-user usage pages (F5.3 / F5.4).
        Index("ix_usage_group_started", "group_id", "started_at"),
        Index("ix_usage_user_started", "user_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Billing evidence must survive: no ON DELETE CASCADE anywhere.
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))

    gpus: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Rounded-up minutes, so the row reconciles with ``gpu_minutes`` in F4.6.
    duration_min: Mapped[int] = mapped_column(Integer)
    gpu_hours: Mapped[Decimal] = mapped_column(Numeric(12, 4))

    # Rates in force when the job ran (D23 fills this in).
    rate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # Set on compensating rows; the original row is never mutated.
    reversed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<UsageEvent job={self.job_id} {self.gpus}gpu {self.gpu_hours}h>"
