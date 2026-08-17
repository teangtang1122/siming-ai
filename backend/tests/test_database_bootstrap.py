"""Database bootstrap and recovery-mode migration tests."""

from __future__ import annotations

import json
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
            assert result.schema_revision == revision == "300a17_chapter_sort_order"
            assert epoch == SCHEMA_EPOCH
            assert {
                "projects",
                "chapters",
                "operation_runs",
                "content_sync_jobs",
                "gateway_devices",
                "sync_changes",
            } <= tables
            assert "sort_order" in {
                column["name"] for column in inspect(engine).get_columns("chapters")
            }
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
            assert revision == "300a17_chapter_sort_order"
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


def test_stamped_300a12_database_repairs_missing_resolution_evidence_columns():
    """A briefly shipped 300a12 schema was stamped before this column existed."""

    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "stamped-300a12.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
                ))
                connection.execute(text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('300a12_narrative_governance_loop')"
                ))
                connection.execute(text(
                    "CREATE TABLE siming_schema_metadata "
                    "(key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME NOT NULL)"
                ))
                for table_name in ("foreshadowings", "causal_edges", "narrative_debts"):
                    connection.execute(text(f"CREATE TABLE {table_name} (id VARCHAR(36) PRIMARY KEY)"))

            result = bootstrap_database(engine, database_url=url)

            inspector = inspect(engine)
            assert result.mode == "migrated"
            assert result.schema_revision == "300a17_chapter_sort_order"
            for table_name in ("foreshadowings", "causal_edges", "narrative_debts"):
                assert "resolution_evidence" in {
                    column["name"] for column in inspector.get_columns(table_name)
                }
        finally:
            engine.dispose()


def test_stamped_300a13_database_repairs_cataloged_outline_hierarchy_only():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "stamped-300a13-outline.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
                ))
                connection.execute(text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('300a13_narrative_resolution_evidence')"
                ))
                connection.execute(text(
                    "CREATE TABLE siming_schema_metadata "
                    "(key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME NOT NULL)"
                ))
                connection.execute(text(
                    "CREATE TABLE outline_nodes ("
                    "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, "
                    "parent_id VARCHAR(36), node_type VARCHAR(20) NOT NULL, title VARCHAR(200) NOT NULL, "
                    "summary TEXT, status VARCHAR(20), source_chapter_id VARCHAR(36), "
                    "actual_summary TEXT, planned_summary TEXT, metadata_json TEXT, "
                    "cataloging_status VARCHAR(30), sort_order INTEGER, "
                    "created_at DATETIME, updated_at DATETIME)"
                ))
                connection.execute(text(
                    "INSERT INTO outline_nodes VALUES "
                    "('chapter-3','p1',NULL,'chapter','第三章 打回去','整章','completed','source-3','整章','',NULL,'cataloged',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),"
                    "('section-3','p1','第三章 打回去','section','空地冲突','场景','completed','source-3','场景','',NULL,'cataloged',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),"
                    "('manual-root','p1',NULL,'chapter','作者手工幕间','手工节点','pending',NULL,'','','{}',NULL,9,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ))

            result = bootstrap_database(engine, database_url=url)

            with engine.connect() as connection:
                rows = {
                    row.id: row
                    for row in connection.execute(text(
                        "SELECT id,parent_id,node_type,title,metadata_json FROM outline_nodes"
                    )).mappings()
                }
            volumes = [row for row in rows.values() if row.node_type == "volume"]
            assert result.schema_revision == "300a17_chapter_sort_order"
            assert len(volumes) == 1
            assert rows["chapter-3"].parent_id == volumes[0].id
            assert rows["section-3"].parent_id == "chapter-3"
            assert rows["manual-root"].parent_id is None
            assert json.loads(volumes[0].metadata_json) == {
                "source": "cataloging_default_volume",
                "start_chapter": 1,
            }
        finally:
            engine.dispose()


def test_stamped_300a14_database_canonicalizes_free_form_character_roles():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "stamped-300a14-roles.db"
        url = _database_url(database_path)
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(64) NOT NULL PRIMARY KEY)"
                ))
                connection.execute(text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('300a14_cataloging_outline_hierarchy')"
                ))
                connection.execute(text(
                    "CREATE TABLE siming_schema_metadata "
                    "(key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME NOT NULL)"
                ))
                connection.execute(text(
                    "CREATE TABLE characters ("
                    "id VARCHAR(36) PRIMARY KEY, role_type VARCHAR(50), background TEXT)"
                ))
                connection.execute(text(
                    "CREATE TABLE cataloging_chapter_runs ("
                    "id VARCHAR(36) PRIMARY KEY, error TEXT)"
                ))
                connection.execute(text(
                    "INSERT INTO characters (id, role_type) VALUES "
                    "('hero', '主角，穿越者，陆家三岁孙女'), "
                    "('elder', '家族长辈·主脉家主')"
                ))

            result = bootstrap_database(engine, database_url=url)

            with engine.connect() as connection:
                rows = {
                    row.id: row
                    for row in connection.execute(text(
                        "SELECT id, role_type, background FROM characters ORDER BY id"
                    )).mappings()
                }
            assert result.schema_revision == "300a17_chapter_sort_order"
            assert {key: row.role_type for key, row in rows.items()} == {
                "elder": "other",
                "hero": "protagonist",
            }
            assert rows["hero"].background == "身份补充：穿越者、陆家三岁孙女"
        finally:
            engine.dispose()


def test_current_database_mcp_check_stays_read_only_during_active_writer():
    with TemporaryDirectory() as temp_dir:
        database_path = Path(temp_dir) / "current-with-writer.db"
        url = _database_url(database_path)
        bootstrap_engine = create_engine(url)
        mcp_engine = create_engine(url)
        writer = None
        try:
            initialized = bootstrap_database(bootstrap_engine, database_url=url)
            assert initialized.read_only is False

            writer = sqlite3.connect(database_path)
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "UPDATE siming_schema_metadata SET value = value WHERE key = 'application_version'"
            )

            checked = bootstrap_database(
                mcp_engine,
                database_url=url,
                refresh_current_metadata=False,
            )

            assert checked.mode == "ready"
            assert checked.read_only is False
            assert checked.schema_revision == initialized.schema_revision
        finally:
            if writer is not None:
                writer.rollback()
                writer.close()
            mcp_engine.dispose()
            bootstrap_engine.dispose()


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


def test_alpha1_database_upgrades_through_gateway_sync():
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
            assert result.schema_revision == "300a17_chapter_sort_order"
            assert {"content_sync_jobs", "gateway_devices", "sync_changes"} <= set(
                inspect(engine).get_table_names()
            )
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
