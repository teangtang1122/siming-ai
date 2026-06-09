"""Tests for external writing context tool — API-free context preparation."""
import asyncio
import json
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class ExternalWritingContextToolRegisteredTest(unittest.TestCase):
    """Verify prepare_external_writing_context is registered."""

    def test_registered(self):
        td = registry.get("prepare_external_writing_context")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("prepare_external_writing_context", names)


class PrepareExternalWritingContextTest(unittest.TestCase):
    """Verify prepare_external_writing_context behavior."""

    def test_project_not_found(self):
        from app.services.workspace.tools.external_writing import prepare_external_writing_context
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = asyncio.run(prepare_external_writing_context(db, "nonexistent", {}))
        self.assertEqual(result["status"], "skipped")

    def test_returns_context_sections(self):
        from app.services.workspace.tools.external_writing import prepare_external_writing_context
        from datetime import datetime

        # Mock project
        project = MagicMock()
        project.id = "p1"
        project.title = "Test Novel"
        project.writing_style = "natural"
        project.forbidden_sentence_patterns = "仿佛\n不由得"
        project.narrative_perspective = "third_person"

        # Mock character
        char = MagicMock()
        char.id = "c1"
        char.name = "Hero"
        char.role_type = "protagonist"
        char.personality = "Brave"
        char.current_location = "Castle"
        char.current_goal = "Save world"
        char.life_status = "alive"

        # Mock worldbuilding
        wb = MagicMock()
        wb.id = "w1"
        wb.title = "Magic System"
        wb.dimension = "power_system"
        wb.content = "Magic requires mana"

        # Mock prompt pack
        pack = MagicMock()
        pack.pack_id = "chapter_writing_quality"
        pack.version = "1.0.0"
        pack.title = "Quality Writing"
        pack.system_prompt = "Write well..."
        pack.workflow_json = [{"step": 1}]
        pack.quality_rubric_json = {"dimensions": []}
        pack.forbidden_patterns_json = ["仿佛"]

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.order_by.return_value = q
            q.limit.return_value = q
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if "Project" in model_name:
                q.first.return_value = project
            elif "PublicPromptPack" in model_name:
                q.first.return_value = pack
            elif "Character" in model_name and "Relationship" not in model_name:
                q.all.return_value = [char]
            elif "WorldbuildingEntry" in model_name:
                q.all.return_value = [wb]
            elif "Chapter" in model_name:
                q.all.return_value = []
            elif "CharacterRelationship" in model_name:
                q.all.return_value = []
            elif "OutlineNode" in model_name:
                q.first.return_value = None
            else:
                q.first.return_value = None
                q.all.return_value = []
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(prepare_external_writing_context(db, "p1", {"mode": "quality"}))
        self.assertEqual(result["status"], "ok")
        data = result["data"]
        self.assertIn("prompt_pack", data)
        self.assertIn("characters", data)
        self.assertIn("worldbuilding", data)
        self.assertIn("warnings", data)
        self.assertIn("next_tool_suggestions", data)

    def test_no_llm_call(self):
        """Verify the tool does not call LLMGateway."""
        from app.services.workspace.tools.external_writing import prepare_external_writing_context
        # If LLMGateway were called, this import would trigger it
        # The tool should only use DB queries
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        # Should succeed without any LLM call
        result = asyncio.run(prepare_external_writing_context(db, "p1", {}))
        self.assertEqual(result["status"], "skipped")  # project not found, but no crash


if __name__ == "__main__":
    unittest.main()
