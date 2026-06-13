"""Unit tests for the Prompt Pack system."""
from __future__ import annotations

import unittest

from app.prompts.packs import PromptPack
from app.prompts.packs.workspace_fast import PACK as WF
from app.prompts.packs.workspace_quality import PACK as WQ
from app.prompts.packs.chapter_fast import PACK as CF
from app.prompts.packs.chapter_quality import PACK as CQ
from app.prompts.packs.cataloging import FACT_EXTRACTION_PACK, RESOLUTION_PACK, LEGACY_PACK
from app.prompts.packs.research import PACK as RESEARCH
from app.prompts.packs.memory_extraction import PACK as MEM


ALL_PACKS = [WF, WQ, CF, CQ, FACT_EXTRACTION_PACK, RESOLUTION_PACK, LEGACY_PACK, RESEARCH, MEM]


class TestPackMetadata(unittest.TestCase):
    """Every pack must declare all required metadata fields."""

    def test_all_packs_have_name(self):
        for p in ALL_PACKS:
            self.assertTrue(p.name, f"Pack missing name: {p}")

    def test_all_packs_have_version(self):
        for p in ALL_PACKS:
            self.assertTrue(p.version, f"{p.name} missing version")

    def test_all_packs_have_pack_type(self):
        valid_types = {"workspace", "chapter", "cataloging", "research", "memory"}
        for p in ALL_PACKS:
            self.assertIn(p.pack_type, valid_types, f"{p.name} has invalid pack_type: {p.pack_type}")

    def test_all_packs_have_input_fields(self):
        for p in ALL_PACKS:
            self.assertIsInstance(p.input_fields, list)
            self.assertGreater(len(p.input_fields), 0, f"{p.name} has no input_fields")

    def test_all_packs_have_token_budget(self):
        for p in ALL_PACKS:
            self.assertGreater(p.max_token_budget, 0, f"{p.name} has no token budget")

    def test_all_packs_have_output_format(self):
        valid = {"prose", "json", "jsonl", "text_reply"}
        for p in ALL_PACKS:
            self.assertIn(p.output_format, valid, f"{p.name} has invalid output_format: {p.output_format}")

    def test_all_packs_have_forbidden_behaviors(self):
        for p in ALL_PACKS:
            self.assertIsInstance(p.forbidden_behaviors, list)
            self.assertGreater(len(p.forbidden_behaviors), 0, f"{p.name} has no forbidden_behaviors")

    def test_all_packs_have_tool_policy(self):
        valid = {"full", "search_only", "none", "custom"}
        for p in ALL_PACKS:
            self.assertIn(p.tool_policy, valid, f"{p.name} has invalid tool_policy: {p.tool_policy}")

    def test_all_packs_have_build_system_prompt(self):
        for p in ALL_PACKS:
            self.assertTrue(callable(p.build_system_prompt), f"{p.name} build_system_prompt not callable")


