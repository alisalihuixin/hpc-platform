"""Compute nodes (F2.1).

``gpu_used`` is maintained by the scheduler once jobs exist (M2 D21+); here it is
only ever adjusted by an administrator, and a CHECK constraint keeps it within
``[0, gpu_total]``.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# JSONB on PostgreSQL (indexable, per the ERD) with a JSON fallback so the same
# metadata also builds under the SQLite used by the test suite.
JSONDict = JSONB().with_variant(JSON(), "sqlite")


class NodeStatus(enum.StrEnum):
    """Lifecycle of a node.

    ``draining`` stops new jobs from being scheduled onto the node while its
    running jobs are allowed to finish.
    """

    ONLINE = "online"
    OFFLINE = "offline"
    DRAINING = "draining"


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        CheckConstraint("status IN ('online', 'offline', 'draining')", name="ck_nodes_status"),
        CheckConstraint("gpu_used >= 0 AND gpu_used <= gpu_total", name="ck_nodes_gpu_used"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    cpu_cores: Mapped[int] = mapped_column(Integer)
    mem_gb: Mapped[int] = mapped_column(Integer)
    gpu_model: Mapped[str | None] = mapped_column(String(128), default=None)
    gpu_total: Mapped[int] = mapped_column(Integer, default=0)
    gpu_used: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=NodeStatus.ONLINE.value, index=True)
    tags: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

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
    def gpu_free(self) -> int:
        """Idle GPU count - surfaced by the read-only resource view (F2.3)."""
        return max(self.gpu_total - self.gpu_used, 0)

    @property
    def accepts_jobs(self) -> bool:
        """Only ``online`` nodes may receive new work (F2.2)."""
        return self.status == NodeStatus.ONLINE.value

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Node {self.hostname} {self.status}>"
