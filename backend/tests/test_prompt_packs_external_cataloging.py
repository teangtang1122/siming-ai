"""Tests for external cataloging prompt pack."""
import sys
import os
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.prompt_packs.seed import BUILTIN_PACKS


class ExternalCatalogingPackTest(unittest.TestCase):
    """Verify external cataloging prompt pack exists and is valid."""

    def test_pack_exists(self):
        pack_ids = {p["pack_id"] for p in BUILTIN_PACKS}
        self.assertIn("cataloging_external_no_api", pack_ids)

    def test_pack_has_required_fields(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        self.assertIn("scope", pack)
        self.assertIn("title", pack)
        self.assertIn("system_prompt", pack)
        self.assertEqual(pack["scope"], "cataloging")

    def test_pack_has_workflow(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        self.assertIn("workflow_json", pack)
        self.assertIsNotNone(pack["workflow_json"])
        self.assertGreater(len(pack["workflow_json"]), 0)

    def test_pack_has_quality_rubric(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        self.assertIn("quality_rubric_json", pack)
        self.assertIsNotNone(pack["quality_rubric_json"])
        self.assertIn("dimensions", pack["quality_rubric_json"])

    def test_pack_has_forbidden_patterns(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        self.assertIn("forbidden_patterns_json", pack)
        self.assertIsNotNone(pack["forbidden_patterns_json"])
        self.assertGreater(len(pack["forbidden_patterns_json"]), 0)

    def test_pack_forbids_internal_api_tools(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn("start_cataloging_job", prompt)
        self.assertIn("不要调用", prompt)

    def test_pack_requires_source_language_archive(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn("中文小说必须用中文建档", prompt)
        self.assertIn("不要改成英文或拼音", prompt)

    def test_pack_requires_explicit_project_binding(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn("project_id", prompt)
        self.assertIn("current_project_id 为空", prompt)
        self.assertIn("同一个 project_id", prompt)

    def test_pack_documents_external_no_api_flow(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn("get_prompt_pack", prompt)
        self.assertIn("start_external_cataloging_job", prompt)
        self.assertIn("save_external_cataloging_facts", prompt)
        self.assertIn("list_cataloging_facts", prompt)
        self.assertIn("save_external_cataloging_candidates", prompt)
        self.assertIn("apply_pending_cataloging", prompt)
        self.assertIn("phase=\"facts\"", prompt)
        self.assertIn("phase=\"candidates\"", prompt)
        self.assertIn("读取章节正文和档案镜像", prompt)
        self.assertNotIn("merged", prompt)

    def test_pack_requires_unified_outline_granularity(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn('node_type="section"', prompt)
        self.assertIn("parent_title", prompt)
        self.assertIn("2-6", prompt)
        self.assertIn("内部建档、外部 MCP 建档、本机 CLI 建档", prompt)

    def test_pack_requires_explicit_governance_coverage_and_stable_resolution_identity(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn("narrative_review", prompt)
        self.assertIn("没有发现时各数组也写 []", prompt)
        self.assertIn("resolves_item_id", prompt)
        self.assertIn("不得按标题猜测", prompt)

    def test_pack_requires_complete_character_and_relationship_contract(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn("coverage_manifest", prompt)
        self.assertIn("relationships", prompt)
        self.assertIn("character_profiles", prompt)
        self.assertIn("character_relationship", prompt)
        self.assertIn("core_motivation", prompt)
        self.assertIn("未具名岗位", prompt)
        self.assertIn("不得创建角色卡", prompt)

    def test_pack_requires_atomic_summary_outline_skeleton(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn("首个响应对象", prompt)
        self.assertIn('"chapter_outline"', prompt)
        self.assertIn("不能只返回摘要后结束", prompt)
        self.assertIn("空数组是合法结果", prompt)

    def test_seed_contains_one_cataloging_workflow(self):
        source = Path(__file__).resolve().parents[1] / "app" / "services" / "prompt_packs" / "seed.py"
        text = source.read_text(encoding="utf-8")
        self.assertEqual(text.count('"pack_id": "cataloging_external_no_api"'), 1)

    def test_pack_requires_verification(self):
        pack = next(p for p in BUILTIN_PACKS if p["pack_id"] == "cataloging_external_no_api")
        prompt = pack["system_prompt"]
        self.assertIn("验证", prompt)
        self.assertIn("status", prompt)

    def test_total_pack_count(self):
        self.assertEqual(len(BUILTIN_PACKS), 12)


if __name__ == "__main__":
    unittest.main()