class TestWorkspacePacks(unittest.TestCase):
    """Workspace pack structural invariants."""

    def test_fast_pack_builds_nonempty_prompt(self):
        prompt = WF.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 100)

    def test_quality_pack_builds_nonempty_prompt(self):
        prompt = WQ.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertGreater(len(prompt), 100)

    def test_fast_prompt_same_as_quality(self):
        fast = WF.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        quality = WQ.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertEqual(fast, quality)

    def test_quality_pack_requires_evaluate_chapter(self):
        prompt = WQ.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertIn("evaluate_chapter", prompt)

    def test_fast_pack_mandates_quality_evaluation(self):
        prompt = WF.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertIn("evaluate_chapter", prompt)
        self.assertIn("质量模式", prompt)

    def test_fast_pack_includes_quality_tools(self):
        for tool in ["evaluate_chapter", "design_plot", "roleplay_character", "dialogue_battle"]:
            self.assertIn(tool, WF.available_tools)

    def test_quality_pack_includes_quality_tools(self):
        for tool in ["evaluate_chapter", "design_plot", "roleplay_character", "dialogue_battle"]:
            self.assertIn(tool, WQ.available_tools)

    def test_fast_pack_keeps_maintenance_tools(self):
        """Fast mode still needs post-write character/worldbuilding sync."""
        self.assertIn("detect_character_changes", WF.available_tools)
        self.assertIn("detect_new_worldbuilding", WF.available_tools)

    def test_both_packs_have_function_calling_protocol(self):
        for pack in [WF, WQ]:
            prompt = pack.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
            self.assertIn("函数调用", prompt, f"{pack.name} missing function calling protocol")

    def test_quality_pack_forbids_fabricated_ids(self):
        prompt = WQ.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertIn("严禁自行编造 ID", prompt)

    def test_scope_labels_work(self):
        for scope in ["outline", "characters", "worldbuilding", "project"]:
            prompt = WF.build_system_prompt(scope=scope, outline_batch_count=3, auto_apply=True)
            self.assertGreater(len(prompt), 100)

    def test_quality_pack_tool_policy_is_full(self):
        self.assertEqual(WQ.tool_policy, "full")

    def test_fast_pack_tool_policy_is_full(self):
        self.assertEqual(WF.tool_policy, "full")


class TestChapterPacks(unittest.TestCase):
    """Chapter writer pack invariants."""

    def test_quality_includes_craft_rules(self):
        prompt = CQ.build_system_prompt(style_context="测试风格")
        self.assertIn("身体细节替代情绪词", prompt)

    def test_quality_includes_dialogue_rules(self):
        prompt = CQ.build_system_prompt(style_context="测试风格")
        self.assertIn("对话核心规则", prompt)

    def test_quality_includes_anti_ai_rules(self):
        prompt = CQ.build_system_prompt(style_context="测试风格")
        self.assertIn("仿佛", prompt)  # tier1 banned word

    def test_quality_includes_hooks(self):
        prompt = CQ.build_system_prompt(style_context="测试风格")
        self.assertIn("章首引子", prompt)
        self.assertIn("章末钩子", prompt)

    def test_quality_includes_literary_techniques(self):
        prompt = CQ.build_system_prompt(style_context="测试风格")
        self.assertIn("文学技法", prompt)

    def test_fast_prompt_shorter_than_quality(self):
        fast = CF.build_system_prompt(style_context="测试风格")
        quality = CQ.build_system_prompt(style_context="测试风格")
        self.assertEqual(fast, quality)

    def test_fast_word_target_lower(self):
        prompt = CF.build_system_prompt(style_context="测试风格")
        self.assertIn("1800-2500", prompt)

    def test_quality_word_target(self):
        prompt = CQ.build_system_prompt(style_context="测试风格")
        self.assertIn("1800-2500", prompt)

    def test_both_forbid_meta_commentary(self):
        for pack in [CF, CQ]:
            prompt = pack.build_system_prompt(style_context="")
            self.assertTrue(
                "不要加任何前言" in prompt or "不要加任何说明" in prompt or "不要加任何前言、后记" in prompt,
                f"{pack.name} must forbid meta-commentary"
            )

    def test_both_forbid_markdown(self):
        for pack in [CF, CQ]:
            prompt = pack.build_system_prompt(style_context="")
            self.assertIn("Markdown", prompt, f"{pack.name} must forbid Markdown")

    def test_both_forbid_chapter_title(self):
        for pack in [CF, CQ]:
            prompt = pack.build_system_prompt(style_context="")
            self.assertIn("不要加章节标题", prompt, f"{pack.name} must forbid chapter title")

    def test_fast_includes_banned_words(self):
        prompt = CF.build_system_prompt(style_context="")
        self.assertIn("仿佛", prompt)

    def test_fast_includes_forbidden_sentence_templates(self):
        prompt = CF.build_system_prompt(style_context="")
        self.assertIn("去AI味", prompt)

    def test_fast_includes_rhetoric_limits(self):
        prompt = CF.build_system_prompt(style_context="")
        self.assertIn("文学技法", prompt)

    def test_fast_includes_dialogue_core_rules(self):
        prompt = CF.build_system_prompt(style_context="")
        self.assertIn("对话核心规则", prompt)


