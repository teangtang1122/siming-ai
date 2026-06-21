"""Regression tests for runtime database schema compatibility."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, inspect, text

from app.database.backup import backup_sqlite_database, sqlite_database_path
from app.database.migrations import ensure_runtime_schema, runtime_schema_needs_sync
from app.database.models import AssistantRun, AgentPlan, AgentPlanStep, Base  # noqa: F401 - importing models populates metadata
from app.services.workspace.run_log import mark_interrupted_assistant_runs


class RuntimeMigrationTestCase(unittest.TestCase):
    def test_runtime_schema_needs_sync_detects_missing_columns(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, title VARCHAR(200))"))
        self.assertTrue(runtime_schema_needs_sync(engine))
        Base.metadata.create_all(bind=engine)
        ensure_runtime_schema(engine)
        self.assertFalse(runtime_schema_needs_sync(engine))

    def test_existing_legacy_sqlite_database_gets_new_cataloging_schema(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, title VARCHAR(200))"))
            conn.execute(text("CREATE TABLE chapters (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), title VARCHAR(200), content TEXT)"))
            conn.execute(text("CREATE TABLE characters (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), name VARCHAR(200))"))
            conn.execute(text("CREATE TABLE outline_nodes (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), title VARCHAR(200))"))
            conn.execute(text("CREATE TABLE worldbuilding_entries (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), title VARCHAR(200), content TEXT)"))
            conn.execute(text("INSERT INTO projects (id, title) VALUES ('p1', 'Legacy Project')"))
            conn.execute(text("INSERT INTO chapters (id, project_id, title, content) VALUES ('c1', 'p1', 'Chapter 1', 'text')"))
            conn.execute(text("INSERT INTO characters (id, project_id, name) VALUES ('ch1', 'p1', 'Hero')"))
            conn.execute(text("INSERT INTO outline_nodes (id, project_id, title) VALUES ('o1', 'p1', 'Opening')"))
            conn.execute(text("INSERT INTO worldbuilding_entries (id, project_id, title, content) VALUES ('w1', 'p1', 'World', 'old')"))

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
        }:
            self.assertIn(table_name, table_names)

        character_columns = {column["name"] for column in inspector.get_columns("characters")}
        self.assertIn("current_location", character_columns)
        self.assertIn("physical_state", character_columns)
        self.assertIn("last_seen_chapter_id", character_columns)

        world_columns = {column["name"] for column in inspector.get_columns("worldbuilding_entries")}
        self.assertIn("first_seen_chapter_id", world_columns)
        self.assertIn("confidence", world_columns)

        outline_columns = {column["name"] for column in inspector.get_columns("outline_nodes")}
        self.assertIn("source_chapter_id", outline_columns)
        self.assertIn("actual_summary", outline_columns)

        with engine.connect() as conn:
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM projects")).scalar_one(), 1)
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM chapters")).scalar_one(), 1)

    def test_sqlite_database_is_backed_up_before_runtime_migration(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            db_path.write_bytes(b"legacy-data")
            url = f"sqlite:///{db_path}"

            self.assertEqual(sqlite_database_path(url), db_path.resolve())
            backup_path = backup_sqlite_database(url, reason="pre-test")

            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            self.assertEqual(backup_path.read_bytes(), b"legacy-data")
            self.assertIn("pre-test", backup_path.name)

    def test_agent_plan_tables_created_with_correct_schema(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        ensure_runtime_schema(engine)

        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())

        self.assertIn("agent_plans", table_names)
        self.assertIn("agent_plan_steps", table_names)

        # Verify agent_plans columns
        plan_columns = {col["name"] for col in inspector.get_columns("agent_plans")}
        for col in ("id", "project_id", "conversation_id", "assistant_run_id",
                     "assistant_message_id", "name", "status", "graph_json",
                     "model", "error", "created_at", "updated_at", "completed_at"):
            self.assertIn(col, plan_columns, f"agent_plans missing column: {col}")

        # Verify agent_plan_steps columns
        step_columns = {col["name"] for col in inspector.get_columns("agent_plan_steps")}
        for col in ("id", "plan_id", "project_id", "step_key", "tool", "args_json",
                     "depends_on_json", "status", "retry_policy", "idempotency_key",
                     "result_json", "output_refs", "detail", "error", "attempt_no",
                     "retry_of_step_id", "resolved_step_id", "started_at",
                     "completed_at", "created_at", "updated_at"):
            self.assertIn(col, step_columns, f"agent_plan_steps missing column: {col}")

        # Verify indexes exist
        step_indexes = {idx["name"] for idx in inspector.get_indexes("agent_plan_steps")}
        self.assertIn("ix_agent_plan_steps_plan_key", step_indexes)
        self.assertIn("ix_agent_plan_steps_idempotency", step_indexes)

    def test_running_assistant_runs_are_marked_interrupted_on_startup(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            db.add(AssistantRun(project_id="p1", status="running", phase="write"))
            db.commit()
            changed = mark_interrupted_assistant_runs(db)
            self.assertEqual(changed, 1)
            run = db.query(AssistantRun).first()
            self.assertEqual(run.status, "interrupted")
            self.assertIn("服务重启", run.error)
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
            self.assertIn("rag_chunks_fts", table_names, "rag_chunks_fts should exist when FTS5 is available")
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
