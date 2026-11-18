"""diagnosis domain - mock fleet, alerts, runs, evidence chain, reviews (F1/F3/F7)

Revision ID: 0009_diagnosis
Revises: 0008_audit_log
Create Date: 2026-11-18

Creates the seven tables of project 2.  They are intentionally separate from the
billing domain: ``incident_nodes`` is a mock fleet, so diagnosing a script can
never mutate ``nodes.gpu_used`` or trip the audit triggers.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.types import JSONDict

revision: str = "0009_diagnosis"
down_revision: str | None = "0008_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("node_model", sa.String(length=128), nullable=False),
        sa.Column("switch", sa.String(length=64), nullable=False),
        sa.Column("rack", sa.String(length=64), nullable=False),
        sa.Column("gpu_model", sa.String(length=64), nullable=True),
        sa.Column("gpu_count", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="online"),
        sa.Column("schedulable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags", JSONDict, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('online', 'degraded', 'offline')",
            name="ck_incident_nodes_status",
        ),
        sa.CheckConstraint("gpu_count >= 0", name="ck_incident_nodes_gpus"),
    )
    op.create_index("ix_incident_nodes_hostname", "incident_nodes", ["hostname"], unique=True)
    op.create_index("ix_incident_nodes_switch", "incident_nodes", ["switch"])
    op.create_index("ix_incident_nodes_rack", "incident_nodes", ["rack"])
    op.create_index("ix_incident_nodes_status", "incident_nodes", ["status"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("incident_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("script_key", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="warning"),
        sa.Column("summary", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column(
            "fired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'critical')",
            name="ck_alerts_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'diagnosing', 'resolved')",
            name="ck_alerts_status",
        ),
    )
    op.create_index("ix_alerts_node_id", "alerts", ["node_id"])
    op.create_index("ix_alerts_script_key", "alerts", ["script_key"])
    op.create_index("ix_alerts_status_fired_at", "alerts", ["status", "fired_at"])

    op.create_table(
        "diagnosis_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alert_id",
            sa.Integer(),
            sa.ForeignKey("alerts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("root_cause", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("iterations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
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
            "status IN ('pending', 'running', 'succeeded', 'degraded', 'failed')",
            name="ck_diagnosis_runs_status",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_diagnosis_runs_confidence",
        ),
        sa.CheckConstraint(
            "iterations >= 0 AND tokens >= 0",
            name="ck_diagnosis_runs_counters",
        ),
    )
    op.create_index("ix_diagnosis_runs_alert_id", "diagnosis_runs", ["alert_id"])
    op.create_index("ix_diagnosis_runs_batch_id", "diagnosis_runs", ["batch_id"])
    op.create_index("ix_diagnosis_runs_status", "diagnosis_runs", ["status"])
    op.create_index("ix_diagnosis_runs_trace_id", "diagnosis_runs", ["trace_id"])

    op.create_table(
        "diagnosis_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("node", sa.String(length=16), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("args", JSONDict, nullable=True),
        sa.Column("result", JSONDict, nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "node IN ('plan', 'tool', 'observe', 'reflect', 'report', 'fallback')",
            name="ck_diagnosis_steps_node",
        ),
        sa.CheckConstraint("seq > 0 AND latency_ms >= 0", name="ck_diagnosis_steps_seq"),
        sa.UniqueConstraint("run_id", "seq", name="uq_diagnosis_steps_run_seq"),
    )
    op.create_index("ix_diagnosis_steps_run_id", "diagnosis_steps", ["run_id"])

    op.create_table(
        "diagnosis_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("diagnosis_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("corrected_cause", sa.String(length=255), nullable=True),
        sa.Column(
            "reviewer_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verdict IN ('correct', 'wrong')",
            name="ck_diagnosis_reviews_verdict",
        ),
        sa.UniqueConstraint("run_id", name="uq_diagnosis_reviews_run_id"),
    )
    op.create_index("ix_diagnosis_reviews_run_id", "diagnosis_reviews", ["run_id"])

    op.create_table(
        "runbooks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tags", sa.String(length=255), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_runbooks_title", "runbooks", ["title"])

    op.create_table(
        "eval_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("script_key", sa.String(length=64), nullable=False),
        sa.Column("expected_cause", sa.String(length=255), nullable=False),
        sa.Column("trap", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expected_evidence", JSONDict, nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("script_key", name="uq_eval_cases_script_key"),
    )
    op.create_index("ix_eval_cases_script_key", "eval_cases", ["script_key"])


def downgrade() -> None:
    op.drop_index("ix_eval_cases_script_key", table_name="eval_cases")
    op.drop_table("eval_cases")

    op.drop_index("ix_runbooks_title", table_name="runbooks")
    op.drop_table("runbooks")

    op.drop_index("ix_diagnosis_reviews_run_id", table_name="diagnosis_reviews")
    op.drop_table("diagnosis_reviews")

    op.drop_index("ix_diagnosis_steps_run_id", table_name="diagnosis_steps")
    op.drop_table("diagnosis_steps")

    op.drop_index("ix_diagnosis_runs_trace_id", table_name="diagnosis_runs")
    op.drop_index("ix_diagnosis_runs_status", table_name="diagnosis_runs")
    op.drop_index("ix_diagnosis_runs_batch_id", table_name="diagnosis_runs")
    op.drop_index("ix_diagnosis_runs_alert_id", table_name="diagnosis_runs")
    op.drop_table("diagnosis_runs")

    op.drop_index("ix_alerts_status_fired_at", table_name="alerts")
    op.drop_index("ix_alerts_script_key", table_name="alerts")
    op.drop_index("ix_alerts_node_id", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_incident_nodes_status", table_name="incident_nodes")
    op.drop_index("ix_incident_nodes_rack", table_name="incident_nodes")
    op.drop_index("ix_incident_nodes_switch", table_name="incident_nodes")
    op.drop_index("ix_incident_nodes_hostname", table_name="incident_nodes")
    op.drop_table("incident_nodes")
