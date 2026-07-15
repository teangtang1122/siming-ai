"""Runtime schema sync: auto-add missing tables and columns from metadata."""
from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.types import Boolean, Date, DateTime, Float, Integer, LargeBinary, String, Text


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
    if isinstance(col_type, LargeBinary):
        return "BLOB"
    return "TEXT"


def _sql_default(col) -> str:
    """Extract a SQL-safe default value from a column, or empty string."""
    if col.default is None and col.server_default is None:
        return ""

    default_arg = None
    if col.server_default is not None:
        # server_default is a SQL expression; use it directly.
        return f" DEFAULT ({col.server_default.arg})" if col.server_default.arg else ""
    if col.default is not None:
        default_arg = col.default.arg
        if default_arg is None:
            return ""

    if callable(default_arg):
        # Python callable defaults cannot be translated to SQL safely.
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
    """Return whether model metadata contains a table or column missing in DB."""
    from .session import Base

    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            return True
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        if any(column.name not in existing for column in table.columns):
            return True
    return False


def ensure_runtime_schema(engine: Engine) -> None:
    """Auto-sync DB schema with all tables in SQLAlchemy metadata.

    New tables are handled by create_all() before this runs. Existing tables are
    checked column-by-column and any missing columns are appended in place.
    """
    from .session import Base

    with engine.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            inspector = inspect(conn)
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
                    # SQLite cannot add a NOT NULL column without a default when
                    # rows may already exist. Prefer preserving user data.
                    nullable = ""

                sql = (
                    f"ALTER TABLE {table_name} ADD COLUMN "
                    f"{column.name} {col_sql}{nullable}{default_clause}"
                )
                conn.execute(text(sql))

            # --- Create missing indexes ---
            existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
            for index in table.indexes:
                if index.name in existing_indexes:
                    continue
                cols = ", ".join(c.name for c in index.columns)
                conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index.name} ON {table_name} ({cols})"))

        # Existing installations predate the readiness contract. Preserve the
        # selected global model, while requiring every other legacy config to
        # pass an explicit verification before it can be used.
        inspector = inspect(conn)
        if inspector.has_table("api_configs"):
            columns = {column["name"] for column in inspector.get_columns("api_configs")}
            if {"readiness_status", "readiness_json"}.issubset(columns):
                conn.execute(text(
                    "UPDATE api_configs "
                    "SET readiness_status = 'ready', "
                    "readiness_json = '{\"source\":\"legacy_global\"}' "
                    "WHERE is_global_default = 1 "
                    "AND readiness_status = 'unverified' "
                    "AND (readiness_json IS NULL OR readiness_json = '')"
                ))
                conn.execute(text(
                    "UPDATE api_configs "
                    "SET readiness_json = '{\"source\":\"legacy_existing\"}' "
                    "WHERE (readiness_json IS NULL OR readiness_json = '')"
                ))
                if "api_protocol" in columns:
                    # Before 2.8.4 custom OpenAI-compatible endpoints were
                    # verified only through GET /models. A 404 there does not
                    # prove that Chat Completions or Responses is unavailable.
                    conn.execute(text(
                        "UPDATE api_configs "
                        "SET readiness_status = 'unverified', "
                        "readiness_json = '{\"source\":\"protocol_migration\","
                        "\"message\":\"旧版模型列表验证不适用于此接口，请重新点击测试并启用\"}' "
                        "WHERE provider_type = 'api' "
                        "AND provider NOT IN ('openai','anthropic','deepseek','qwen','gemini') "
                        "AND readiness_status = 'unavailable' "
                        "AND readiness_json LIKE '%404%' "
                        "AND readiness_json LIKE '%/models%'"
                    ))

    # --- RAG FTS5 virtual table (try/except, never fail startup) ---
    try:
        with engine.begin() as fts_conn:
            inspector = inspect(fts_conn)
            existing_tables = inspector.get_table_names()
            if "rag_chunks_fts" not in existing_tables:
                fts_conn.execute(text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS rag_chunks_fts USING fts5("
                    "chunk_id UNINDEXED, "
                    "project_id UNINDEXED, "
                    "source_type UNINDEXED, "
                    "title, "
                    "content, "
                    "metadata_json, "
                    "tokenize='unicode61'"
                    ")"
                ))
    except Exception:
        # FTS5 not available in this SQLite build — retriever will fall back to LIKE
        pass
