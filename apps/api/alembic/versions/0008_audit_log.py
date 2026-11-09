"""audit log + triggers (F7.1)

Revision ID: 0008_audit_log
Revises: 0007_monthly_billing
Create Date: 2026-11-09

Creates ``audit_log`` and installs the UPDATE/DELETE triggers for
``users`` / ``quotas`` / ``nodes`` / ``jobs``.  The trigger DDL lives in
``app/db/audit_triggers.py`` so the pytest bootstrap can install the exact same
statements on its in-memory SQLite database.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db import audit_triggers
from app.db.types import JSONDict

revision: str = "0008_audit_log"
down_revision: str | None = "0007_monthly_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("record_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("changed_by", sa.String(length=64), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("before", JSONDict, nullable=True),
        sa.Column("after", JSONDict, nullable=True),
    )
    op.create_index("ix_audit_table_record", "audit_log", ["table_name", "record_id"])
    op.create_index("ix_audit_changed_at", "audit_log", ["changed_at"])

    for statement in audit_triggers.statements(op.get_bind().dialect.name):
        op.execute(statement)


def downgrade() -> None:
    for statement in audit_triggers.drop_statements(op.get_bind().dialect.name):
        op.execute(statement)
    op.drop_index("ix_audit_changed_at", table_name="audit_log")
    op.drop_index("ix_audit_table_record", table_name="audit_log")
    op.drop_table("audit_log")
