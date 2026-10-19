"""usage_events table (F5.1)

Revision ID: 0005_usage_events
Revises: 0004_jobs
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_usage_events"
down_revision: str | None = "0004_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_DICT = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        # No ON DELETE CASCADE: billing rows are evidence.
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("gpus", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("gpu_hours", sa.Numeric(12, 4), nullable=False),
        sa.Column("rate_snapshot", JSONB_DICT, nullable=False),
        sa.Column("reversed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("gpus >= 0", name="ck_usage_gpus"),
        sa.CheckConstraint("duration_min >= 0", name="ck_usage_duration"),
        sa.CheckConstraint("gpu_hours >= 0", name="ck_usage_gpu_hours"),
    )
    op.create_index("ix_usage_events_job_id", "usage_events", ["job_id"])
    op.create_index("ix_usage_group_started", "usage_events", ["group_id", "started_at"])
    op.create_index("ix_usage_user_started", "usage_events", ["user_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_user_started", table_name="usage_events")
    op.drop_index("ix_usage_group_started", table_name="usage_events")
    op.drop_index("ix_usage_events_job_id", table_name="usage_events")
    op.drop_table("usage_events")
