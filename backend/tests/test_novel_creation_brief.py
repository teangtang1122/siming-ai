"""Tests for novel creation brief tool."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class NovelCreationBriefToolRegisteredTest(unittest.TestCase):
    """Verify start_novel_creation_session is registered."""

    def test_registered(self):
        td = registry.get("start_novel_creation_session")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("start_novel_creation_session", names)


class StartNovelCreationSessionTest(unittest.TestCase):
    """Verify start_novel_creation_session behavior."""

    def test_creates_session(self):
        from app.services.workspace.tools.novel_creation import start_novel_creation_session
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = asyncio.run(start_novel_creation_session(db, "p1", {
            "genre": "xianxia",
            "user_brief": "A cultivation novel",
        }))
        self.assertEqual(result["status"], "ok")
        self.assertIn("session_id", result["data"])
        self.assertIn("checklist", result["data"])

    def test_checklist_identifies_missing_fields(self):
        from app.services.workspace.tools.novel_creation import start_novel_creation_session
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = asyncio.run(start_novel_creation_session(db, "p1", {
            "genre": "xianxia",
        }))
        checklist = result["data"]["checklist"]
        self.assertIn("target_audience", checklist["missing"])
        self.assertIn("platform", checklist["missing"])
        self.assertFalse(checklist["complete"])

    def test_checklist_complete_when_all_provided(self):
        from app.services.workspace.tools.novel_creation import start_novel_creation_session
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = asyncio.run(start_novel_creation_session(db, "p1", {
            "genre": "xianxia",
            "target_audience": "male",
            "platform": "qidian",
            "user_brief": "Cultivation novel",
        }))
        checklist = result["data"]["checklist"]
        self.assertTrue(checklist["complete"])
        self.assertEqual(checklist["next_action"], "draft_blueprints")


if __name__ == "__main__":
    unittest.main()
