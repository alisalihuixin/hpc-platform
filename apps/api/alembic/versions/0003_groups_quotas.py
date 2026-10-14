"""groups, user_groups and quotas (F3.1 / F8.2)

Revision ID: 0003_groups_quotas
Revises: 0002_nodes
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_groups_quotas"
down_revision: str | None = "0002_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_DICT = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
    )
    op.create_index("ix_groups_name", "groups", ["name"], unique=True)

    op.create_table(
        "user_groups",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "quotas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gpu_limit", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gpu_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_nodes_per_job", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_gpus_per_job", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("detail", JSONB_DICT, nullable=False),
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
            "gpu_used >= 0 AND gpu_used <= gpu_limit", name="ck_quotas_gpu_used"
        ),
    )
    op.create_index("ix_quotas_group_id", "quotas", ["group_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_quotas_group_id", table_name="quotas")
    op.drop_table("quotas")
    op.drop_table("user_groups")
    op.drop_index("ix_groups_name", table_name="groups")
    op.drop_table("groups")
