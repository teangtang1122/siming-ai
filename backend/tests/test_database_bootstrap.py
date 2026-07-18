"""Database bootstrap and recovery-mode migration tests."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect, text

from app.database.bootstrap import SCHEMA_EPOCH, bootstrap_database


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_fresh_database_is_initialized_and_versioned():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "fresh.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            result = bootstrap_database(engine, database_url=url)
            tables = set(inspect(engine).get_table_names())
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                epoch = connection.execute(
                    text("SELECT value FROM siming_schema_metadata WHERE key = 'schema_epoch'")
                ).scalar_one()
            assert result.mode == "initialized"
            assert result.read_only is False
            assert result.schema_revision == revision == "300a2_content_sync"
            assert epoch == SCHEMA_EPOCH
            assert {"projects", "chapters", "operation_runs", "content_sync_jobs"} <= tables
        finally:
            engine.dispose()


def test_recognized_legacy_database_is_backed_up_and_preserved():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "legacy.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, title VARCHAR(200))")
                )
                connection.execute(
                    text(
                        "INSERT INTO projects (id, title) VALUES ('legacy-project', 'Legacy Story')"
                    )
                )

            result = bootstrap_database(engine, database_url=url)

            assert result.mode == "migrated"
            assert result.backup_path
            assert Path(result.backup_path).is_file()
            with engine.connect() as connection:
                title = connection.execute(
                    text("SELECT title FROM projects WHERE id = 'legacy-project'")
                ).scalar_one()
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            assert title == "Legacy Story"
            assert revision == "300a2_content_sync"
        finally:
            engine.dispose()


def test_unknown_database_enters_read_only_recovery_without_mutation():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "unknown.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE unrelated_data (id INTEGER PRIMARY KEY)"))
                connection.execute(text("INSERT INTO unrelated_data (id) VALUES (1)"))

            result = bootstrap_database(engine, database_url=url)

            assert result.mode == "read_only_recovery"
            assert result.read_only is True
            assert "do not belong to Siming" in result.message
            tables = set(inspect(engine).get_table_names())
            assert tables == {"unrelated_data"}
            with engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT COUNT(*) FROM unrelated_data")).scalar_one()
                    == 1
                )
        finally:
            engine.dispose()


def test_current_database_bootstrap_is_idempotent():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "current.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            first = bootstrap_database(engine, database_url=url)
            second = bootstrap_database(engine, database_url=url)
            assert first.mode == "initialized"
            assert second.mode == "ready"
            assert second.backup_path is None
            assert second.schema_revision == first.schema_revision
        finally:
            engine.dispose()


def test_failed_migration_returns_the_verified_backup(monkeypatch):
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "failed.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, title VARCHAR(200))")
                )
                connection.execute(text("INSERT INTO projects VALUES ('p1', 'Preserve Me')"))

            def fail_upgrade(*_args, **_kwargs):
                raise RuntimeError("migration rehearsal failure")

            monkeypatch.setattr("app.database.bootstrap.command.upgrade", fail_upgrade)
            result = bootstrap_database(engine, database_url=url)

            assert result.mode == "read_only_recovery"
            assert result.read_only is True
            assert result.backup_path is not None
            backup_path = Path(result.backup_path)
            assert backup_path.is_file()
            with closing(sqlite3.connect(backup_path)) as connection:
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert (
                    connection.execute("SELECT title FROM projects").fetchone()[0] == "Preserve Me"
                )
            with engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT title FROM projects")).scalar_one()
                    == "Preserve Me"
                )
        finally:
            engine.dispose()


def test_alpha1_database_upgrades_to_content_sync_outbox():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "alpha1.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE alembic_version "
                        "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                    )
                )
                connection.execute(
                    text("INSERT INTO alembic_version (version_num) VALUES ('300a1_baseline')")
                )
                connection.execute(
                    text(
                        "CREATE TABLE siming_schema_metadata "
                        "(key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL, "
                        "updated_at DATETIME NOT NULL)"
                    )
                )

            result = bootstrap_database(engine, database_url=url)

            assert result.mode == "migrated"
            assert result.schema_revision == "300a2_content_sync"
            assert "content_sync_jobs" in inspect(engine).get_table_names()
        finally:
            engine.dispose()


def test_importing_application_does_not_create_or_migrate_database():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "import-only.db"
        environment = {
            **os.environ,
            "DATABASE_URL": _database_url(database_path),
            "SIMING_DISABLE_UPDATE": "1",
            "MOSHU_DISABLE_AUTO_MCP_SETUP": "1",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.main import app; print(app.version)",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert database_path.exists() is False
