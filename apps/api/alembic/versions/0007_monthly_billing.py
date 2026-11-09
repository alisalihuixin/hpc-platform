"""monthly billing materialized view (F6.1)

Revision ID: 0007_monthly_billing
Revises: 0006_scheduler_core
Create Date: 2026-11-09

PostgreSQL gets a materialized view (month x user x group) plus the unique index
``REFRESH CONCURRENTLY`` requires.  SQLite has no materialized views, so the
migration is a no-op there and the report service falls back to aggregating
``usage_events`` live - the two paths share one output shape.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_monthly_billing"
down_revision: str | None = "0006_scheduler_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW = """
CREATE MATERIALIZED VIEW mv_monthly_billing AS
SELECT
    date_trunc('month', started_at) AS month,
    user_id,
    group_id,
    SUM(CASE WHEN reversed THEN -gpu_hours ELSE gpu_hours END) AS gpu_hours,
    COUNT(DISTINCT job_id) AS job_count
FROM usage_events
GROUP BY 1, 2, 3
"""

# CONCURRENTLY requires a unique index on the view.
_UNIQUE_INDEX = (
    "CREATE UNIQUE INDEX ux_mv_monthly_billing "
    "ON mv_monthly_billing (month, user_id, group_id)"
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        return
    op.execute(_VIEW)
    op.execute(_UNIQUE_INDEX)


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_monthly_billing")
