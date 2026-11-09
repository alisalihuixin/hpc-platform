"""Audit trigger DDL (F7.1) - one source for migrations and the test bootstrap.

The app never writes ``audit_log`` itself: triggers on ``users`` / ``quotas`` /
``nodes`` / ``jobs`` capture UPDATE and DELETE.  ``hashed_password`` is stripped
from every payload.

* PostgreSQL reads the acting user from ``app.user_id`` (set per request by
  ``app.deps.auth.get_current_user`` via ``set_config(..., true)``); background
  writes fall back to ``system``.
* SQLite has no session variables, so ``changed_by`` stays NULL there - the
  tests assert the trigger *fires*, not the actor.
* ``jobs`` only records a change when ``status`` actually moves, otherwise every
  scheduler tick would flood the table.
"""

from __future__ import annotations

#: Columns kept in the audit payload. ``hashed_password`` is intentionally absent.
AUDITED_TABLES: dict[str, tuple[str, ...]] = {
    "users": ("email", "username", "full_name", "is_active"),
    "quotas": ("gpu_limit", "gpu_used", "max_nodes_per_job", "max_gpus_per_job"),
    "nodes": (
        "hostname",
        "status",
        "gpu_model",
        "gpu_total",
        "gpu_used",
        "cpu_cores",
        "cpu_used",
        "mem_gb",
        "mem_used",
    ),
    "jobs": ("status", "node_id", "started_at", "ended_at", "num_gpus", "num_cpus", "mem_gb"),
}

#: Tables whose UPDATE trigger only fires when this column changes.
_CHANGE_GUARDS: dict[str, str] = {"jobs": "status"}


def _sqlite_trigger(table: str, columns: tuple[str, ...], action: str) -> str:
    if action == "update":
        before = ", ".join(f"'{column}', OLD.{column}" for column in columns)
        after = ", ".join(f"'{column}', NEW.{column}" for column in columns)
        guard = ""
        if table in _CHANGE_GUARDS:
            column = _CHANGE_GUARDS[table]
            guard = f"\nFOR EACH ROW WHEN OLD.{column} <> NEW.{column}"
        else:
            guard = "\nFOR EACH ROW"
        return f"""
CREATE TRIGGER IF NOT EXISTS trg_audit_{table}_update
AFTER UPDATE ON {table}{guard}
BEGIN
    INSERT INTO audit_log (table_name, record_id, action, changed_by, changed_at, before, after)
    VALUES (
        '{table}', OLD.id, 'update', NULL, CURRENT_TIMESTAMP,
        json_object({before}),
        json_object({after})
    );
END
"""

    before = ", ".join(f"'{column}', OLD.{column}" for column in columns)
    return f"""
CREATE TRIGGER IF NOT EXISTS trg_audit_{table}_delete
AFTER DELETE ON {table}
FOR EACH ROW
BEGIN
    INSERT INTO audit_log (table_name, record_id, action, changed_by, changed_at, before, after)
    VALUES (
        '{table}', OLD.id, 'delete', NULL, CURRENT_TIMESTAMP,
        json_object({before}),
        NULL
    );
END
"""


def sqlite_statements() -> list[str]:
    statements: list[str] = []
    for table, columns in AUDITED_TABLES.items():
        statements.append(_sqlite_trigger(table, columns, "update"))
        statements.append(_sqlite_trigger(table, columns, "delete"))
    return statements


_POSTGRES_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger AS $$
BEGIN
    IF (TG_OP = 'DELETE') THEN
        INSERT INTO audit_log (table_name, record_id, action, changed_by, before, after)
        VALUES (
            TG_TABLE_NAME,
            OLD.id,
            'delete',
            COALESCE(NULLIF(current_setting('app.user_id', true), ''), 'system'),
            to_jsonb(OLD) - 'hashed_password',
            NULL
        );
        RETURN OLD;
    END IF;

    INSERT INTO audit_log (table_name, record_id, action, changed_by, before, after)
    VALUES (
        TG_TABLE_NAME,
        NEW.id,
        'update',
        COALESCE(NULLIF(current_setting('app.user_id', true), ''), 'system'),
        to_jsonb(OLD) - 'hashed_password',
        to_jsonb(NEW) - 'hashed_password'
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def postgres_statements() -> list[str]:
    statements = [_POSTGRES_FUNCTION]
    for table in AUDITED_TABLES:
        statements.append(
            f"CREATE TRIGGER trg_audit_{table}_update "
            f"AFTER UPDATE ON {table} FOR EACH ROW "
            f"EXECUTE FUNCTION audit_row_change()"
        )
        statements.append(
            f"CREATE TRIGGER trg_audit_{table}_delete "
            f"AFTER DELETE ON {table} FOR EACH ROW "
            f"EXECUTE FUNCTION audit_row_change()"
        )
    return statements


def statements(dialect: str) -> list[str]:
    if dialect == "postgresql":
        return postgres_statements()
    if dialect == "sqlite":
        return sqlite_statements()
    return []


def drop_statements(dialect: str) -> list[str]:
    if dialect == "postgresql":
        return [
            *(
                f"DROP TRIGGER IF EXISTS trg_audit_{table}_update ON {table}"
                for table in AUDITED_TABLES
            ),
            *(
                f"DROP TRIGGER IF EXISTS trg_audit_{table}_delete ON {table}"
                for table in AUDITED_TABLES
            ),
            "DROP FUNCTION IF EXISTS audit_row_change()",
        ]
    if dialect == "sqlite":
        return [
            *(f"DROP TRIGGER IF EXISTS trg_audit_{table}_update" for table in AUDITED_TABLES),
            *(f"DROP TRIGGER IF EXISTS trg_audit_{table}_delete" for table in AUDITED_TABLES),
        ]
    return []
