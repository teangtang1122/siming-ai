"""Tests for apply novel blueprint tool."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class ApplyBlueprintToolRegisteredTest(unittest.TestCase):
    """Verify apply_novel_blueprint is registered."""

    def test_registered(self):
        td = registry.get("apply_novel_blueprint")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")
        self.assertTrue(td.writes_project_data)

    def test_in_project_writing_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="project_management")
        names = {t.name for t in tools}
        self.assertIn("apply_novel_blueprint", names)


class ApplyNovelBlueprintTest(unittest.TestCase):
    """Verify apply_novel_blueprint behavior."""

    def test_missing_session_id_skipped(self):
        from app.services.workspace.tools.novel_creation import apply_novel_blueprint
        db = MagicMock()
        result = asyncio.run(apply_novel_blueprint(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")

    def test_session_not_found(self):
        from app.services.workspace.tools.novel_creation import apply_novel_blueprint
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock
        result = asyncio.run(apply_novel_blueprint(db, "p1", {"session_id": "nonexistent"}))
        self.assertEqual(result["status"], "skipped")

    def test_no_blueprint_skipped(self):
        from app.services.workspace.tools.novel_creation import apply_novel_blueprint
        session = MagicMock()
        session.id = "s1"
        session.blueprint_json = None

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = session
        db.query.return_value = query_mock

        result = asyncio.run(apply_novel_blueprint(db, "p1", {"session_id": "s1"}))
        self.assertEqual(result["status"], "skipped")
        self.assertIn("blueprint", result["detail"].lower())

    def test_manual_mode_returns_candidates(self):
        from app.services.workspace.tools.novel_creation import apply_novel_blueprint
        session = MagicMock()
        session.id = "s1"
        session.blueprint_json = {
            "title": "Test Novel",
            "premise": "A test story",
            "protagonist": {"name": "Hero", "goal": "Save world"},
            "characters": [{"name": "Villain", "role_type": "antagonist"}],
            "worldbuilding": [{"title": "Magic System", "content": "Mana-based"}],
            "outline": [{"title": "Chapter 1"}, {"title": "Chapter 2"}],
        }

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = session
        db.query.return_value = query_mock

        result = asyncio.run(apply_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "mode": "manual",
        }))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["mode"], "manual")
        self.assertGreater(len(result["data"]["candidates"]), 0)

    def test_auto_mode_creates_project(self):
        from app.services.workspace.tools.novel_creation import apply_novel_blueprint
        session = MagicMock()
        session.id = "s1"
        session.blueprint_json = {
            "title": "Test Novel",
            "premise": "A test story",
            "protagonist": {"name": "Hero", "goal": "Save world"},
        }

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = session
        db.query.return_value = query_mock

        result = asyncio.run(apply_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "mode": "auto",
        }))
        self.assertEqual(result["status"], "ok")
        self.assertIn("project_id", result["data"])
        self.assertEqual(session.status, "completed")


if __name__ == "__main__":
    unittest.main()