class TestCatalogingPacks(unittest.TestCase):
    """Cataloging pack invariants."""

    def test_fact_extraction_prompt_builds(self):
        prompt = FACT_EXTRACTION_PACK.build_system_prompt()
        self.assertIn("JSONL", prompt)

    def test_resolution_prompt_builds(self):
        prompt = RESOLUTION_PACK.build_system_prompt()
        self.assertIn("JSONL", prompt)

    def test_legacy_prompt_builds(self):
        prompt = LEGACY_PACK.build_system_prompt()
        self.assertGreater(len(prompt), 100)

    def test_fact_extraction_forbids_write_types(self):
        forbidden = FACT_EXTRACTION_PACK.forbidden_behaviors
        self.assertTrue(any("写库" in f for f in forbidden))

    def test_resolution_forbids_unchanged_fields(self):
        forbidden = RESOLUTION_PACK.forbidden_behaviors
        self.assertTrue(any("未变化" in f for f in forbidden))


class TestResearchPack(unittest.TestCase):
    """Research pack invariants."""

    def test_prompt_builds(self):
        prompt = RESEARCH.build_system_prompt()
        self.assertIn("web_search", prompt)

    def test_has_available_tools(self):
        self.assertIn("web_search", RESEARCH.available_tools)

    def test_has_unavailable_tools(self):
        self.assertIn("fetch_url", RESEARCH.unavailable_tools)
        self.assertIn("extract_page", RESEARCH.unavailable_tools)

    def test_unavailable_not_in_available(self):
        for tool in RESEARCH.unavailable_tools:
            self.assertNotIn(tool, RESEARCH.available_tools)


class TestMemoryExtractionPack(unittest.TestCase):
    """Memory extraction pack invariants."""

    def test_prompt_builds(self):
        prompt = MEM.build_system_prompt()
        self.assertIn("JSON", prompt)

    def test_output_schema_defined(self):
        from app.prompts.packs.memory_extraction import OUTPUT_SCHEMA
        self.assertIsNotNone(OUTPUT_SCHEMA)
        self.assertEqual(OUTPUT_SCHEMA["type"], "array")

    def test_forbids_low_importance(self):
        forbidden = MEM.forbidden_behaviors
        self.assertTrue(any("importance" in f.lower() for f in forbidden))

    def test_forbids_non_json_output(self):
        forbidden = MEM.forbidden_behaviors
        self.assertTrue(any("JSON" in f for f in forbidden))

    def test_requires_evidence(self):
        forbidden = MEM.forbidden_behaviors
        self.assertTrue(any("evidence" in f for f in forbidden))


class TestToolCallRules(unittest.TestCase):
    """Verify tool call protocol rules are present."""

    def test_workspace_fast_has_tool_protocol(self):
        prompt = WF.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertIn("函数调用", prompt)

    def test_workspace_fast_delegates_to_quality_prompt(self):
        fast = WF.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        quality = WQ.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertEqual(fast, quality)

    def test_workspace_quality_has_tool_protocol(self):
        prompt = WQ.build_system_prompt(scope="project", outline_batch_count=3, auto_apply=True)
        self.assertIn("函数调用", prompt)


class TestOutputLimits(unittest.TestCase):
    """Verify output format constraints are declared."""

    def test_chapter_quality_output_format(self):
        self.assertEqual(CQ.output_format, "prose")

    def test_chapter_fast_output_format(self):
        self.assertEqual(CF.output_format, "prose")

    def test_cataloging_output_format(self):
        self.assertEqual(FACT_EXTRACTION_PACK.output_format, "jsonl")

    def test_memory_extraction_output_format(self):
        self.assertEqual(MEM.output_format, "json")

    def test_workspace_output_format(self):
        self.assertEqual(WF.output_format, "text_reply")
        self.assertEqual(WQ.output_format, "text_reply")


