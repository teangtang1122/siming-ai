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

from ..architecture.uow import commit_connection, rollback_connection
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
RETIRED_DATA_ONLY_REVISIONS = {
    "300a19_runtime_readiness": "300a18_user_chapter_cataloging",
}
SQLITE_FK_RELAXED_REVISION = "300a28_conversation_context"


@dataclass(frozen=True)
class DatabaseBootstrapResult:
    mode: str
    schema_revision: str | None
    message: str
    read_only: bool = False
    backup_path: str | None = None


def migration_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "alembic"
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


def _retired_revision_target(config: Config, current: str | None) -> str | None:
    target = RETIRED_DATA_ONLY_REVISIONS.get(str(current or ""))
    if not target:
        return None
    # Only normalize to a revision that is actually present in this build.
    if ScriptDirectory.from_config(config).get_revision(target) is None:
        raise RuntimeError(f"Retired revision target is unavailable: {target}")
    return target


def _normalize_retired_revision(
    target_engine: Engine,
    *,
    current: str,
    target: str,
) -> None:
    """Restamp one shipped data-only revision whose migration file was retired."""

    with target_engine.begin() as connection:
        result = connection.execute(
            text(
                "UPDATE alembic_version SET version_num = :target "
                "WHERE version_num = :current"
            ),
            {"current": current, "target": target},
        )
        if result.rowcount != 1:
            raise RuntimeError(
                f"Could not normalize retired revision {current!r} to {target!r}."
            )
        if "siming_schema_metadata" in inspect(connection).get_table_names():
            connection.execute(
                text(
                    "UPDATE siming_schema_metadata "
                    "SET value = :target, updated_at = CURRENT_TIMESTAMP "
                    "WHERE key = 'alembic_revision'"
                ),
                {"target": target},
            )


def _classify_unversioned_schema(target_engine: Engine) -> tuple[str, str]:
    # Schema recognition must not depend on which routers or services happened
    # to be imported before bootstrap runs.
    from . import models as _models  # noqa: F401

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
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing = required_columns - columns
        if missing:
            return (
                "unknown",
                f"Table {table_name} is missing identity columns: " + ", ".join(sorted(missing)),
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


def _set_sqlite_foreign_keys(connection, *, enabled: bool) -> None:
    """Set one migration connection's SQLite FK mode outside a transaction."""

    if connection.in_transaction():
        commit_connection(connection)
    expected = 1 if enabled else 0
    connection.exec_driver_sql(
        f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}"
    )
    if connection.in_transaction():
        commit_connection(connection)
    actual = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
    if connection.in_transaction():
        commit_connection(connection)
    if int(actual or 0) != expected:
        state = "enabled" if enabled else "disabled"
        raise RuntimeError(f"SQLite foreign keys could not be {state} for migration")


def _migration_path_contains(
    config: Config,
    *,
    current: str | None,
    head: str,
    revision: str,
) -> bool:
    scripts = ScriptDirectory.from_config(config)
    return any(
        candidate.revision == revision
        for candidate in scripts.iterate_revisions(head, current)
    )


def _upgrade_schema(
    target_engine: Engine,
    config: Config,
    *,
    relax_sqlite_foreign_keys: bool,
) -> None:
    """Upgrade through one migration-only connection.

    SQLite table rebuilds use ``DROP TABLE`` followed by ``RENAME``.  Keeping
    foreign keys enabled while rebuilding a referenced parent table can either
    reject legacy orphan rows or apply ``ON DELETE`` actions to otherwise valid
    child rows.  Both happened in the 3.3.9 migration.  Disable enforcement
    only on this connection, let the integrity migration repair legacy rows,
    and restore enforcement before the pooled connection can be reused.
    """

    if target_engine.dialect.name != "sqlite" or not relax_sqlite_foreign_keys:
        with target_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        return

    with target_engine.connect() as connection:
        config.attributes["connection"] = connection
        config.attributes["sqlite_migration_foreign_keys_disabled"] = True
        try:
            _set_sqlite_foreign_keys(connection, enabled=False)
            command.upgrade(config, "head")
            if connection.in_transaction():
                commit_connection(connection)
        except Exception:
            if connection.in_transaction():
                rollback_connection(connection)
            raise
        finally:
            config.attributes.pop("sqlite_migration_foreign_keys_disabled", None)
            _set_sqlite_foreign_keys(connection, enabled=True)


def bootstrap_database(
    target_engine: Engine = engine,
    *,
    database_url: str | None = None,
    refresh_current_metadata: bool = True,
) -> DatabaseBootstrapResult:
    """Upgrade a known database to head or return a safe recovery result.

    ``refresh_current_metadata`` may be disabled by packaged MCP child
    processes.  The desktop process has already completed the schema bootstrap
    before it launches those children, so refreshing application-version
    metadata again only creates an unnecessary competing SQLite writer.
    """
    settings = get_settings()
    url = database_url or settings.database_url
    config = alembic_config(url)
    backup_path: Path | None = None
    try:
        head = _head_revision(config)
        current = _current_revision(target_engine)
        mode = "versioned"
        retired_revision = str(current or "")
        retired_target = _retired_revision_target(config, current)
        if retired_target:
            backup_path = backup_sqlite_database(
                url,
                reason=f"pre-{APP_VERSION}-retired-revision",
            )
            _normalize_retired_revision(
                target_engine,
                current=retired_revision,
                target=retired_target,
            )
            current = retired_target
            mode = "retired_revision_normalized"
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
            if refresh_current_metadata:
                _record_schema_epoch(target_engine, head)
            return DatabaseBootstrapResult(
                mode="migrated" if mode == "retired_revision_normalized" else "ready",
                schema_revision=head,
                message=(
                    f"Normalized retired database revision {retired_revision} to {head}."
                    if mode == "retired_revision_normalized"
                    else "Database schema is current."
                ),
                backup_path=str(backup_path) if backup_path else None,
            )

        if backup_path is None:
            backup_path = backup_sqlite_database(
                url,
                reason=f"pre-{APP_VERSION}",
            )
        relax_sqlite_foreign_keys = (
            target_engine.dialect.name == "sqlite"
            and _migration_path_contains(
                config,
                current=current,
                head=head,
                revision=SQLITE_FK_RELAXED_REVISION,
            )
        )
        _upgrade_schema(
            target_engine,
            config,
            relax_sqlite_foreign_keys=relax_sqlite_foreign_keys,
        )
        revision = _current_revision(target_engine)
        if revision != head:
            raise RuntimeError(f"Schema upgrade ended at {revision!r}, expected {head!r}.")
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
            backup_path=str(backup_path) if backup_path else None,
        )
