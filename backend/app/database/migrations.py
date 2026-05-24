"""Runtime schema sync — auto-adds missing tables and columns from SQLAlchemy metadata."""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.types import Boolean, Date, DateTime, Float, Integer, String, Text


def _sqlite_type(col_type) -> str:
    """Map a SQLAlchemy column type to a SQLite-compatible SQL type name."""
    if isinstance(col_type, String):
        return f"VARCHAR({col_type.length})" if col_type.length else "TEXT"
    if isinstance(col_type, Text):
        return "TEXT"
    if isinstance(col_type, Integer):
        return "INTEGER"
    if isinstance(col_type, Boolean):
        return "INTEGER"
    if isinstance(col_type, DateTime):
        return "TIMESTAMP"
    if isinstance(col_type, Date):
        return "DATE"
    if isinstance(col_type, Float):
        return "REAL"
    return "TEXT"


def _sql_default(col) -> str:
    """Extract a SQL-safe default value from a column, or empty string."""
    if col.default is None and col.server_default is None:
        return ""

    default_arg = None
    if col.server_default is not None:
        # server_default is a SQL expression — use it directly
        return f" DEFAULT ({col.server_default.arg})" if col.server_default.arg else ""
    if col.default is not None:
        default_arg = col.default.arg
        if default_arg is None:
            return ""

    if callable(default_arg):
        # Python callable (e.g. generate_uuid, datetime.utcnow) — can't translate to SQL
        return ""
    if isinstance(default_arg, bool):
        return f" DEFAULT {1 if default_arg else 0}"
    if isinstance(default_arg, (int, float)):
        return f" DEFAULT {default_arg}"
    if isinstance(default_arg, str):
        escaped = default_arg.replace("'", "''")
        return f" DEFAULT '{escaped}'"
    return ""


def ensure_runtime_schema(engine: Engine) -> None:
    """Auto-sync DB schema with all tables in SQLAlchemy metadata.

    - New tables: handled by create_all() which runs before this.
    - New columns on existing tables: detected and added via ALTER TABLE.
    """
    from .session import Base

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            if not inspector.has_table(table_name):
                continue  # create_all() handles brand-new tables

            existing = {col["name"] for col in inspector.get_columns(table_name)}

            for column in table.columns:
                if column.name in existing:
                    continue

                col_sql = _sqlite_type(column.type)
                nullable = "" if column.nullable else " NOT NULL"
                default_clause = _sql_default(column)
                if not column.nullable and not default_clause:
                    # Can't add a NOT NULL column without a default to a table
                    # that already has rows. Fall back to nullable for safety.
                    nullable = ""

                sql = (
                    f"ALTER TABLE {table_name} ADD COLUMN "
                    f"{column.name} {col_sql}{nullable}{default_clause}"
                )
                conn.execute(text(sql))
