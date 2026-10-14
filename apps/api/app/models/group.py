"""User groups (F3 / F8.2).

A group owns exactly one quota row (1:1) which bounds the pooled resources its
members may consume.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, user_groups

if TYPE_CHECKING:
    from app.models.quota import Quota
    from app.models.user import User


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), default=None)

    # Groups are soft-deleted: a group referenced by jobs/usage rows must not be
    # hard-deleted (F8.2).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    members: Mapped[list[User]] = relationship(
        secondary=user_groups,
        lazy="selectin",
    )

    quota: Mapped[Quota | None] = relationship(
        back_populates="group",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Group {self.name}>"
