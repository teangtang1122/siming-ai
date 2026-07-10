"""Tests for workspace prompt pack integration — internal assistant uses shared packs."""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.agent.prompt_builder import inject_public_prompt_pack_section


class InjectPublicPromptPackSectionTest(unittest.TestCase):
    """Verify inject_public_prompt_pack_section appends public pack data."""

    def test_appends_pack_summary(self):
        db = MagicMock()
        pack = MagicMock()
        pack.pack_id = "chapter_writing_quality"
        pack.title = "质量模式章节写作"
        pack.version = "1.0.0"
        pack.summary = "完整技法的章节写作流程"
        pack.quality_rubric_json = {
            "dimensions": [
                {"name": "opening_hook", "max_score": 10},
                {"name": "plot_progression", "max_score": 10},
            ]
        }
        pack.forbidden_patterns_json = ["仿佛", "不由得", "心中暗想"]

        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = pack
        db.query.return_value = query_mock

        original = "你是写作助手。"
        result = inject_public_prompt_pack_section(original, db, "chapter_writing", "quality")

        self.assertIn("质量模式章节写作", result)
        self.assertIn("1.0.0", result)
        self.assertIn("完整技法", result)
        self.assertIn("opening_hook", result)
        self.assertIn("仿佛", result)

    def test_returns_original_if_no_pack(self):
        db = MagicMock()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        original = "你是写作助手。"
        result = inject_public_prompt_pack_section(original, db, "nonexistent")
        self.assertEqual(result, original)

    def test_returns_original_on_exception(self):
        db = MagicMock()
        db.query.side_effect = Exception("DB error")

        original = "你是写作助手。"
        result = inject_public_prompt_pack_section(original, db, "chapter_writing")
        self.assertEqual(result, original)

    def test_fast_mode_injects_fast_pack(self):
        db = MagicMock()
        pack = MagicMock()
        pack.pack_id = "chapter_writing_fast"
        pack.title = "快速模式"
        pack.version = "1.0.0"
        pack.summary = "少轮次直写"
        pack.quality_rubric_json = None
        pack.forbidden_patterns_json = ["仿佛"]

        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = pack
        db.query.return_value = query_mock

        result = inject_public_prompt_pack_section("原始", db, "chapter_writing", "fast")
        self.assertIn("快速模式", result)


if __name__ == "__main__":
    unittest.main()
