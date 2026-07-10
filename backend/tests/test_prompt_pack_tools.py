"""Tests for prompt pack tools — list, get, playbook, rubric."""
import asyncio
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.workspace.registry import registry


class PromptPackToolsRegisteredTest(unittest.TestCase):
    """Verify prompt pack tools are registered in the workspace registry."""

    def test_get_moshu_usage_guide_registered(self):
        td = registry.get("get_moshu_usage_guide")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_list_prompt_packs_registered(self):
        td = registry.get("list_prompt_packs")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "read")

    def test_get_prompt_pack_registered(self):
        td = registry.get("get_prompt_pack")
        self.assertIsNotNone(td)

    def test_get_tool_playbook_registered(self):
        td = registry.get("get_tool_playbook")
        self.assertIsNotNone(td)

    def test_get_quality_rubric_registered(self):
        td = registry.get("get_quality_rubric")
        self.assertIsNotNone(td)

    def test_all_prompt_tools_are_readonly(self):
        from app.mcp.permissions import get_tier
        for name in ["get_moshu_usage_guide", "list_prompt_packs", "get_prompt_pack", "get_tool_playbook", "get_quality_rubric"]:
            td = registry.get(name)
            self.assertIsNotNone(td)
            self.assertEqual(get_tier(td), "readonly", f"{name} should be readonly")


class ListPromptPacksTest(unittest.TestCase):
    """Verify list_prompt_packs tool behavior."""

    def test_returns_packs(self):
        from app.services.workspace.tools.prompt_packs import list_prompt_packs
        db = MagicMock()
        # Mock query chain
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        query_mock.all.return_value = []
        db.query.return_value = query_mock

        result = asyncio.run(list_prompt_packs(db, "p1", {}))
        self.assertEqual(result["status"], "ok")
        self.assertIn("items", result["data"])


class GetMoshuUsageGuideTest(unittest.TestCase):
    """Verify the quickstart guide is available without model calls."""

    def test_cataloging_no_api_guide_mentions_external_tools(self):
        from app.services.workspace.tools.prompt_packs import get_moshu_usage_guide
        db = MagicMock()

        with patch("app.services.prompt_packs.seed.ensure_builtin_packs"):
            result = asyncio.run(get_moshu_usage_guide(db, "p1", {"scenario": "cataloging_no_api", "no_api": True}))

        self.assertEqual(result["status"], "ok")
        text = json.dumps(result["data"], ensure_ascii=False)
        self.assertIn("start_external_cataloging_job", text)
        self.assertIn("apply_pending_cataloging", text)
        self.assertIn("start_cataloging_job", text)
        self.assertIn("phase='merged'", text)
        self.assertIn("save_external_cataloging_candidates", text)
        self.assertNotIn("phase='facts'", text)

    def test_quickstart_tells_external_agents_to_store_long_content(self):
        from app.services.workspace.tools.prompt_packs import get_moshu_usage_guide
        db = MagicMock()

        with patch("app.services.prompt_packs.seed.ensure_builtin_packs"):
            result = asyncio.run(get_moshu_usage_guide(db, "p1", {"scenario": "quickstart"}))

        self.assertEqual(result["status"], "ok")
        text = json.dumps(result["data"], ensure_ascii=False)
        self.assertIn("长正文", text)
        self.assertIn("save_external_chapter_draft", text)
        self.assertIn("save_external_cataloging_candidates", text)

    def test_api_free_rules_tell_agents_to_store_long_content(self):
        from app.prompts.prompt_source import get_api_free_mode_rules

        rules = get_api_free_mode_rules()
        self.assertIn("长内容处理规则", rules)
        self.assertIn("save_external_chapter_draft", rules)
        self.assertIn("不要把整章正文", rules)
        self.assertIn("save_external_cataloging_candidates", rules)


class GetPromptPackTest(unittest.TestCase):
    """Verify get_prompt_pack tool behavior."""

    def test_missing_pack_returns_skipped(self):
        from app.services.workspace.tools.prompt_packs import get_prompt_pack
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = asyncio.run(get_prompt_pack(db, "p1", {"scope": "nonexistent"}))
        self.assertEqual(result["status"], "skipped")

    def test_fast_chapter_writing_request_returns_fast_pack(self):
        from app.services.workspace.tools.prompt_packs import get_prompt_pack

        project = MagicMock()
        project.narrative_perspective = "third_person"
        project.writing_style = "natural"
        project.forbidden_sentence_patterns = ""
        project.rhetoric_guidelines = ""
        project.custom_style_prompt = ""
        project.short_sentences = False

        pack = MagicMock()
        pack.pack_id = "chapter_writing_fast"
        pack.version = "1.0.0"
        pack.scope = "chapter_writing"
        pack.title = "快速模式章节写作"
        pack.summary = "少轮次直写"
        pack.workflow_json = []
        pack.quality_rubric_json = {"dimensions": []}
        pack.tool_playbook_json = {}
        pack.forbidden_patterns_json = ["仿佛"]
        pack.context_policy_json = {}
        pack.output_contract_json = {}
        pack.system_prompt = "old"

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "Project" in model_name:
                q.first.return_value = project
            else:
                q.first.return_value = pack
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(get_prompt_pack(db, "p1", {"scope": "chapter_writing", "mode": "fast"}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["pack_id"], "chapter_writing_fast")
        self.assertEqual(result["data"]["effective_pack_id"], "chapter_writing_fast")
        self.assertIn("快速模式定位", result["data"]["system_prompt"])
        self.assertIn("archive_chapter_after_write", result["data"]["system_prompt"])


class ToolRegistrationTest(unittest.TestCase):
    """Verify prompt pack tools appear in MCP tool list."""

    def test_tools_in_readonly_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="readonly_collaboration")
        names = {t.name for t in tools}
        self.assertIn("get_moshu_usage_guide", names)
        self.assertIn("list_prompt_packs", names)
        self.assertIn("get_prompt_pack", names)
        self.assertIn("get_tool_playbook", names)
        self.assertIn("get_quality_rubric", names)


if __name__ == "__main__":
    unittest.main()
