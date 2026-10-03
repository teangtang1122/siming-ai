"""Prompt-pack tests for the single quality chapter-writing path."""
from __future__ import annotations

import unittest

from app.prompts.packs.chapter_quality import PACK as CQ
from app.prompts.packs.memory_extraction import PACK as ME
from app.prompts.packs.research import PACK as RP
from app.prompts.packs.workspace_quality import PACK as WQ
from app.services.agent.prompt_builder import (
    build_system_prompt,
    compose_chapter_writer_messages,
    get_chapter_pack,
    get_workspace_pack,
)


ALL_PACKS = [WQ, CQ, RP, ME]


class TestPackMetadata(unittest.TestCase):
    def test_all_remaining_packs_have_complete_metadata(self):
        for pack in ALL_PACKS:
            with self.subTest(pack=pack.name):
                self.assertTrue(pack.name)
                self.assertTrue(pack.version)
                self.assertTrue(pack.pack_type)
                self.assertIsInstance(pack.input_fields, list)
                self.assertGreater(pack.max_token_budget, 0)
                self.assertTrue(pack.output_format)
                self.assertTrue(callable(pack.build_system_prompt))


class TestSingleWorkspacePack(unittest.TestCase):
    def test_quality_workspace_prompt_is_the_only_runtime_pack(self):
        self.assertIs(get_workspace_pack(), WQ)
        prompt = WQ.build_system_prompt(
            outline_batch_count=3,
        )
        self.assertIn("函数调用协议", prompt)
        self.assertIn("未入库草稿", prompt)
        self.assertIn("保存并建档", prompt)
        self.assertNotIn("快速模式", prompt)
        self.assertNotIn("create_chapter", prompt)
        self.assertNotIn("update_chapter", prompt)

    def test_quality_workspace_forbids_false_success(self):
        prompt = WQ.build_system_prompt(
            scope="project",
            outline_batch_count=3,
            tool_names=["chapter_writer"],
        )
        self.assertIn("严禁自行编造 ID", prompt)
        self.assertIn("不得回复“已完成”", prompt)


class TestSingleChapterPack(unittest.TestCase):
    def setUp(self):
        self.prompt = CQ.build_system_prompt(
            style_context="第三人称",
        )

    def test_quality_chapter_pack_is_the_only_runtime_pack(self):
        self.assertIs(get_chapter_pack(), CQ)
        self.assertIn("对话", self.prompt)
        self.assertIn("章末", self.prompt)
        self.assertIn("文学技法", self.prompt)
        self.assertNotIn("快速模式", self.prompt)

    def test_base_writing_does_not_bundle_review_or_de_ai(self):
        self.assertIn("只输出正文", self.prompt)
        self.assertNotIn("逐项评分", self.prompt)

    def test_chapter_output_contract_is_plain_prose(self):
        self.assertEqual(CQ.output_format, "prose")
        self.assertIn("不要加任何前言", self.prompt)
        self.assertIn("不要加章节标题", self.prompt)


class TestOtherPacks(unittest.TestCase):
    def test_research_pack_has_separate_search_tools(self):
        self.assertTrue(RP.build_system_prompt())
        self.assertTrue(RP.available_tools)
        self.assertFalse(set(RP.available_tools) & set(RP.unavailable_tools))

    def test_memory_pack_remains_structured_and_evidence_bound(self):
        prompt = ME.build_system_prompt()
        self.assertIsNotNone(ME.output_schema)
        self.assertIn("evidence", prompt.lower())
        self.assertEqual(ME.output_format, "json")


class TestPromptBuilder(unittest.TestCase):
    def test_system_builder_returns_quality_prompt(self):
        result = build_system_prompt(
            WQ,
            outline_batch_count=3,
        )
        self.assertIsInstance(result, str)
        self.assertIn("chapter_writer", result)

    def test_chapter_messages_follow_structured_length_target(self):
        messages = compose_chapter_writer_messages(
            pack=CQ,
            style_context="第三人称",
            outline_context="第一章",
            world_context="现实都市",
            character_profiles="林舟",
            recent_summaries="无",
            minimum_han_characters=2000,
        )
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("本次篇幅参考为 2000 个汉字", messages[1]["content"])
        self.assertNotIn("2200", messages[1]["content"])
        self.assertNotIn("1800-2500", messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
