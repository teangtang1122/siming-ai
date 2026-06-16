"""Tests for novel blueprint draft tool."""
import asyncio
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

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

    def test_template_mode_generates_full_blueprints(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "我想写一本仙侠小说，主角叫特昂糖，是三岁穿越女娃，核心卖点是科学思维修仙和病毒追杀。"
        session.genre = "xianxia"
        session.target_audience = "all"
        session.platform = "qidian"

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "NovelCreationSession" in model_name:
                q.first.return_value = session
            else:
                q.first.return_value = None
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "template",
        }))

        self.assertEqual(result["status"], "ok")
        blueprints = result["data"]["blueprints"]
        self.assertEqual(len(blueprints), 3)
        first = blueprints[0]
        self.assertGreaterEqual(len(first["selling_points"]), 4)
        self.assertGreaterEqual(len(first["characters"]), 5)
        self.assertGreaterEqual(len(first["relationships"]), 6)
        self.assertGreaterEqual(len(first["worldbuilding"]), 8)
        self.assertGreaterEqual(len(first["volume_outline"]), 3)
        self.assertGreaterEqual(len(first["outline"]), 12)
        self.assertIn("golden_three", first)
        self.assertIn("chapter_1", first["golden_three"])
        self.assertIn("creative_slots", first)
        self.assertIn("requirement_coverage", first)
        self.assertIn("quality_self_check", first)
        self.assertIn("compiled_brief", result["data"])

    def test_template_mode_refines_from_feedback(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "我想写一本仙侠小说，主角叫特昂糖，是三岁穿越女娃。"
        session.genre = "xianxia"
        session.target_audience = "all"
        session.platform = "qidian"
        session.blueprint_json = [{"title": "特昂糖的灵石账簿"}]

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "NovelCreationSession" in model_name:
                q.first.return_value = session
            else:
                q.first.return_value = None
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "template",
            "feedback": "更暗黑一点，把病毒线提前",
            "revision_mode": "refine",
        }))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["revision_mode"], "refine")
        first = result["data"]["blueprints"][0]
        self.assertEqual(first["title"], "特昂糖的灵石账簿")
        self.assertEqual(first["revision_instruction"], "更暗黑一点，把病毒线提前")
        self.assertEqual(first["adjustment_notes"]["label"], "暗线压迫")
        self.assertTrue(any("本轮调整重点" in point for point in first["selling_points"]))


    def test_template_refine_does_not_call_llm_by_default(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "xianxia virus novel"
        session.genre = "xianxia"
        session.target_audience = "all"
        session.platform = "qidian"
        session.blueprint_json = [{"title": "Existing Title"}]

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "NovelCreationSession" in model_name:
                q.first.return_value = session
            else:
                q.first.return_value = None
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        with patch(
            "app.services.workspace.tools.novel_creation._try_llm_blueprint_refinement",
            side_effect=AssertionError("template refine should not call LLM unless enhance_with_llm=true"),
        ):
            result = asyncio.run(draft_novel_blueprint(db, "p1", {
                "session_id": "s1",
                "execution_mode": "template",
                "feedback": "make it darker",
                "revision_mode": "refine",
            }))

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["data"]["enhance_with_llm"])

    def test_template_honors_long_hybrid_brief_constraints(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "我想写1000章，克苏鲁+修仙+规则怪谈，主角叫陆知微"
        session.genre = "xianxia"
        session.target_audience = "all"
        session.platform = "qidian"
        session.blueprint_json = []

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "NovelCreationSession" in model_name:
                q.first.return_value = session
            else:
                q.first.return_value = None
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "template",
        }))

        self.assertEqual(result["status"], "ok")
        first = result["data"]["blueprints"][0]
        self.assertEqual(first["estimated_chapters"], 1000)
        self.assertIn("克苏鲁", first["genre"])
        self.assertIn("修仙", first["genre"])
        self.assertIn("规则怪谈", first["genre"])
        self.assertEqual(first["protagonist"]["name"], "陆知微")
        self.assertGreaterEqual(len(first["volume_outline"]), 8)
        self.assertGreaterEqual(len(first["outline"]), 30)
        self.assertGreaterEqual(first["requirement_coverage"]["score"], 90)
        self.assertIn("篇幅 1000 章", first["requirement_coverage"]["covered"])
        self.assertIn("克苏鲁", first["creative_slots"]["genre_fusion"])

    def test_template_refine_can_rename_protagonist(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "我想写一部修仙规则怪谈"
        session.genre = "xianxia"
        session.target_audience = "all"
        session.platform = "qidian"
        session.blueprint_json = [{"title": "旧日仙途怪谈录"}]

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "NovelCreationSession" in model_name:
                q.first.return_value = session
            else:
                q.first.return_value = None
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "template",
            "feedback": "把主角命名为沈夜",
            "revision_mode": "refine",
        }))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["blueprints"][0]["protagonist"]["name"], "沈夜")

    def test_template_preserves_unusual_custom_motifs(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "我想写800章，梦境审判+灵魂税收+修仙，主角叫闻灯，不要后宫"
        session.genre = "xianxia"
        session.target_audience = "all"
        session.platform = "qidian"
        session.blueprint_json = []

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "NovelCreationSession" in model_name:
                q.first.return_value = session
            else:
                q.first.return_value = None
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        result = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "template",
        }))

        first = result["data"]["blueprints"][0]
        flat_text = str(first)
        self.assertEqual(first["estimated_chapters"], 800)
        self.assertEqual(first["protagonist"]["name"], "闻灯")
        self.assertIn("梦境审判", flat_text)
        self.assertIn("灵魂税收", flat_text)
        self.assertTrue(any("后宫" in item for item in first["forbidden_patterns"]))
        self.assertGreaterEqual(first["requirement_coverage"]["score"], 85)


if __name__ == "__main__":
    unittest.main()
