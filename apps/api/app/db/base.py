"""Declarative base + shared association tables."""

from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Table
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Root declarative class shared by every model."""


# users <-> roles many-to-many.
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

# users <-> groups many-to-many (group membership, F3 / F8.2).
user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)
