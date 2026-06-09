"""Tests for novel blueprint draft tool."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class BlueprintDraftToolRegisteredTest(unittest.TestCase):
    """Verify draft_novel_blueprint is registered."""

    def test_registered(self):
        td = registry.get("draft_novel_blueprint")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("draft_novel_blueprint", names)


class DraftNovelBlueprintTest(unittest.TestCase):
    """Verify draft_novel_blueprint behavior."""

    def test_missing_session_id_skipped(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint
        db = MagicMock()
        result = asyncio.run(draft_novel_blueprint(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")

    def test_session_not_found(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock
        result = asyncio.run(draft_novel_blueprint(db, "p1", {"session_id": "nonexistent"}))
        self.assertEqual(result["status"], "skipped")

    def test_external_agent_mode_returns_prompt(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint
        from app.database.models import NovelCreationSession

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "Xianxia novel"
        session.genre = "xianxia"
        session.target_audience = "male"
        session.platform = "qidian"

        pack = MagicMock()
        pack.pack_id = "new_project_setup"
        pack.system_prompt = "Create a novel..."
        pack.workflow_json = []

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if "NovelCreationSession" in model_name:
                q.first.return_value = session
            elif "PublicPromptPack" in model_name:
                q.first.return_value = pack
            else:
                q.first.return_value = None
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "external_agent",
        }))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["execution_mode"], "external_agent")
        self.assertIn("output_schema", result["data"])
        self.assertIn("prompt_pack", result["data"])

    def test_internal_mode_returns_hint(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = session
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "internal_llm",
        }))
        self.assertEqual(result["status"], "skipped")
        self.assertIn("external_agent", result["data"]["hint"])


if __name__ == "__main__":
    unittest.main()
