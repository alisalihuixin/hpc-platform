"""scheduler core: priority, CPU/memory slots, job leases (F4.3)

Revision ID: 0006_scheduler_core
Revises: 0005_usage_events
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_scheduler_core"
down_revision: str | None = "0005_usage_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Batch mode keeps this portable to SQLite (no ALTER TABLE ADD CONSTRAINT).
    with op.batch_alter_table("nodes") as batch:
        batch.add_column(
            sa.Column("cpu_used", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("mem_used", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_check_constraint(
            "ck_nodes_cpu_used", "cpu_used >= 0 AND cpu_used <= cpu_cores"
        )
        batch.create_check_constraint(
            "ck_nodes_mem_used", "mem_used >= 0 AND mem_used <= mem_gb"
        )

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column("num_cpus", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("mem_gb", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint("ck_jobs_cpu_mem", "num_cpus >= 0 AND mem_gb >= 0")

    op.create_index("ix_jobs_status_lease", "jobs", ["status", "lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status_lease", table_name="jobs")

    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_cpu_mem", type_="check")
        batch.drop_column("lease_expires_at")
        batch.drop_column("priority")
        batch.drop_column("mem_gb")
        batch.drop_column("num_cpus")

    with op.batch_alter_table("nodes") as batch:
        batch.drop_constraint("ck_nodes_mem_used", type_="check")
        batch.drop_constraint("ck_nodes_cpu_used", type_="check")
        batch.drop_column("mem_used")
        batch.drop_column("cpu_used")
