"""Resource quota, one row per group (F3.1).

``gpu_limit`` / ``gpu_used`` form the pooled GPU budget the group may reserve
against; the remaining columns bound a single job.  The CHECK constraint is the
last line of defence behind the atomic reservation in
``app.services.quota_service`` - it guarantees ``gpu_used`` can never overshoot
even if an application bug slips through.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import JSONDict

if TYPE_CHECKING:
    from app.models.group import Group


class Quota(Base):
    __tablename__ = "quotas"
    __table_args__ = (
        CheckConstraint("gpu_used >= 0 AND gpu_used <= gpu_limit", name="ck_quotas_gpu_used"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), unique=True, index=True
    )

    # Pooled GPU budget for the whole group.
    gpu_limit: Mapped[int] = mapped_column(Integer, default=0)
    gpu_used: Mapped[int] = mapped_column(Integer, default=0)

    # Per-job ceilings.
    max_nodes_per_job: Mapped[int] = mapped_column(Integer, default=1)
    max_gpus_per_job: Mapped[int] = mapped_column(Integer, default=1)

    # Extensible resource dimensions (kept in JSONB so new ones need no migration).
    detail: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    group: Mapped[Group] = relationship(back_populates="quota", lazy="selectin")

    @property
    def gpu_available(self) -> int:
        return max(self.gpu_limit - self.gpu_used, 0)

    @property
    def group_name(self) -> str | None:
        return self.group.name if self.group is not None else None

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Quota group={self.group_id} {self.gpu_used}/{self.gpu_limit}>"
