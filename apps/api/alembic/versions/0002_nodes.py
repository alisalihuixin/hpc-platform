"""nodes table (F2.1)

Revision ID: 0002_nodes
Revises: 0001_initial
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_nodes"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on PostgreSQL, JSON on SQLite - keeps `alembic upgrade` runnable in both.
TAGS_TYPE = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("cpu_cores", sa.Integer(), nullable=False),
        sa.Column("mem_gb", sa.Integer(), nullable=False),
        sa.Column("gpu_model", sa.String(length=128), nullable=True),
        sa.Column("gpu_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gpu_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="online"),
        sa.Column("tags", TAGS_TYPE, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('online', 'offline', 'draining')", name="ck_nodes_status"
        ),
        sa.CheckConstraint("gpu_used >= 0 AND gpu_used <= gpu_total", name="ck_nodes_gpu_used"),
    )
    op.create_index("ix_nodes_hostname", "nodes", ["hostname"], unique=True)
    op.create_index("ix_nodes_status", "nodes", ["status"])


def downgrade() -> None:
    op.drop_index("ix_nodes_status", table_name="nodes")
    op.drop_index("ix_nodes_hostname", table_name="nodes")
    op.drop_table("nodes")
