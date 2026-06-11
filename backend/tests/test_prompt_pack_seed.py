"""Tests for built-in prompt pack seeding."""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.prompt_packs.seed import BUILTIN_PACKS, seed_builtin_packs, ensure_builtin_packs


class BuiltinPacksDefinitionTest(unittest.TestCase):
    """Verify built-in pack definitions are valid."""

    def test_has_required_packs(self):
        pack_ids = {p["pack_id"] for p in BUILTIN_PACKS}
        required = {
            "new_project_setup",
            "chapter_writing_quality",
            "chapter_writing_fast",
            "chapter_review_quality",
            "character_design",
            "worldbuilding_design",
            "outline_planning",
            "anti_ai_review",
        }
        missing = required - pack_ids
        self.assertEqual(missing, set(), f"Missing packs: {missing}")

    def test_each_pack_has_required_fields(self):
        for pack in BUILTIN_PACKS:
            with self.subTest(pack_id=pack["pack_id"]):
                self.assertIn("pack_id", pack)
                self.assertIn("scope", pack)
                self.assertIn("title", pack)
                self.assertIn("system_prompt", pack)
                self.assertTrue(pack["system_prompt"], f"{pack['pack_id']}: empty system_prompt")

    def test_scopes_are_valid(self):
        valid_scopes = {
            "new_project", "chapter_writing", "chapter_review",
            "character_design", "worldbuilding", "outline_planning",
            "anti_ai_review", "cataloging", "character_change_detection",
            "worldbuilding_detection", "chapter_evaluation", "conflict_suggestion",
        }
        for pack in BUILTIN_PACKS:
            with self.subTest(pack_id=pack["pack_id"]):
                self.assertIn(pack["scope"], valid_scopes,
                              f"{pack['pack_id']}: invalid scope '{pack['scope']}'")

    def test_quality_packs_have_rubric(self):
        quality_packs = {"chapter_writing_quality", "chapter_review_quality"}
        for pack in BUILTIN_PACKS:
            if pack["pack_id"] in quality_packs:
                with self.subTest(pack_id=pack["pack_id"]):
                    self.assertIn("quality_rubric_json", pack)
                    self.assertIsNotNone(pack["quality_rubric_json"])
                    self.assertIn("dimensions", pack["quality_rubric_json"])

    def test_writing_packs_have_forbidden_patterns(self):
        writing_packs = {"chapter_writing_quality", "chapter_writing_fast", "anti_ai_review"}
        for pack in BUILTIN_PACKS:
            if pack["pack_id"] in writing_packs:
                with self.subTest(pack_id=pack["pack_id"]):
                    self.assertIn("forbidden_patterns_json", pack)
                    self.assertIsNotNone(pack["forbidden_patterns_json"])
                    self.assertGreater(len(pack["forbidden_patterns_json"]), 0)

    def test_pack_count(self):
        self.assertEqual(len(BUILTIN_PACKS), 13)


class SeedFunctionTest(unittest.TestCase):
    """Verify seed function behavior."""

    def test_seed_returns_count(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        count = seed_builtin_packs(db)
        self.assertEqual(count, len(BUILTIN_PACKS))
        self.assertEqual(db.add.call_count, len(BUILTIN_PACKS))
        db.commit.assert_called_once()

    def test_seed_idempotent(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        # Simulate all packs already exist
        db.query.return_value.filter.return_value.first.return_value = MagicMock()
        count = seed_builtin_packs(db)
        self.assertEqual(count, 0)
        db.add.assert_not_called()
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
