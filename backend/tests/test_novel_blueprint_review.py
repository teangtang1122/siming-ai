"""Tests for novel blueprint review tool."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class BlueprintReviewToolRegisteredTest(unittest.TestCase):
    """Verify review_novel_blueprint is registered."""

    def test_registered(self):
        td = registry.get("review_novel_blueprint")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("review_novel_blueprint", names)


class ReviewNovelBlueprintTest(unittest.TestCase):
    """Verify review_novel_blueprint behavior."""

    def test_missing_session_id_skipped(self):
        from app.services.workspace.tools.novel_creation import review_novel_blueprint
        db = MagicMock()
        result = asyncio.run(review_novel_blueprint(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")

    def test_session_not_found(self):
        from app.services.workspace.tools.novel_creation import review_novel_blueprint
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock
        result = asyncio.run(review_novel_blueprint(db, "p1", {"session_id": "nonexistent"}))
        self.assertEqual(result["status"], "skipped")

    def test_external_agent_mode_returns_rubric(self):
        from app.services.workspace.tools.novel_creation import review_novel_blueprint
        session = MagicMock()
        session.id = "s1"
        session.blueprint_json = {"title": "Test Blueprint"}

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = session
        db.query.return_value = query_mock

        result = asyncio.run(review_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "external_agent",
        }))
        self.assertEqual(result["status"], "ok")
        self.assertIn("review_dimensions", result["data"])
        self.assertIn("output_schema", result["data"])
        self.assertEqual(len(result["data"]["review_dimensions"]), 8)

    def test_saves_blueprint_to_session(self):
        from app.services.workspace.tools.novel_creation import review_novel_blueprint
        session = MagicMock()
        session.id = "s1"
        session.blueprint_json = None

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = session
        db.query.return_value = query_mock

        blueprint = {"title": "New Blueprint", "premise": "Test"}
        result = asyncio.run(review_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "external_agent",
            "blueprint": blueprint,
        }))
        self.assertEqual(result["status"], "ok")
        # Verify blueprint was saved
        self.assertEqual(session.blueprint_json, blueprint)


if __name__ == "__main__":
    unittest.main()
