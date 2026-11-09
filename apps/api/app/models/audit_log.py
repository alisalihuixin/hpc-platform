"""Audit trail (F7.1).

Rows are written by **database triggers** (see ``app/db/audit_triggers.py``), not
by application code: a missed ``db.commit()`` can therefore never skip the audit
row. The application only reads this table (``/audit-logs``).

``hashed_password`` is always stripped before persisting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONDict


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_table_record", "table_name", "record_id"),
        Index("ix_audit_changed_at", "changed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    record_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: ``update`` or ``delete``.
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ``app.user_id`` of the request; ``system`` for worker/background writes.
    changed_by: Mapped[str | None] = mapped_column(String(64), default=None)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONDict, default=None)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONDict, default=None)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<AuditLog {self.table_name}#{self.record_id} {self.action}>"
