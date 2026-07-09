"""Tests for the legacy external story update compatibility wrapper."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database.models import (  # noqa: E402
    Base,
    CatalogingCandidate,
    Chapter,
    ChapterSummary,
    Character,
    OutlineNode,
    Project,
)
from app.services.workspace.registry import registry  # noqa: E402


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class ExternalStoryUpdatesToolRegisteredTest(unittest.TestCase):
    """Verify apply_external_story_updates remains registered."""

    def test_registered(self):
        td = registry.get("apply_external_story_updates")
        self.assertIsNotNone(td)
        self.assertEqual(td.tool_type, "write")
        self.assertTrue(td.writes_project_data)

    def test_in_project_writing_pack(self):
        from app.mcp.adapter import list_mcp_tools
        tools = list_mcp_tools(permission_pack="project_writing")
        names = {t.name for t in tools}
        self.assertIn("apply_external_story_updates", names)
        self.assertIn("archive_chapter_after_write", names)


class ApplyExternalStoryUpdatesTest(unittest.TestCase):
    """Verify grouped legacy updates are converted to standard candidates."""

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_project(self):
        db = self.Session()
        project = Project(id="p1", title="Test Novel")
        outline = OutlineNode(
            id="o1",
            project_id="p1",
            node_type="chapter",
            title="第1章 抢网",
            summary="旧摘要",
            status="pending",
        )
        chapter = Chapter(
            id="ch1",
            project_id="p1",
            outline_node_id="o1",
            title="第1章 抢网",
            content="Hero在旧城里等待。\n\n场景二，Hero冲向战场并夺回通讯网。",
            word_count=42,
        )
        character = Character(
            id="c1",
            project_id="p1",
            name="Hero",
            age="3岁",
            appearance="旧外貌",
            current_location="旧城",
            current_goal="等待",
            life_status="alive",
        )
        db.add_all([project, outline, chapter, character])
        db.commit()
        return db

    def test_invalid_updates_skipped(self):
        from app.services.workspace.tools.external_story_updates import apply_external_story_updates
        db = self.Session()
        result = _run(apply_external_story_updates(db, "p1", {"updates": "invalid"}))
        self.assertEqual(result["status"], "skipped")
        db.close()

    def test_manual_mode_stores_candidates_without_applying(self):
        from app.services.workspace.tools.external_story_updates import apply_external_story_updates
        db = self._seed_project()

        result = _run(apply_external_story_updates(db, "p1", {
            "chapter_id": "ch1",
            "updates": {
                "characters": [
                    {"id": "c1", "current_location": "战场", "current_goal": "夺回通讯网"},
                ],
            },
            "mode": "manual",
        }))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["mode"], "manual")
        self.assertGreater(len(result["data"]["candidates"]), 0)
        character = db.query(Character).filter(Character.id == "c1").first()
        self.assertEqual(character.current_location, "旧城")
        self.assertGreater(db.query(CatalogingCandidate).count(), 0)
        db.close()

    def test_auto_mode_applies_standard_archive_candidates(self):
        from app.services.workspace.tools.external_story_updates import apply_external_story_updates
        db = self._seed_project()

        result = _run(apply_external_story_updates(db, "p1", {
            "chapter_id": "ch1",
            "updates": {
                "characters": [
                    {
                        "id": "c1",
                        "age": "4岁",
                        "appearance": "银白短发，左眼有细小光纹",
                        "current_location": "战场",
                    },
                ],
            },
            "mode": "auto",
        }))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["mode"], "auto")
        self.assertGreater(len(result["data"]["applied"]), 0)
        character = db.query(Character).filter(Character.id == "c1").first()
        self.assertEqual(character.age, "4岁")
        self.assertEqual(character.appearance, "银白短发，左眼有细小光纹")
        self.assertEqual(character.current_location, "战场")
        self.assertEqual(character.last_updated_chapter_id, "ch1")
        self.assertIsNotNone(db.query(ChapterSummary).filter(ChapterSummary.chapter_id == "ch1").first())
        item_types = {row.item_type for row in db.query(CatalogingCandidate).all()}
        self.assertIn("chapter_summary", item_types)
        self.assertIn("outline_update", item_types)
        self.assertIn("character_state_update", item_types)
        db.close()

    def test_missing_character_skipped(self):
        from app.services.workspace.tools.external_story_updates import apply_external_story_updates
        db = self._seed_project()

        result = _run(apply_external_story_updates(db, "p1", {
            "chapter_id": "ch1",
            "updates": {
                "characters": [{"id": "missing-character"}],
            },
        }))

        self.assertEqual(result["status"], "ok")
        self.assertGreater(len(result["data"]["skipped"]), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
