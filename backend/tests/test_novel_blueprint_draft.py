"""Tests for novel blueprint draft tool."""
import asyncio
import sys
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_internal_mode_is_normalized_to_hybrid(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "克苏鲁规则怪谈"
        session.genre = "other"
        session.target_audience = "all"
        session.platform = "qidian"
        session.blueprint_json = []

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = session
            return q

        db = MagicMock()
        db.query.side_effect = query_side_effect

        with patch(
            "app.services.workspace.tools.novel_creation._generate_clarifying_questions",
            return_value=[],
        ), patch(
            "app.services.workspace.tools.novel_creation._try_llm_initial_draft",
            return_value=None,
        ):
            result = asyncio.run(draft_novel_blueprint(db, "p1", {
                "session_id": "s1",
                "execution_mode": "internal_llm",
            }))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["execution_mode"], "hybrid")
        self.assertEqual(result["data"]["enhancement_mode"], "template_fallback")

    def test_selected_model_is_forwarded_to_blueprint_llm(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "克苏鲁规则怪谈"
        session.genre = "other"
        session.target_audience = "all"
        session.platform = "qidian"
        session.blueprint_json = []

        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = session
        db.query.return_value = query_mock

        selected_model = "claude_cli:claude-code"
        with patch(
            "app.services.workspace.tools.novel_creation._generate_clarifying_questions",
            new=AsyncMock(return_value=[]),
        ) as questions_mock, patch(
            "app.services.workspace.tools.novel_creation._try_llm_initial_draft",
            new=AsyncMock(return_value=None),
        ) as draft_mock:
            result = asyncio.run(draft_novel_blueprint(db, "p1", {
                "session_id": "s1",
                "execution_mode": "hybrid",
                "model": selected_model,
            }))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(questions_mock.await_args.kwargs["model"], selected_model)
        self.assertEqual(draft_mock.await_args.kwargs["model"], selected_model)


class SystemAssistantModelOverrideTest(unittest.TestCase):
    def test_system_chat_uses_selected_model(self):
        from app.services.workspace.tools.novel_creation import system_chat_completion

        selected_model = "codex_cli:codex-cli"
        with patch(
            "app.services.workspace.tools.novel_creation.LLMGateway.model_identity",
            return_value=("codex_cli", "codex-cli"),
        ), patch(
            "app.services.workspace.tools.novel_creation._novel_creation_cli_context",
            return_value={"local_cli_cwd": r"D:\novels"},
        ), patch(
            "app.services.workspace.tools.novel_creation.LLMGateway.chat_completion",
            new=AsyncMock(return_value={"content": "你好，我正在使用 Codex CLI。"}),
        ) as completion_mock:
            result = asyncio.run(system_chat_completion(
                message="你是什么模型？",
                context={},
                model=selected_model,
            ))

        self.assertEqual(result["reply"], "你好，我正在使用 Codex CLI。")
        self.assertEqual(completion_mock.await_args.kwargs["model"], selected_model)
        self.assertEqual(completion_mock.await_args.kwargs["timeout"], 180)
        self.assertEqual(
            completion_mock.await_args.kwargs["extra_body"],
            {"local_cli_cwd": r"D:\novels"},
        )

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

    def test_template_auto_names_protagonists_for_cthulhu_rules(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "克苏鲁+规则怪谈"
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

        blueprints = result["data"]["blueprints"]
        names = [bp["protagonist"]["name"] for bp in blueprints]
        titles = [bp["title"] for bp in blueprints]
        self.assertEqual(len(set(names)), 3)
        self.assertNotIn("未命名主角", names)
        self.assertEqual(len(set(titles)), 3)
        self.assertTrue(all("克苏鲁+规则怪谈" not in title for title in titles))
        self.assertTrue(any("旧神" in title or "规则" in title or "怪谈" in title for title in titles))
        self.assertTrue(all(bp["requirement_coverage"]["score"] >= 90 for bp in blueprints))
        self.assertEqual(len({bp["subtitle"] for bp in blueprints}), 3)
        self.assertEqual(len({bp["core_conflict"] for bp in blueprints}), 3)
        self.assertEqual(len({bp["protagonist"]["goal"] for bp in blueprints}), 3)
        self.assertEqual(len({bp["golden_three"]["chapter_1"] for bp in blueprints}), 3)

    def test_template_regenerate_rotates_to_new_story_engines(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "克苏鲁+规则怪谈，至少1000章"
        session.genre = "other"
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

        initial = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "template",
        }))["data"]["blueprints"]
        session.blueprint_json = initial
        regenerated = asyncio.run(draft_novel_blueprint(db, "p1", {
            "session_id": "s1",
            "execution_mode": "template",
            "feedback": "全部重新生成",
            "revision_mode": "regenerate",
        }))["data"]["blueprints"]

        self.assertTrue(set(bp["title"] for bp in initial).isdisjoint(bp["title"] for bp in regenerated))
        self.assertTrue(set(bp["subtitle"] for bp in initial).isdisjoint(bp["subtitle"] for bp in regenerated))
        self.assertNotEqual(
            [bp["protagonist"]["name"] for bp in initial],
            [bp["protagonist"]["name"] for bp in regenerated],
        )

    def test_template_feedback_separately_designs_protagonists_without_false_avoid(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "克苏鲁+规则怪谈"
        session.genre = "xianxia"
        session.target_audience = "all"
        session.platform = "qidian"
        session.blueprint_json = [{"title": "禁忌怪谈档案"}]

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
            "feedback": "分别设计一下主角",
            "revision_mode": "refine",
        }))

        blueprints = result["data"]["blueprints"]
        names = [bp["protagonist"]["name"] for bp in blueprints]
        self.assertEqual(len(set(names)), 3)
        self.assertNotIn("未命名主角", names)
        for bp in blueprints:
            forbidden_text = " ".join(bp["forbidden_patterns"])
            self.assertNotIn("设计一下主角", forbidden_text)
            self.assertTrue(bp["protagonist"]["weakness"])
            self.assertTrue(bp["protagonist"]["opening_pressure"])

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

    def test_repeated_qa_history_generates_without_placeholder_protagonist(self):
        from app.services.workspace.tools.novel_creation import draft_novel_blueprint

        session = MagicMock()
        session.id = "s1"
        session.user_brief = "用户希望创建一部新小说。"
        session.genre = "fantasy"
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

        qa_history = [
            {"question": "小说的类型是什么？", "answer": "玄幻"},
            {"question": "故事的核心冲突是什么？", "answer": "个人与外界的对抗"},
            {"question": "故事的背景设定在什么样的世界观中？", "answer": "现实世界"},
            {"question": "主角的具体身份和背景是什么？", "answer": "特殊能力者"},
            {"question": "主角的具体身份和背景是什么？", "answer": "被秘密组织培养的实验体"},
        ]

        with patch(
            "app.services.workspace.tools.novel_creation._try_llm_initial_draft",
            new=AsyncMock(return_value=None),
        ):
            result = asyncio.run(draft_novel_blueprint(db, "p1", {
                "session_id": "s1",
                "execution_mode": "hybrid",
                "model": "local_llama_cpp:qwen3-4b-q4",
                "qa_history": qa_history,
                "revision_mode": "initial",
            }))

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("questions", result["data"])
        first = result["data"]["blueprints"][0]
        flat_text = str(first)
        self.assertNotEqual(first["protagonist"]["name"], "什么样的人")
        self.assertIn("被秘密组织培养的实验体", flat_text)
        self.assertNotIn("Q:", flat_text)

    def test_extract_protagonist_name_rejects_question_placeholder(self):
        from app.services.workspace.tools.novel_creation import _extract_protagonist_name

        brief = "Q: 你的主角是什么样的人？\nA: 穿越重生型：现代人穿越到修仙世界"
        self.assertEqual(_extract_protagonist_name(brief), "未命名主角")


if __name__ == "__main__":
    unittest.main()
