"""Tests for the shared story-granularity contract."""
from __future__ import annotations

import os
import sys
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.models import Base, Chapter, OutlineNode, Project  # noqa: E402
from app.services.story_granularity import (  # noqa: E402
    chapter_outline_node,
    inspect_candidate_coverage_items,
    normalize_outline_batch,
    title_has_chapter_number,
)


class StoryGranularityContractTest(unittest.TestCase):
    def test_outline_batch_adds_chapter_number_and_section_parent(self):
        nodes = normalize_outline_batch([
            {"title": "抢网", "node_type": "chapter", "summary": "夺回通讯网。"},
            {"title": "突入中继站", "node_type": "scene", "parent_title": "抢网", "summary": "进入中继站。"},
        ], chapter_number=151)

        self.assertEqual(nodes[0]["title"], "第151章 抢网")
        self.assertEqual(nodes[1]["node_type"], "section")
        self.assertEqual(nodes[1]["parent_title"], "第151章 抢网")
        self.assertTrue(nodes[1]["title"].startswith("第151章 抢网 / "))
        self.assertEqual(nodes[1]["actual_summary"], "进入中继站。")

    def test_outline_batch_preserves_equivalent_chinese_chapter_number(self):
        nodes = normalize_outline_batch([
            {"title": "第一百五十一章 抢网", "node_type": "chapter"},
            {
                "title": "第一百五十一章 抢网 / 突入中继站",
                "node_type": "section",
                "parent_title": "第一百五十一章 抢网",
            },
        ], chapter_number=151)

        self.assertEqual(nodes[0]["title"], "第一百五十一章 抢网")
        self.assertEqual(nodes[1]["parent_title"], "第一百五十一章 抢网")
        self.assertEqual(nodes[1]["title"], "第一百五十一章 抢网 / 突入中继站")

    def test_title_number_check_uses_shared_chinese_parser(self):
        for title in ("第二十五章 风暴", "第〇二五章 风暴", "第 二 十 五 章 风暴"):
            with self.subTest(title=title):
                self.assertTrue(title_has_chapter_number(title, 25))
        self.assertFalse(title_has_chapter_number("第二十六章 风暴", 25))

    def test_candidate_coverage_warns_when_multiscene_has_no_sections(self):
        coverage = inspect_candidate_coverage_items([
            {"type": "chapter_summary", "summary_text": "多场景章节", "scene_count": 3},
            {"type": "outline_create", "node_type": "chapter", "title": "第151章 抢网", "summary": "抢网。"},
        ])

        self.assertFalse(coverage.is_complete)
        self.assertIn("narrative-governance assessment", coverage.missing)
        self.assertIn("no_narrative_governance_assessment", coverage.warnings)
        self.assertIn("multi_scene_chapter_without_section_outline", coverage.warnings)
        self.assertIn("no_character_state_candidates", coverage.warnings)

    def test_explicit_empty_narrative_assessment_is_complete(self):
        coverage = inspect_candidate_coverage_items([
            {
                "type": "chapter_summary",
                "summary_text": "本章没有新增伏笔或未完成行动。",
                "coverage_manifest": {
                    "scene_count": 1,
                    "characters": [],
                    "worldbuilding": [],
                    "relationships": [],
                    "character_profiles": [],
                },
                "narrative_state": {
                    "events": [],
                    "foreshadowing_planted": [],
                    "foreshadowing_resolved": [],
                    "storyline_progress": [],
                    "unresolved_actions": [],
                },
            },
            {
                "type": "outline_update",
                "node_type": "chapter",
                "title": "第151章 抢网",
                "summary": "本章完成例行交接。",
            },
        ])

        self.assertTrue(coverage.is_complete)
        self.assertTrue(coverage.narrative_assessed)
        self.assertEqual(coverage.governance_findings_count, 0)

    def test_cli_parity_rejects_summary_only_granularity(self):
        coverage = inspect_candidate_coverage_items([
            {
                "type": "chapter_summary",
                "summary_text": "本章包含四个场景，并声明了角色与关键设定。",
                "characters": ["陆糖", "陆承宇", "陆家爷爷", "陆景珩"],
                "worldbuilding": ["吐纳法", "灵气波动", "护族大阵", "院门石狮", "陆家宅院"],
                "outline_hint": "本章为多场景章节，包含四个场景。",
                "narrative_state": {"events": [{"description": "陆糖开始吐纳。"}]},
                "narrative_review": {"source": "provided", "findings": []},
            },
            {
                "type": "outline_create",
                "node_type": "chapter",
                "title": "第二章 吐纳",
                "summary": "陆糖开始修炼。",
            },
        ])

        self.assertFalse(coverage.is_complete)
        self.assertEqual(coverage.scene_count, 4)
        self.assertIn("chapter_summary.scene_count coverage declaration", coverage.cli_parity_missing)
        self.assertIn("character_state_update for declared characters (0/4)", coverage.cli_parity_missing)
        self.assertIn("worldbuilding candidates for declared entries (0/5)", coverage.cli_parity_missing)
        self.assertIn("section outlines for declared scenes (0/4)", coverage.cli_parity_missing)
        self.assertIn("chapter_link candidates for declared characters/worldbuilding (0/9)", coverage.cli_parity_missing)
        self.assertIn("chapter_summary.relationships coverage declaration", coverage.cli_parity_missing)
        self.assertIn("chapter_summary.character_profiles coverage declaration", coverage.cli_parity_missing)

    def test_candidate_coverage_counts_narrative_and_scene_state(self):
        coverage = inspect_candidate_coverage_items([
            {
                "type": "chapter_summary",
                "summary_text": "Scene one changes the network.",
                "scene_count": 2,
                "coverage_manifest": {
                    "scene_count": 2,
                    "characters": ["Siming"],
                    "worldbuilding": [],
                    "relationships": [],
                    "character_profiles": [],
                },
                "narrative_state": {
                    "events": [{"description": "The relay opens."}],
                    "timeline_events": [{"description": "Night shift begins."}],
                    "foreshadowing_planted": [{"description": "A dead node blinks."}],
                    "foreshadowing_resolved": [{"description": "The old password works."}],
                    "storyline_progress": [{"description": "Network arc advances."}],
                    "unresolved_actions": [{"description": "Find the source."}],
                },
            },
            {
                "type": "outline_create",
                "node_type": "chapter",
                "title": "Chapter 151",
                "summary": "The relay opens.",
            },
            {
                "type": "outline_create",
                "node_type": "section",
                "title": "Chapter 151 / Relay",
                "parent_title": "Chapter 151",
                "summary": "The relay scene.",
                "scene_number": 1,
                "purpose": "open the relay",
                "unresolved_actions": [{"description": "Trace the signal."}],
            },
            {
                "type": "outline_create",
                "node_type": "section",
                "title": "Chapter 151 / Control room",
                "parent_title": "Chapter 151",
                "summary": "The control-room scene.",
                "scene_number": 2,
                "purpose": "confirm the relay state",
            },
            {"type": "character_state_update", "name": "Siming", "current_location": "Relay"},
            {"type": "chapter_link", "character_names": ["Siming"]},
        ])

        self.assertTrue(coverage.is_complete)
        self.assertEqual(coverage.section_count, 2)
        self.assertEqual(coverage.scene_state_count, 2)
        self.assertEqual(coverage.event_count, 2)
        self.assertEqual(coverage.foreshadowing_planted_count, 1)
        self.assertEqual(coverage.foreshadowing_resolved_count, 1)
        self.assertEqual(coverage.storyline_progress_count, 1)
        self.assertEqual(coverage.unresolved_action_count, 2)

    def test_scene_cards_require_unique_numbers_within_declared_range(self):
        base = [
            {
                "type": "chapter_summary",
                "summary_text": "三个场景依次完成校准。",
                "coverage_manifest": {
                    "scene_count": 3,
                    "characters": [],
                    "worldbuilding": [],
                    "relationships": [],
                    "character_profiles": [],
                },
                "narrative_state": {"events": []},
            },
            {
                "type": "outline_create",
                "node_type": "chapter",
                "title": "第三十八章",
                "summary": "完成校准。",
            },
        ]
        missing_numbers = inspect_candidate_coverage_items(base + [
            {
                "type": "outline_create",
                "node_type": "section",
                "title": f"第三十八章 / 场景{number}",
                "summary": "核对。",
                "purpose": "校准",
            }
            for number in range(1, 4)
        ])
        repeated_and_out_of_range = inspect_candidate_coverage_items(base + [
            {
                "type": "outline_create",
                "node_type": "section",
                "title": f"第三十八章 / 场景{index}",
                "summary": "核对。",
                "scene_number": scene_number,
                "purpose": "校准",
            }
            for index, scene_number in enumerate((1, 1, 4), start=1)
        ])

        self.assertFalse(missing_numbers.is_complete)
        self.assertEqual(missing_numbers.section_count, 0)
        self.assertIn(
            "section outline candidates require unique scene_number within 1..3: missing_or_invalid=3",
            missing_numbers.persistence_missing,
        )
        self.assertFalse(repeated_and_out_of_range.is_complete)
        self.assertEqual(repeated_and_out_of_range.section_count, 1)
        self.assertIn(
            "section outline candidates require unique scene_number within 1..3: duplicate=1, out_of_range=4",
            repeated_and_out_of_range.persistence_missing,
        )

    def test_duplicate_character_cards_cannot_satisfy_two_declared_identities(self):
        coverage = inspect_candidate_coverage_items([
            {
                "type": "chapter_summary",
                "summary_text": "甲与乙进入城门。",
                "coverage_manifest": {
                    "scene_count": 1,
                    "characters": ["甲", "乙"],
                    "worldbuilding": [],
                    "relationships": [],
                    "character_profiles": [],
                },
                "narrative_state": {"events": []},
            },
            {"type": "outline_create", "node_type": "chapter", "title": "第一章", "summary": "入城。"},
            {"type": "character_state_update", "name": "甲", "current_location": "城门"},
            {"type": "character_state_update", "name": "甲", "mental_state": "警惕"},
            {"type": "chapter_link", "character_names": ["甲", "乙"]},
        ])

        self.assertFalse(coverage.is_complete)
        self.assertEqual(coverage.character_state_count, 1)
        self.assertIn(
            "character_state_update for declared characters (1/2)",
            coverage.cli_parity_missing,
        )

    def test_relationship_and_profile_manifests_are_checked_by_identity(self):
        base = [
            {
                "type": "chapter_summary",
                "summary_text": "甲确认乙是自己的师父。",
                "coverage_manifest": {
                    "scene_count": 1,
                    "characters": ["甲", "乙"],
                    "worldbuilding": [],
                    "relationships": [{
                        "source_name": "甲",
                        "target_name": "乙",
                        "relationship_type": "师徒",
                    }],
                    "character_profiles": ["甲"],
                },
                "narrative_state": {"relationship_changes": ["师徒关系得到确认"]},
            },
            {"type": "outline_create", "node_type": "chapter", "title": "第一章", "summary": "认师。"},
            {"type": "character_state_update", "name": "甲", "current_location": "山门"},
            {"type": "character_state_update", "name": "乙", "current_location": "山门"},
            {"type": "chapter_link", "character_names": ["甲", "乙"]},
            {"type": "character_relationship", "source_name": "甲", "target_name": "乙", "relationship_type": "师徒"},
        ]

        identity_only = inspect_candidate_coverage_items([
            *base,
            {"type": "character_update", "name": "甲"},
        ])
        self.assertFalse(identity_only.is_complete)
        self.assertIn(
            "character_create/update for declared profile changes (0/1)",
            identity_only.cli_parity_missing,
        )

        complete = inspect_candidate_coverage_items([
            *base,
            {"type": "character_update", "name": "甲", "background": "乙的弟子。"},
        ])
        self.assertTrue(complete.is_complete)


class ChapterOutlineLookupTest(unittest.TestCase):
    def test_matches_legacy_chinese_number_titles(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        try:
            db.add_all([
                Project(id="legacy-cn", title="Legacy Chinese Novel"),
                OutlineNode(
                    id="legacy-cn-outline",
                    project_id="legacy-cn",
                    node_type="chapter",
                    title="第一百零三章 被删去的火灾",
                    sort_order=103,
                ),
                Chapter(
                    id="legacy-cn-chapter",
                    project_id="legacy-cn",
                    title="第一〇三章 火灾余烬",
                    content="旧正文。",
                ),
            ])
            db.commit()

            chapter = db.query(Chapter).filter(Chapter.id == "legacy-cn-chapter").one()
            outline = chapter_outline_node(db, "legacy-cn", chapter)
            self.assertIsNotNone(outline)
            self.assertEqual(outline.id, "legacy-cn-outline")
        finally:
            db.close()
            Base.metadata.drop_all(engine)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