class TestPromptBuilder(unittest.TestCase):
    """Test the prompt_builder composition logic."""

    def test_build_system_prompt_returns_string(self):
        from app.services.agent.prompt_builder import build_system_prompt
        result = build_system_prompt(WF, scope="project", outline_batch_count=3, auto_apply=True)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 100)

    def test_build_system_prompt_includes_tool_policy(self):
        from app.services.agent.prompt_builder import build_system_prompt
        result = build_system_prompt(WF, scope="project", outline_batch_count=3, auto_apply=True)
        self.assertIn("工具策略", result)

    def test_compose_chapter_writer_messages_returns_two_messages(self):
        from app.services.agent.prompt_builder import compose_chapter_writer_messages
        messages = compose_chapter_writer_messages(
            pack=CQ,
            style_context="测试风格",
            outline_context="测试大纲",
            world_context="测试世界观",
            character_profiles="测试角色",
            recent_summaries="暂无前文章节。",
        )
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")

    def test_compose_chapter_writer_fast_uses_quality_word_target(self):
        from app.services.agent.prompt_builder import compose_chapter_writer_messages
        messages = compose_chapter_writer_messages(
            pack=CF,
            style_context="测试风格",
            outline_context="测试大纲",
            world_context="测试世界观",
            character_profiles="测试角色",
            recent_summaries="暂无前文章节。",
        )
        user_msg = messages[1]["content"]
        self.assertIn("1800-2500", user_msg)

    def test_compose_chapter_writer_quality_word_target(self):
        from app.services.agent.prompt_builder import compose_chapter_writer_messages
        messages = compose_chapter_writer_messages(
            pack=CQ,
            style_context="测试风格",
            outline_context="测试大纲",
            world_context="测试世界观",
            character_profiles="测试角色",
            recent_summaries="暂无前文章节。",
        )
        user_msg = messages[1]["content"]
        self.assertIn("1800-2500", user_msg)

    def test_get_workspace_pack_defaults_to_quality(self):
        from app.services.agent.prompt_builder import get_workspace_pack
        pack = get_workspace_pack("unknown_mode")
        self.assertEqual(pack.name, "workspace_quality")

    def test_get_workspace_pack_fast(self):
        from app.services.agent.prompt_builder import get_workspace_pack
        pack = get_workspace_pack("fast")
        self.assertEqual(pack.name, "workspace_quality")

    def test_get_chapter_pack_defaults_to_quality(self):
        from app.services.agent.prompt_builder import get_chapter_pack
        pack = get_chapter_pack("unknown_mode")
        self.assertEqual(pack.name, "chapter_quality")

    def test_get_chapter_pack_fast(self):
        from app.services.agent.prompt_builder import get_chapter_pack
        pack = get_chapter_pack("fast")
        self.assertEqual(pack.name, "chapter_quality")

    def test_inject_assistant_mode_for_chapter_writer(self):
        from app.services.agent.prompt_builder import inject_assistant_mode
        action = {"tool": "chapter_writer", "arguments": {"outline_node_id": "123"}}
        result = inject_assistant_mode(action, "fast")
        self.assertEqual(result["arguments"]["mode"], "fast")

    def test_inject_assistant_mode_skips_other_tools(self):
        from app.services.agent.prompt_builder import inject_assistant_mode
        action = {"tool": "create_chapter", "arguments": {"title": "test"}}
        result = inject_assistant_mode(action, "fast")
        self.assertNotIn("mode", result["arguments"])

    def test_inject_assistant_mode_preserves_existing_mode(self):
        from app.services.agent.prompt_builder import inject_assistant_mode
        action = {"tool": "chapter_writer", "arguments": {"outline_node_id": "123", "mode": "quality"}}
        result = inject_assistant_mode(action, "fast")
        self.assertEqual(result["arguments"]["mode"], "quality")


if __name__ == "__main__":
    unittest.main()
