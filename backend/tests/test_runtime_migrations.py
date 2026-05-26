"""Regression tests for runtime database schema compatibility."""

import unittest

from sqlalchemy import create_engine, inspect, text

from app.database.migrations import ensure_runtime_schema
from app.database.models import Base  # noqa: F401 - importing models populates metadata


class RuntimeMigrationTestCase(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
