"""Regression tests for shared cataloging behavior prompts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.prompts.cataloging_source import (
    get_external_cataloging_system_prompt,
    get_fact_extraction_rules,
    get_outline_granularity_rules,
)
from app.services.cataloging.orchestrator import _append_incremental_candidate_retry
from app.services.cataloging.staged_prompts import (
    CATALOGING_RESOLUTION_SYSTEM_PROMPT,
    FACT_EXTRACTION_SYSTEM_PROMPT,
)


class CatalogingPromptUnificationTest(unittest.TestCase):
    """Ensure internal and external cataloging share the same critical rules."""

    def test_outline_granularity_is_shared_across_entrypoints(self):
        marker = 'node_type="section"'
        shared = get_outline_granularity_rules()
        external = get_external_cataloging_system_prompt()

        self.assertIn(marker, shared)
        self.assertIn(marker, external)
        self.assertIn(marker, CATALOGING_RESOLUTION_SYSTEM_PROMPT)
        self.assertIn("parent_title", external)
        self.assertIn("parent_title", CATALOGING_RESOLUTION_SYSTEM_PROMPT)

    def test_fact_extraction_rules_are_shared_by_staged_prompt(self):
        shared = get_fact_extraction_rules()

        self.assertIn("只裸读当前章节正文", shared)
        self.assertIn("只裸读当前章节正文", FACT_EXTRACTION_SYSTEM_PROMPT)
        self.assertIn("outline_fact 要覆盖整章节点和重要场景节点", FACT_EXTRACTION_SYSTEM_PROMPT)

    def test_cataloging_prompts_keep_jsonl_protocol_and_readable_examples(self):
        external = get_external_cataloging_system_prompt()
        internal = CATALOGING_RESOLUTION_SYSTEM_PROMPT
        shared_facts = get_fact_extraction_rules()

        for prompt in (external, internal):
            self.assertIn("character_state_update", prompt)
            self.assertIn("worldbuilding_create", prompt)
            self.assertIn("JSONL", prompt)
            self.assertIn("角色", prompt)
            self.assertIn("世界观", prompt)
            self.assertIn("候选", prompt)

        self.assertIn("只输出 JSONL", shared_facts)
        self.assertIn("不要输出 Markdown", shared_facts)

    def test_candidate_prompts_require_summary_and_outline_only_on_initial_generation(self):
        external = get_external_cataloging_system_prompt()
        internal = CATALOGING_RESOLUTION_SYSTEM_PROMPT

        for prompt in (external, internal):
            self.assertIn("首次生成回合的首个响应对象必须同时包含两个必填对象", prompt)
            self.assertIn('"chapter_outline"', prompt)
            self.assertIn("不能只返回其中一个", prompt)

    def test_incremental_repair_prompt_does_not_request_a_full_candidate_replay(self):
        for prompt in (
            get_external_cataloging_system_prompt(),
            CATALOGING_RESOLUTION_SYSTEM_PROMPT,
        ):
            self.assertEqual(prompt.count("【候选缺项自动修复】"), 1)
            self.assertIn("增量修复回合", prompt)
            self.assertIn("不要重发完整候选集", prompt)

        retry_prompt = _append_incremental_candidate_retry(
            "首次生成回合必须输出完整骨架。",
            "缺少角色状态候选：张三",
        )
        self.assertIn("本节规则优先于上面的首次生成要求", retry_prompt)
        self.assertIn("只输出错误信息明确指出的缺失候选", retry_prompt)
        self.assertIn("不要重发完整候选集", retry_prompt)
        self.assertNotIn("重新输出完整标准 JSONL", retry_prompt)

    def test_character_state_schema_tracks_appearance_and_age(self):
        external = get_external_cataloging_system_prompt()

        self.assertIn("character_state_update", external)
        self.assertIn('"appearance":"..."', external)
        self.assertIn('"age":"..."', external)

    def test_state_prompts_preserve_omitted_fields_without_rewriting_existing_cards(self):
        for prompt in (get_external_cataloging_system_prompt(), CATALOGING_RESOLUTION_SYSTEM_PROMPT):
            self.assertIn("未变化或未交代的字段必须省略", prompt)
            self.assertIn("司命保留原值", prompt)
            self.assertIn("通话另一端", prompt)
            self.assertIn("省略 current_location", prompt)
            self.assertIn("appearance_before", prompt)
            self.assertIn("appearance_evidence", prompt)
            self.assertIn("age_before", prompt)
            self.assertIn("age_evidence", prompt)
            self.assertIn("items_or_assets 是整字段替换", prompt)
            self.assertIn("items_or_assets_before", prompt)
            self.assertIn("逐字包含", prompt)
            self.assertIn("同场另一人物", prompt)
            self.assertNotIn("即使没有变化也要输出当前值", prompt)
            self.assertNotIn("必须输出 character_state_update 和 character_update", prompt)

    def test_fact_prompt_requires_direct_actor_evidence_for_location_and_items(self):
        shared = get_fact_extraction_rules()

        self.assertIn("不得从通话另一端所在场景推断", shared)
        self.assertIn("省略 location", shared)
        self.assertIn("必须归属于该 character_fact 的人物", shared)
        self.assertIn("能直接确认人物与地点", shared)

    def test_fact_prompt_does_not_turn_shared_procedure_into_corroboration(self):
        for prompt in (get_fact_extraction_rules(), FACT_EXTRACTION_SYSTEM_PROMPT):
            self.assertIn("只说明程序框架一致，不等于材料互相印证", prompt)
            self.assertIn("来源独立、互不背书或尚未校验", prompt)
            self.assertIn("chapter_overview 和 outline_fact 必须保留该限制", prompt)

    def test_candidate_prompts_disallow_duplicate_relationship_and_link_identities(self):
        for prompt in (get_external_cataloging_system_prompt(), CATALOGING_RESOLUTION_SYSTEM_PROMPT):
            self.assertIn("同一有向角色对只能保留一个当前 relationship_type", prompt)
            self.assertIn("每个角色只出现一次", prompt)
            self.assertIn("选择一个 appearance_type", prompt)

    def test_candidate_prompts_keep_anonymous_roles_out_of_stable_character_cards(self):
        for prompt in (get_external_cataloging_system_prompt(), CATALOGING_RESOLUTION_SYSTEM_PROMPT):
            self.assertIn("未具名岗位", prompt)
            self.assertIn("不得创建角色卡、状态卡、角色关系、角色档案或角色章节关联", prompt)
            self.assertIn("不得因为两个章节都出现相同岗位称谓", prompt)
            self.assertIn("coverage_manifest.characters", prompt)

    def test_candidate_prompts_require_worldbuilding_identity_to_persist_independently(self):
        for prompt in (get_external_cataloging_system_prompt(), CATALOGING_RESOLUTION_SYSTEM_PROMPT):
            self.assertIn("独立生命周期", prompt)
            self.assertIn("操作视角", prompt)
            self.assertIn("阶段汇总", prompt)
            self.assertIn("仅声称“层级不同”不是有效的新建理由", prompt)
            self.assertIn("合并到逐项审阅的旧卡为何会损害其真实身份", prompt)

    def test_candidate_prompts_preserve_existing_character_background_verbatim(self):
        for prompt in (get_external_cataloging_system_prompt(), CATALOGING_RESOLUTION_SYSTEM_PROMPT):
            self.assertIn("background_before", prompt)
            self.assertIn("逐字复制当前完整背景", prompt)
            self.assertIn("禁止改写、缩短或删除旧背景", prompt)


if __name__ == "__main__":
    unittest.main()
