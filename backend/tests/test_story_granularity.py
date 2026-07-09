"""Tests for the shared story granularity contract."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.models import Base, Chapter, ChapterSummary, Character, OutlineNode, Project  # noqa: E402
from app.services.story_granularity import inspect_candidate_coverage_items, normalize_outline_batch  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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

    def test_candidate_coverage_warns_when_multiscene_has_no_sections(self):
        coverage = inspect_candidate_coverage_items([
            {"type": "chapter_summary", "summary_text": "多场景章节", "scene_count": 3},
            {"type": "outline_create", "node_type": "chapter", "title": "第151章 抢网", "summary": "抢网。"},
        ])

        self.assertTrue(coverage.is_complete)
        self.assertIn("multi_scene_chapter_without_section_outline", coverage.warnings)
        self.assertIn("no_character_state_candidates", coverage.warnings)


class ArchiveChapterAfterWriteTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_archive_auto_applies_summary_outline_and_character_state(self):
        from app.services.workspace.tools.story_granularity import archive_chapter_after_write

        db = self.Session()
        db.add_all([
            Project(id="p1", title="Test Novel"),
            OutlineNode(id="o1", project_id="p1", node_type="chapter", title="第151章 抢网", summary="计划抢网。"),
            Chapter(
                id="ch151",
                project_id="p1",
                outline_node_id="o1",
                title="第151章 抢网",
                content="司命进入中继站。\n\n第二场，司命夺回网络。",
            ),
            Character(id="c1", project_id="p1", name="司命", age="3岁", current_location="旧城"),
        ])
        db.commit()

        result = _run(archive_chapter_after_write(db, "p1", {
            "chapter_id": "ch151",
            "mode": "auto",
            "source": "internal_writer",
            "candidates": [
                {"type": "chapter_summary", "summary_text": "司命夺回网络。", "scene_count": 2},
                {"type": "outline_update", "title": "第151章 抢网", "node_type": "chapter", "summary": "司命夺回网络。"},
                {"type": "outline_create", "title": "突入中继站", "node_type": "section", "parent_title": "第151章 抢网", "summary": "司命进入中继站。"},
                {"type": "character_state_update", "id": "c1", "name": "司命", "age": "4岁", "current_location": "中继站"},
            ],
        }))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["coverage"]["section_count"], 1)
        self.assertIsNotNone(db.query(ChapterSummary).filter(ChapterSummary.chapter_id == "ch151").first())
        character = db.query(Character).filter(Character.id == "c1").first()
        self.assertEqual(character.age, "4岁")
        self.assertEqual(character.current_location, "中继站")
        section = db.query(OutlineNode).filter(OutlineNode.node_type == "section").first()
        self.assertIsNotNone(section)
        self.assertEqual(section.parent_id, "o1")
        db.close()


if __name__ == "__main__":
    unittest.main()
