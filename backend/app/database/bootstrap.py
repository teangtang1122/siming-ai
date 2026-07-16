"""Versioned database bootstrap and recovery-mode classification."""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from alembic import command

from ..core.config import get_settings
from ..version import APP_VERSION
from .backup import backup_sqlite_database
from .session import Base, engine

logger = logging.getLogger(__name__)
SCHEMA_EPOCH = "3.0"
KNOWN_CORE_COLUMNS = {
    "projects": {"id", "title"},
    "chapters": {"id", "project_id", "title"},
    "characters": {"id", "project_id", "name"},
    "outline_nodes": {"id", "project_id", "title"},
    "worldbuilding_entries": {"id", "project_id", "title"},
}


@dataclass(frozen=True)
class DatabaseBootstrapResult:
    mode: str
    schema_revision: str | None
    message: str
    read_only: bool = False
    backup_path: str | None = None


def migration_root() -> Path:
    if getattr(sys, "frozen", False):
        return (
            Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            / "alembic"
        )
    return Path(__file__).resolve().parents[2] / "alembic"


def alembic_config(database_url: str | None = None) -> Config:
    root = migration_root()
    config = Config()
    config.set_main_option("script_location", str(root))
    config.set_main_option(
        "sqlalchemy.url",
        database_url or get_settings().database_url,
    )
    return config


def _current_revision(target_engine: Engine) -> str | None:
    with target_engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _head_revision(config: Config) -> str:
    head = ScriptDirectory.from_config(config).get_current_head()
    if not head:
        raise RuntimeError("No Alembic head revision is available.")
    return head


def _classify_unversioned_schema(target_engine: Engine) -> tuple[str, str]:
    inspector = inspect(target_engine)
    tables = set(inspector.get_table_names())
    application_tables = set(Base.metadata.tables)
    user_tables = tables - {"alembic_version", "sqlite_sequence"}
    if not user_tables:
        return "fresh", "No existing application schema was found."

    recognized = user_tables & application_tables
    if not recognized:
        return (
            "unknown",
            "The configured database contains tables that do not belong to Siming.",
        )
    if "projects" not in user_tables:
        return (
            "unknown",
            "The database resembles Siming data but has no projects table.",
        )
    for table_name, required_columns in KNOWN_CORE_COLUMNS.items():
        if table_name not in user_tables:
            continue
        columns = {
            column["name"]
            for column in inspector.get_columns(table_name)
        }
        missing = required_columns - columns
        if missing:
            return (
                "unknown",
                f"Table {table_name} is missing identity columns: "
                + ", ".join(sorted(missing)),
            )
    return "legacy", "A recognized pre-3.0 Siming schema was found."


def _record_schema_epoch(
    target_engine: Engine,
    revision: str,
) -> None:
    with target_engine.begin() as connection:
        payload = {
            "schema_epoch": SCHEMA_EPOCH,
            "alembic_revision": revision,
            "application_version": APP_VERSION,
        }
        for key, value in payload.items():
            connection.execute(
                text(
                    "INSERT INTO siming_schema_metadata "
                    "(key, value, updated_at) "
                    "VALUES (:key, :value, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, "
                    "updated_at = CURRENT_TIMESTAMP"
                ),
                {"key": key, "value": value},
            )


def bootstrap_database(
    target_engine: Engine = engine,
    *,
    database_url: str | None = None,
) -> DatabaseBootstrapResult:
    """Upgrade a known database to head or return a safe recovery result."""
    settings = get_settings()
    url = database_url or settings.database_url
    config = alembic_config(url)
    try:
        head = _head_revision(config)
        current = _current_revision(target_engine)
        mode = "versioned"
        if current is None:
            mode, detail = _classify_unversioned_schema(target_engine)
            if mode == "unknown":
                return DatabaseBootstrapResult(
                    mode="read_only_recovery",
                    schema_revision=None,
                    message=detail,
                    read_only=True,
                )
        if current == head:
            _record_schema_epoch(target_engine, head)
            return DatabaseBootstrapResult(
                mode="ready",
                schema_revision=head,
                message="Database schema is current.",
            )

        backup_path = backup_sqlite_database(
            url,
            reason=f"pre-{APP_VERSION}",
        )
        with target_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        revision = _current_revision(target_engine)
        if revision != head:
            raise RuntimeError(
                f"Schema upgrade ended at {revision!r}, expected {head!r}."
            )
        _record_schema_epoch(target_engine, head)
        return DatabaseBootstrapResult(
            mode="initialized" if mode == "fresh" else "migrated",
            schema_revision=head,
            message=(
                "Created the versioned Siming schema."
                if mode == "fresh"
                else "Migrated the existing Siming schema to the 3.0 baseline."
            ),
            backup_path=str(backup_path) if backup_path else None,
        )
    except Exception as exc:
        logger.exception("Database bootstrap failed")
        try:
            failed_revision = _current_revision(target_engine)
        except Exception:
            failed_revision = None
        return DatabaseBootstrapResult(
            mode="read_only_recovery",
            schema_revision=failed_revision,
            message=f"Database migration failed: {exc}",
            read_only=True,
        )
