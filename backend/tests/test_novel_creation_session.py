"""Tests for novel creation session model and schema."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.models import NovelCreationSession
from app.schemas.novel_creation import (
    NovelCreationSessionCreate,
    NovelCreationSessionRead,
)


class NovelCreationSessionModelTest(unittest.TestCase):
    """Verify the NovelCreationSession model has required fields."""

    def test_has_table_name(self):
        self.assertEqual(NovelCreationSession.__tablename__, "novel_creation_sessions")

    def test_has_required_columns(self):
        columns = {c.name for c in NovelCreationSession.__table__.columns}
        required = {
            "id", "source_project_id", "created_project_id", "status",
            "mode", "user_brief", "target_audience", "genre", "platform",
            "blueprint_json", "review_json", "created_at", "updated_at", "completed_at",
            "schema_version", "current_stage", "revision", "draft_json",
            "checkpoints_json", "last_error_json",
        }
        missing = required - columns
        self.assertEqual(missing, set(), f"Missing columns: {missing}")

    def test_default_status(self):
        col = NovelCreationSession.__table__.columns["status"]
        self.assertEqual(col.default.arg, "drafting")

    def test_default_mode(self):
        col = NovelCreationSession.__table__.columns["mode"]
        self.assertEqual(col.default.arg, "internal_llm")


class NovelCreationSessionSchemaTest(unittest.TestCase):
    """Verify Pydantic schemas for novel creation sessions."""

    def test_create_schema(self):
        data = NovelCreationSessionCreate(
            mode="external_agent",
            user_brief="I want to write a xianxia novel",
            genre="xianxia",
        )
        self.assertEqual(data.mode, "external_agent")
        self.assertEqual(data.genre, "xianxia")

    def test_create_schema_defaults(self):
        data = NovelCreationSessionCreate()
        self.assertEqual(data.mode, "internal_llm")
        self.assertIsNone(data.user_brief)

    def test_read_schema_from_dict(self):
        from datetime import datetime
        data = NovelCreationSessionRead(
            id="s1", status="drafting", mode="internal_llm",
            created_at=datetime(2026, 6, 9),
        )
        self.assertEqual(data.status, "drafting")


if __name__ == "__main__":
    unittest.main()
