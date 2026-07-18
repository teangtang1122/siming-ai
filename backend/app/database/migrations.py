"""One-time bridge from pre-3.0 runtime schemas to the Alembic baseline.

Siming 2.x inferred missing columns during every startup. Siming 3.x calls the
functions in this module only from the first Alembic baseline migration. The
public wrappers remain for old tests and integrations during the preview cycle.
"""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.types import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
)


def _sqlite_type(col_type) -> str:
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
    if isinstance(col_type, LargeBinary):
        return "BLOB"
    return "TEXT"


def _sql_default(col) -> str:
    if col.default is None and col.server_default is None:
        return ""

    default_arg = None
    if col.server_default is not None:
        return (
            f" DEFAULT ({col.server_default.arg})"
            if col.server_default.arg
            else ""
        )
    if col.default is not None:
        default_arg = col.default.arg
        if default_arg is None:
            return ""

    if callable(default_arg):
        return ""
    if isinstance(default_arg, bool):
        return f" DEFAULT {1 if default_arg else 0}"
    if isinstance(default_arg, (int, float)):
        return f" DEFAULT {default_arg}"
    if isinstance(default_arg, str):
        escaped = default_arg.replace("'", "''")
        return f" DEFAULT '{escaped}'"
    return ""


def runtime_schema_needs_sync(engine: Engine) -> bool:
    """Compatibility probe used by pre-3.0 launchers and tests."""
    from .session import Base

    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            return True
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        if any(column.name not in existing for column in table.columns):
            return True
    return False


def _reconcile_metadata(connection: Connection) -> None:
    from .session import Base

    Base.metadata.create_all(bind=connection)
    for table_name, table in Base.metadata.tables.items():
        inspector = inspect(connection)
        if not inspector.has_table(table_name):
            continue

        existing = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name in existing:
                continue

            col_sql = _sqlite_type(column.type)
            nullable = "" if column.nullable else " NOT NULL"
            default_clause = _sql_default(column)
            if not column.nullable and not default_clause:
                # Old SQLite rows cannot satisfy a newly inferred NOT NULL
                # column. The explicit migration following this baseline owns
                # any stricter backfill.
                nullable = ""
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} ADD COLUMN "
                    f"{column.name} {col_sql}{nullable}{default_clause}"
                )
            )

        existing_indexes = {
            idx["name"] for idx in inspector.get_indexes(table_name)
        }
        for index in table.indexes:
            if index.name in existing_indexes:
                continue
            columns = ", ".join(column.name for column in index.columns)
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {index.name} "
                    f"ON {table_name} ({columns})"
                )
            )


def _backfill_model_readiness(connection: Connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("api_configs"):
        return
    columns = {
        column["name"] for column in inspector.get_columns("api_configs")
    }
    if not {"readiness_status", "readiness_json"}.issubset(columns):
        return

    connection.execute(
        text(
            "UPDATE api_configs "
            "SET readiness_status = 'ready', "
            "readiness_json = '{\"source\":\"legacy_global\"}' "
            "WHERE is_global_default = 1 "
            "AND readiness_status = 'unverified' "
            "AND (readiness_json IS NULL OR readiness_json = '')"
        )
    )
    connection.execute(
        text(
            "UPDATE api_configs "
            "SET readiness_json = '{\"source\":\"legacy_existing\"}' "
            "WHERE (readiness_json IS NULL OR readiness_json = '')"
        )
    )
    if "api_protocol" not in columns:
        return
    connection.execute(
        text(
            "UPDATE api_configs "
            "SET readiness_status = 'unverified', "
            "readiness_json = '{\"source\":\"protocol_migration\","
            "\"message\":\"旧版模型列表验证不适用于此接口，请重新测试并启用\"}' "
            "WHERE provider_type = 'api' "
            "AND provider NOT IN "
            "('openai','anthropic','deepseek','qwen','gemini') "
            "AND readiness_status = 'unavailable' "
            "AND readiness_json LIKE '%404%' "
            "AND readiness_json LIKE '%/models%'"
        )
    )


def _ensure_fts(connection: Connection) -> None:
    try:
        inspector = inspect(connection)
        if "rag_chunks_fts" in inspector.get_table_names():
            return
        connection.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5("
                "chunk_id UNINDEXED, "
                "project_id UNINDEXED, "
                "source_type UNINDEXED, "
                "title, content, metadata_json, "
                "tokenize='unicode61'"
                ")"
            )
        )
    except Exception:
        # Some bundled SQLite builds do not include FTS5. Retrieval already
        # has a LIKE fallback, so schema migration must remain usable.
        pass


def reconcile_pre_3_schema(connection: Connection) -> None:
    """Normalize a recognized legacy database exactly once."""
    _reconcile_metadata(connection)
    _backfill_model_readiness(connection)
    _ensure_fts(connection)


def ensure_runtime_schema(engine: Engine) -> None:
    """Deprecated compatibility wrapper around the one-time bridge."""
    with engine.begin() as connection:
        reconcile_pre_3_schema(connection)
