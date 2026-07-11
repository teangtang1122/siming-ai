"""Tests for the shared story granularity contract."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.models import Base, CatalogingFact, Chapter, ChapterSummary, Character, OutlineNode, Project  # noqa: E402
from app.services.story_granularity import inspect_candidate_coverage_items, inspect_chapter_granularity, normalize_outline_batch  # noqa: E402


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

    def test_candidate_coverage_counts_narrative_and_scene_state(self):
        coverage = inspect_candidate_coverage_items([
            {
                "type": "chapter_summary",
                "summary_text": "Scene one changes the network.",
                "scene_count": 2,
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
            {"type": "character_state_update", "name": "Siming", "current_location": "Relay"},
        ])

        self.assertTrue(coverage.is_complete)
        self.assertEqual(coverage.section_count, 1)
        self.assertEqual(coverage.scene_state_count, 1)
        self.assertEqual(coverage.event_count, 2)
        self.assertEqual(coverage.foreshadowing_planted_count, 1)
        self.assertEqual(coverage.foreshadowing_resolved_count, 1)
        self.assertEqual(coverage.storyline_progress_count, 1)
        self.assertEqual(coverage.unresolved_action_count, 2)


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

    def test_archive_records_narrative_and_section_scene_facts(self):
        from app.services.workspace.tools.story_granularity import archive_chapter_after_write

        db = self.Session()
        db.add_all([
            Project(id="p2", title="Narrative Novel"),
            OutlineNode(id="o2", project_id="p2", node_type="chapter", title="Chapter 151", summary="Plan relay."),
            Chapter(
                id="ch2",
                project_id="p2",
                outline_node_id="o2",
                title="Chapter 151",
                content="Siming enters the relay.\n\nThe dead node blinks again.",
            ),
            Character(id="c2", project_id="p2", name="Siming", current_location="Old city"),
        ])
        db.commit()

        result = _run(archive_chapter_after_write(db, "p2", {
            "chapter_id": "ch2",
            "mode": "auto",
            "source": "external_agent",
            "candidates": [
                {
                    "type": "chapter_state",
                    "events": [{"description": "Siming enters the relay."}],
                    "timeline_events": [{"description": "Night shift begins."}],
                    "foreshadowing_planted": [{"description": "A dead node blinks again."}],
                    "advanced_storylines": [{"description": "Network arc advances."}],
                    "unresolved_actions": [{"description": "Trace the source."}],
                },
                {"type": "chapter_summary", "summary_text": "Siming enters the relay.", "scene_count": 2},
                {"type": "outline_update", "title": "Chapter 151", "node_type": "chapter", "summary": "Siming enters the relay."},
                {
                    "type": "outline_create",
                    "title": "Chapter 151 / Relay",
                    "node_type": "section",
                    "parent_title": "Chapter 151",
                    "summary": "Relay scene.",
                    "scene_number": 1,
                    "purpose": "enter relay station",
                    "location": "relay station",
                    "unresolved_actions": [{"description": "Trace the source."}],
                },
                {"type": "character_state_update", "id": "c2", "name": "Siming", "current_location": "relay station"},
            ],
        }))

        self.assertEqual(result["status"], "ok")
        coverage = result["data"]["coverage"]
        self.assertEqual(coverage["event_count"], 2)
        self.assertEqual(coverage["foreshadowing_planted_count"], 1)
        self.assertEqual(coverage["storyline_progress_count"], 1)
        self.assertEqual(coverage["scene_state_count"], 1)

        narrative_fact = db.query(CatalogingFact).filter(
            CatalogingFact.chapter_id == "ch2",
            CatalogingFact.fact_type == "chapter_narrative_state",
            CatalogingFact.status == "active",
        ).first()
        self.assertIsNotNone(narrative_fact)
        scene_fact = db.query(CatalogingFact).filter(
            CatalogingFact.chapter_id == "ch2",
            CatalogingFact.fact_type == "section_scene_state",
            CatalogingFact.status == "active",
        ).first()
        self.assertIsNotNone(scene_fact)
        audit = inspect_chapter_granularity(db, "p2", db.query(Chapter).filter(Chapter.id == "ch2").first())
        self.assertEqual(audit["narrative_health"]["chapter_narrative_state_count"], 1)
        self.assertEqual(audit["narrative_health"]["section_scene_state_count"], 1)
        from app.services.context_builders import _build_outline_context
        section = db.query(OutlineNode).filter(OutlineNode.project_id == "p2", OutlineNode.node_type == "section").first()
        context = _build_outline_context(db, "p2", section.id)
        self.assertIn("Chapter 151", context)
        self.assertIn("叙事状态锁", context)
        self.assertIn("Siming enters the relay", context)
        db.close()

    def test_narrative_ledger_is_queryable_and_restored_with_snapshot(self):
        from app.services.chapter_service import ensure_current_snapshot
        from app.services.workspace.tools.chapters import restore_chapter_version
        from app.services.workspace.tools.story_granularity import archive_chapter_after_write, get_narrative_ledger, update_narrative_ledger_entry

        db = self.Session()
        db.add_all([
            Project(id="p3", title="Ledger Novel"),
            OutlineNode(id="o3", project_id="p3", node_type="chapter", title="Chapter 1"),
            Chapter(id="ch3", project_id="p3", outline_node_id="o3", title="Chapter 1", content="First draft."),
        ])
        db.commit()

        first = _run(archive_chapter_after_write(db, "p3", {
            "chapter_id": "ch3", "mode": "auto", "generate_if_missing": False,
            "candidates": [
                {"type": "chapter_summary", "summary_text": "First", "narrative_state": {
                    "events": [{"description": "The gate opens."}],
                    "foreshadowing_planted": [{"description": "A red signal remains."}],
                    "storyline_progress": [{"title": "Gate arc", "storyline": "Gate arc"}],
                }},
                {"type": "outline_update", "title": "Chapter 1", "node_type": "chapter", "summary": "First"},
            ],
        }))
        self.assertTrue(first["data"]["ledger_checkpoint_id"])
        first_snapshot_id = first["data"]["ledger_checkpoint"]["snapshot_id"]
        ledger = _run(get_narrative_ledger(db, "p3", {"types": ["narrative_promise"]}))
        self.assertEqual(ledger["data"]["total"], 1)
        self.assertEqual(ledger["data"]["items"][0]["status"], "open")
        revised = _run(update_narrative_ledger_entry(db, "p3", {
            "entry_id": ledger["data"]["items"][0]["id"],
            "status": "invalidated",
            "note": "Author rejected this hook.",
        }))
        self.assertEqual(revised["status"], "ok")
        invalidated = _run(get_narrative_ledger(db, "p3", {"statuses": ["invalidated"]}))
        self.assertEqual(invalidated["data"]["total"], 1)

        chapter = db.query(Chapter).filter(Chapter.id == "ch3").first()
        chapter.content = "Second draft."
        chapter.current_version = 2
        ensure_current_snapshot(db, chapter, "ai_insert")
        db.commit()
        _run(archive_chapter_after_write(db, "p3", {
            "chapter_id": "ch3", "mode": "auto", "generate_if_missing": False,
            "candidates": [
                {"type": "chapter_summary", "summary_text": "Second", "narrative_state": {
                    "events": [{"description": "The gate closes."}],
                    "foreshadowing_resolved": [{"description": "A red signal remains."}],
                }},
                {"type": "outline_update", "title": "Chapter 1", "node_type": "chapter", "summary": "Second"},
            ],
        }))

        restored = _run(restore_chapter_version(db, "p3", {"chapter_id": "ch3", "snapshot_id": first_snapshot_id}))
        self.assertEqual(restored["status"], "ok")
        self.assertTrue(restored["data"]["ledger_checkpoint_id"])
        promises = _run(get_narrative_ledger(db, "p3", {"types": ["narrative_promise"]}))
        self.assertEqual(promises["data"]["items"][0]["status"], "open")
        db.close()


if __name__ == "__main__":
    unittest.main()
