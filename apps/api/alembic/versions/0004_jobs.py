"""jobs table (F4.1)

Revision ID: 0004_jobs
Revises: 0003_groups_quotas
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_jobs"
down_revision: str | None = "0003_groups_quotas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_DICT = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        # No ON DELETE CASCADE on purpose - job rows are billing/audit evidence.
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("num_nodes", sa.Integer(), nullable=False),
        sa.Column("num_gpus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("walltime_min", sa.Integer(), nullable=False),
        sa.Column("params", JSONB_DICT, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('queued', 'running', 'completed', 'failed', 'canceled')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint("num_nodes > 0 AND num_gpus >= 0", name="ck_jobs_resources"),
        sa.CheckConstraint("walltime_min > 0", name="ck_jobs_walltime"),
    )
    op.create_index("ix_jobs_user_id", "jobs", ["user_id"])
    op.create_index("ix_jobs_group_id", "jobs", ["group_id"])
    op.create_index("ix_jobs_status_queued_at", "jobs", ["status", "queued_at"])
    op.create_index("ix_jobs_user_created_at", "jobs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_user_created_at", table_name="jobs")
    op.drop_index("ix_jobs_status_queued_at", table_name="jobs")
    op.drop_index("ix_jobs_group_id", table_name="jobs")
    op.drop_index("ix_jobs_user_id", table_name="jobs")
    op.drop_table("jobs")
