"""Regression tests for runtime database schema compatibility."""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect, text

from app.database.backup import backup_sqlite_database, sqlite_database_path
from app.database.migrations import ensure_runtime_schema, runtime_schema_needs_sync
from app.database.models import (  # noqa: F401 - importing models populates metadata
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
    Base,
    OperationRun,
)
from app.services.workspace.run_log import create_assistant_run, mark_interrupted_assistant_runs


class RuntimeMigrationTestCase(unittest.TestCase):
    def test_models_404_false_negative_is_reset_for_protocol_reverification(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE api_configs ("
                    "id VARCHAR(36) PRIMARY KEY, provider VARCHAR(50), api_key_encrypted TEXT, "
                    "default_model VARCHAR(100), is_global_default INTEGER DEFAULT 0, "
                    "provider_type VARCHAR(20) DEFAULT 'api', readiness_status VARCHAR(30), readiness_json TEXT)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO api_configs "
                    "(id, provider, api_key_encrypted, default_model, provider_type, readiness_status, readiness_json) "
                    "VALUES ('custom', 'yls', 'encrypted', 'gpt-test', 'api', 'unavailable', "
                    '\'{"source":"manual_verify","message":"HTTP 404 at /codex/models"}\')'
                )
            )

        Base.metadata.create_all(bind=engine)
        ensure_runtime_schema(engine)

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT api_protocol, readiness_status, readiness_json FROM api_configs WHERE provider = 'yls'"
                )
            ).one()
        self.assertEqual(row.api_protocol, "auto")
        self.assertEqual(row.readiness_status, "unverified")
        self.assertIn("protocol_migration", row.readiness_json)

    def test_legacy_model_configs_get_non_destructive_readiness_backfill(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE api_configs ("
                    "id VARCHAR(36) PRIMARY KEY, provider VARCHAR(50), api_key_encrypted TEXT, "
                    "default_model VARCHAR(100), is_global_default INTEGER DEFAULT 0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO api_configs (id, provider, api_key_encrypted, default_model, is_global_default) VALUES "
                    "('global', 'openai', 'encrypted', 'gpt-test', 1), "
                    "('secondary', 'claude_cli', 'encrypted', 'claude-code', 0)"
                )
            )

        Base.metadata.create_all(bind=engine)
        ensure_runtime_schema(engine)

        with engine.connect() as conn:
            rows = {
                row.provider: (row.readiness_status, row.readiness_json)
                for row in conn.execute(
                    text("SELECT provider, readiness_status, readiness_json FROM api_configs")
                )
            }
        self.assertEqual(rows["openai"][0], "ready")
        self.assertIn("legacy_global", rows["openai"][1])
        self.assertEqual(rows["claude_cli"][0], "unverified")
        self.assertIn("legacy_existing", rows["claude_cli"][1])

    def test_runtime_schema_needs_sync_detects_missing_columns(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, title VARCHAR(200))")
            )
        self.assertTrue(runtime_schema_needs_sync(engine))
        Base.metadata.create_all(bind=engine)
        ensure_runtime_schema(engine)
        self.assertFalse(runtime_schema_needs_sync(engine))

    def test_existing_legacy_sqlite_database_gets_new_cataloging_schema(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, title VARCHAR(200))")
            )
            conn.execute(
                text(
                    "CREATE TABLE chapters (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), title VARCHAR(200), content TEXT)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE characters (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), name VARCHAR(200))"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE outline_nodes (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), title VARCHAR(200))"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE worldbuilding_entries (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), title VARCHAR(200), content TEXT)"
                )
            )
            conn.execute(text("INSERT INTO projects (id, title) VALUES ('p1', 'Legacy Project')"))
            conn.execute(
                text(
                    "INSERT INTO chapters (id, project_id, title, content) VALUES ('c1', 'p1', 'Chapter 1', 'text')"
                )
            )
            conn.execute(
                text("INSERT INTO characters (id, project_id, name) VALUES ('ch1', 'p1', 'Hero')")
            )
            conn.execute(
                text(
                    "INSERT INTO outline_nodes (id, project_id, title) VALUES ('o1', 'p1', 'Opening')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO worldbuilding_entries (id, project_id, title, content) VALUES ('w1', 'p1', 'World', 'old')"
                )
            )

        Base.metadata.create_all(bind=engine)
        ensure_runtime_schema(engine)

        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        for table_name in {
            "assistant_runs",
            "assistant_run_steps",
            "cataloging_jobs",
            "cataloging_chapter_runs",
            "cataloging_candidates",
            "cataloging_facts",
            "cataloging_apply_logs",
            "character_aliases",
            "chapter_worldbuilding",
            "worldbuilding_versions",
            "worldbuilding_timeline",
            "local_models",
            "local_runtime_installations",
            "model_download_tasks",
            "model_adapters",
            "model_task_settings",
            "training_datasets",
            "training_jobs",
            "novel_creation_stage_runs",
            "novel_creation_stage_events",
            "worldbuilding_relations",
            "operation_runs",
            "operation_events",
        }:
            self.assertIn(table_name, table_names)

        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        self.assertIn("current_location", character_columns)
        self.assertIn("physical_state", character_columns)
        self.assertIn("last_seen_chapter_id", character_columns)
        self.assertIn("profile_json", character_columns)

        world_columns = {
            column["name"] for column in inspector.get_columns("worldbuilding_entries")
        }
        self.assertIn("first_seen_chapter_id", world_columns)
        self.assertIn("confidence", world_columns)

        outline_columns = {column["name"] for column in inspector.get_columns("outline_nodes")}
        self.assertIn("source_chapter_id", outline_columns)
        self.assertIn("actual_summary", outline_columns)
        self.assertIn("metadata_json", outline_columns)

        creation_columns = {
            column["name"] for column in inspector.get_columns("novel_creation_sessions")
        }
        for column_name in (
            "schema_version",
            "current_stage",
            "revision",
            "draft_json",
            "checkpoints_json",
            "last_error_json",
        ):
            self.assertIn(column_name, creation_columns)

        download_columns = {
            column["name"] for column in inspector.get_columns("model_download_tasks")
        }
        rebuild_columns = {
            column["name"] for column in inspector.get_columns("context_rebuild_jobs")
        }
        operation_columns = {column["name"] for column in inspector.get_columns("operation_runs")}
        self.assertIn("operation_id", download_columns)
        self.assertIn("operation_id", rebuild_columns)
        self.assertIn("attention_json", operation_columns)
        self.assertIn("result_json", operation_columns)

        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM projects")).scalar_one(), 1)
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM chapters")).scalar_one(), 1)

    def test_sqlite_database_is_backed_up_before_runtime_migration(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.execute("CREATE TABLE legacy_data (value TEXT)")
                connection.execute("INSERT INTO legacy_data VALUES ('preserved')")
                connection.commit()
            url = f"sqlite:///{db_path}"

            self.assertEqual(sqlite_database_path(url), db_path.resolve())
            backup_path = backup_sqlite_database(url, reason="pre-test")

            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            self.assertIn("pre-test", backup_path.name)
            with closing(sqlite3.connect(backup_path)) as connection:
                value = connection.execute("SELECT value FROM legacy_data").fetchone()[0]
            self.assertEqual(value, "preserved")

    def test_running_assistant_runs_are_marked_interrupted_on_startup(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            conversation = AssistantConversation(project_id="p1", title="恢复测试")
            db.add(conversation)
            db.flush()
            message = AssistantMessage(
                conversation_id=conversation.id,
                role="assistant",
                sequence_no=1,
                content="正在写作",
                status="running",
            )
            db.add(message)
            db.flush()
            run = AssistantRun(
                project_id="p1",
                conversation_id=conversation.id,
                assistant_message_id=message.id,
                status="running",
                phase="write",
            )
            db.add(run)
            db.flush()
            run_step = AssistantRunStep(
                run_id=run.id,
                project_id="p1",
                step_type="write",
                status="running",
            )
            db.add(run_step)
            db.commit()
            changed = mark_interrupted_assistant_runs(db)
            self.assertEqual(changed, 1)
            run = db.query(AssistantRun).first()
            self.assertEqual(run.status, "interrupted")
            self.assertIn("服务重启", run.error)
            self.assertEqual(db.get(AssistantRunStep, run_step.id).status, "interrupted")
            recovered_message = db.get(AssistantMessage, message.id)
            self.assertEqual(recovered_message.status, "error")
            self.assertEqual(
                json.loads(recovered_message.payload_json)["run"]["status"], "interrupted"
            )
        finally:
            db.close()

    def test_create_assistant_run_is_immediately_recoverable_from_message_payload(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            conversation = AssistantConversation(project_id="p1", title="持久化任务")
            db.add(conversation)
            db.flush()
            message = AssistantMessage(
                conversation_id=conversation.id,
                role="assistant",
                sequence_no=1,
                content="正在分析需求...",
                status="running",
            )
            db.add(message)
            db.commit()

            run = create_assistant_run(
                db,
                project_id="p1",
                conversation_id=conversation.id,
                user_message_id=None,
                assistant_message_id=message.id,
                scope="project",
                model="test-model",
            )

            payload = json.loads(db.get(AssistantMessage, message.id).payload_json)
            self.assertEqual(payload["run"]["id"], run.id)
            self.assertEqual(payload["run"]["operation_id"], run.operation_id)
            self.assertFalse(db.get(OperationRun, run.operation_id).can_retry)
        finally:
            db.close()

    def test_rag_tables_created_by_metadata(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        for name in ("rag_documents", "rag_chunks", "rag_links"):
            self.assertIn(name, table_names, f"RAG table {name} missing after create_all")

    def test_rag_fts5_table_created_by_ensure_runtime_schema(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)

        # Check if FTS5 is available in this SQLite build
        fts5_available = False
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE VIRTUAL TABLE temp.__fts5_test USING fts5(content)"))
                conn.execute(text("DROP TABLE temp.__fts5_test"))
            fts5_available = True
        except Exception:
            pass

        ensure_runtime_schema(engine)

        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        if fts5_available:
            self.assertIn(
                "rag_chunks_fts", table_names, "rag_chunks_fts should exist when FTS5 is available"
            )
        else:
            # FTS5 not available — no crash is the success criterion
            self.assertNotIn("rag_chunks_fts", table_names)

    def test_rag_fts5_table_creation_does_not_fail_when_fts5_unavailable(self):
        """ensure_runtime_schema must not raise even if FTS5 is absent."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        # Should not raise
        ensure_runtime_schema(engine)


if __name__ == "__main__":
    unittest.main()
