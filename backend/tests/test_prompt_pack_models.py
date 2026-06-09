"""Tests for PublicPromptPack and MethodCard models and schemas."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.models import PublicPromptPack, MethodCard
from app.schemas.prompt_pack import (
    PublicPromptPackRead,
    PublicPromptPackCreate,
    MethodCardRead,
    MethodCardCreate,
)


class PublicPromptPackModelTest(unittest.TestCase):
    """Verify the PublicPromptPack model has required fields."""

    def test_has_table_name(self):
        self.assertEqual(PublicPromptPack.__tablename__, "public_prompt_packs")

    def test_has_required_columns(self):
        columns = {c.name for c in PublicPromptPack.__table__.columns}
        required = {
            "id", "project_id", "pack_id", "version", "scope", "title",
            "summary", "system_prompt", "workflow_json", "quality_rubric_json",
            "tool_playbook_json", "forbidden_patterns_json", "context_policy_json",
            "output_contract_json", "enabled", "is_builtin", "tags_json",
            "created_at", "updated_at",
        }
        missing = required - columns
        self.assertEqual(missing, set(), f"Missing columns: {missing}")

    def test_default_enabled(self):
        col = PublicPromptPack.__table__.columns["enabled"]
        self.assertTrue(col.default.arg)

    def test_default_version(self):
        col = PublicPromptPack.__table__.columns["version"]
        self.assertEqual(col.default.arg, "1.0.0")


class MethodCardModelTest(unittest.TestCase):
    """Verify the MethodCard model has required fields."""

    def test_has_table_name(self):
        self.assertEqual(MethodCard.__tablename__, "method_cards")

    def test_has_required_columns(self):
        columns = {c.name for c in MethodCard.__table__.columns}
        required = {
            "id", "project_id", "card_id", "version", "title",
            "description", "content_json", "card_type", "enabled",
            "is_builtin", "created_at", "updated_at",
        }
        missing = required - columns
        self.assertEqual(missing, set(), f"Missing columns: {missing}")


class PublicPromptPackSchemaTest(unittest.TestCase):
    """Verify Pydantic schemas for prompt packs."""

    def test_create_schema(self):
        data = PublicPromptPackCreate(
            pack_id="chapter_writing_quality",
            scope="chapter_writing",
            title="Quality Chapter Writing",
            system_prompt="Write chapters following these rules...",
        )
        self.assertEqual(data.pack_id, "chapter_writing_quality")
        self.assertEqual(data.version, "1.0.0")
        self.assertTrue(data.enabled)

    def test_read_schema_from_dict(self):
        from datetime import datetime
        data = PublicPromptPackRead(
            id="pp1", pack_id="chapter_writing_quality",
            version="1.0.0", scope="chapter_writing",
            title="Quality Chapter Writing",
            system_prompt="Write chapters...",
            enabled=True, is_builtin=True,
            created_at=datetime(2026, 6, 9),
        )
        self.assertEqual(data.pack_id, "chapter_writing_quality")
        self.assertTrue(data.is_builtin)


class MethodCardSchemaTest(unittest.TestCase):
    """Verify Pydantic schemas for method cards."""

    def test_create_schema(self):
        data = MethodCardCreate(
            card_id="chapter_writing_workflow",
            title="Chapter Writing Workflow",
            content_json={"steps": ["prepare", "write", "review"]},
            card_type="workflow",
        )
        self.assertEqual(data.card_type, "workflow")
        self.assertTrue(data.enabled)

    def test_read_schema_from_dict(self):
        from datetime import datetime
        data = MethodCardRead(
            id="mc1", card_id="chapter_writing_workflow",
            version="1.0.0", title="Chapter Writing Workflow",
            content_json={"steps": []}, card_type="workflow",
            enabled=True, is_builtin=True,
            created_at=datetime(2026, 6, 9),
        )
        self.assertEqual(data.card_type, "workflow")


if __name__ == "__main__":
    unittest.main()
