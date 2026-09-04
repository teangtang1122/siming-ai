"""End-to-end test for external no-API cataloging (EAC-0502).

Proves that a novel can be cataloged without Siming LLM calls by using
the external cataloging tools directly with test-provided data.
"""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = "sqlite:///./test_external_cataloging_e2e.db"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    Project,
    Chapter,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingCandidate,
    Character,
    OutlineNode,
    ChapterSummary,
    ChapterGovernanceReview,
    WorldbuildingEntry,
)

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
TestSession = sessionmaker(bind=engine)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _summary_candidate(
    summary: str,
    *,
    characters=None,
    worldbuilding=None,
    relationships=None,
    character_profiles=None,
):
    return {
        "type": "chapter_summary",
        "summary_text": summary,
        "coverage_manifest": {
            "scene_count": 1,
            "characters": list(characters or []),
            "worldbuilding": list(worldbuilding or []),
            "relationships": list(relationships or []),
            "character_profiles": list(character_profiles or []),
        },
        "narrative_state": {
            "events": [],
            "timeline_events": [],
            "foreshadowing_planted": [],
            "foreshadowing_resolved": [],
            "storyline_progress": [],
            "new_storylines": [],
            "reader_known_facts": [],
            "character_known_facts": [],
            "unresolved_actions": [],
        },
        "narrative_review": {"source": "provided", "outcome": "assessed"},
    }


def _overview_fact(
    summary: str,
    *,
    cataloging_characters=None,
    anonymous_participants=None,
    cataloging_worldbuilding_titles=None,
    incidental_worldbuilding_mentions=None,
):
    return {
        "fact_type": "chapter_overview",
        "evidence": summary,
        "payload": {
            "summary": summary,
            "key_events": [summary],
            "cataloging_characters": cataloging_characters or [],
            "anonymous_participants": anonymous_participants or [],
            "cataloging_worldbuilding_titles": cataloging_worldbuilding_titles or [],
            "incidental_worldbuilding_mentions": incidental_worldbuilding_mentions or [],
        },
    }


def _alice_facts(summary: str):
    return [
        _overview_fact(summary, cataloging_characters=["Alice"]),
        {
            "fact_type": "character_fact",
            "evidence": "Alice appears in the chapter.",
            "payload": {
                "primary_name": "Alice",
                "archive_identity": "stable_character",
                "stable_profile_change": True,
            },
        },
    ]


def _complete_alice_candidates(chapter_title: str, summary: str):
    return [
        _summary_candidate(
            summary,
            characters=["Alice"],
            character_profiles=["Alice"],
        ),
        {
            "type": "outline_create",
            "node_type": "chapter",
            "title": chapter_title,
            "summary": summary,
        },
        {
            "type": "character_create",
            "name": "Alice",
            "role_type": "protagonist",
            "appearance": "A traveler in road-worn clothes.",
            "age": "adult",
            "personality": "Brave and observant.",
            "background": "A traveler crossing the mystical forest.",
        },
        {
            "type": "character_state_update",
            "name": "Alice",
            "life_status": "alive",
            "current_location": "mystical forest",
            "mental_state": "alert",
        },
        {
            "type": "chapter_link",
            "character_names": ["Alice"],
            "description": "Alice appears in this chapter.",
        },
    ]


class ExternalCatalogingE2ETest(unittest.TestCase):
    """End-to-end external cataloging without Siming LLM calls."""

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        try:
            os.remove("./test_external_cataloging_e2e.db")
        except OSError:
            pass

    def setUp(self):
        self.db = TestSession()
        self.project = Project(title="Test Novel", description="E2E test project")
        self.db.add(self.project)
        self.db.flush()

        self.chapters = []
        for i in range(1, 4):
            ch = Chapter(
                project_id=self.project.id,
                title=f"Chapter {i}",
                content=f"Content of chapter {i}. Alice appears in a mystical forest.",
                word_count=100,
            )
            self.db.add(ch)
            self.chapters.append(ch)
        self.db.commit()
        for ch in self.chapters:
            self.db.refresh(ch)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_full_cataloging_workflow(self):
        """Full external cataloging: facts -> candidates -> apply -> verify."""
        from app.services.workspace.tools.external_cataloging import (
            start_external_cataloging_job,
            get_next_external_cataloging_chapter,
            save_external_cataloging_candidates,
            save_external_cataloging_facts,
            verify_external_cataloging_progress,
        )
        from app.services.workspace.tools.cataloging import apply_pending_cataloging

        project_id = self.project.id

        # Step 1: Start job
        result = _run(start_external_cataloging_job(self.db, project_id, {}))
        self.assertEqual(result["status"], "ok", f"start_job failed: {result}")
        job_id = result["data"]["job_id"]
        self.assertEqual(result["data"]["chapter_count"], 3)
        self.assertEqual(result["data"]["next_tool"], "get_prompt_pack")
        self.assertIn("workflow_reminder", result["data"])

        # Step 2: Process each chapter
        for i in range(3):
            result = _run(get_next_external_cataloging_chapter(self.db, project_id, {"job_id": job_id}))
            self.assertEqual(result["status"], "ok", f"get_next_chapter {i} failed: {result}")
            self.assertFalse(result["data"].get("all_done"))
            self.assertEqual(result["data"]["phase"], "facts")
            self.assertEqual(result["data"]["next_tool"], "save_external_cataloging_facts")
            self.assertIn("source language", result["data"]["workflow_reminder"]["language_rule"])
            self.assertIn('node_type="section"', result["data"]["outline_granularity_policy"])
            self.assertIn("outline_granularity_policy", result["data"]["workflow_reminder"])
            chapter_id = result["data"]["chapter_id"]

            result = _run(save_external_cataloging_facts(
                self.db,
                project_id,
                {
                    "job_id": job_id,
                    "chapter_id": chapter_id,
                    "facts": _alice_facts(f"Chapter {i + 1} summary"),
                },
            ))
            self.assertEqual(result["status"], "ok", f"save_facts {i} failed: {result}")

            result = _run(get_next_external_cataloging_chapter(
                self.db,
                project_id,
                {"job_id": job_id, "phase": "candidates"},
            ))
            self.assertEqual(result["status"], "ok", result)
            self.assertEqual(result["data"]["chapter_id"], chapter_id)
            self.assertEqual(result["data"]["phase"], "candidates")

            # Save candidates
            candidates = _complete_alice_candidates(
                f"Chapter {i + 1}",
                f"Chapter {i + 1} summary",
            )
            existing_alice = self.db.query(Character).filter_by(project_id=project_id, name="Alice").first()
            if existing_alice:
                for candidate in candidates:
                    if candidate.get("type") == "character_create":
                        candidate.update(type="character_update", id=existing_alice.id)
            result = _run(save_external_cataloging_candidates(
                self.db, project_id,
                {"job_id": job_id, "chapter_id": chapter_id, "candidates": candidates},
            ))
            self.assertEqual(result["status"], "ok", f"save_candidates {i} failed: {result}")
            self.assertEqual(result["data"]["candidates_saved"], 5)
            self.assertEqual(result["data"]["chapter_run_status"], "awaiting_confirmation", result)
            self.assertEqual(result["data"]["next_tool"], "apply_pending_cataloging")
            self.assertIn("workflow_reminder", result["data"])

            result = _run(apply_pending_cataloging(self.db, project_id, {"job_id": job_id}))
            self.assertEqual(result["status"], "ok", f"apply_pending {i} failed: {result}")
            self.assertEqual(result["data"]["next_tool"], "verify_external_cataloging_progress")
            self.assertEqual(result["data"]["job"]["last_completed_chapter_id"], chapter_id)
            self.assertIsNotNone(result["data"]["run"]["completed_at"])

        # Step 3: Verify progress
        result = _run(verify_external_cataloging_progress(self.db, project_id, {"job_id": job_id}))
        self.assertEqual(result["status"], "ok")
        data = result["data"]
        self.assertEqual(data["chapters_processed"], 3)
        self.assertEqual(data["chapters_total"], 3)
        self.assertEqual(data["chapters_pending"], 0)
        self.assertEqual(data["chapters_awaiting_confirmation"], 0)
        self.assertEqual(data["chapters_failed"], 0)
        self.assertEqual(data["pending_candidates"], 0)
        self.assertEqual(data["next_tool"], "get_project_archive_status")
        self.assertGreaterEqual(data["characters_count"], 1)
        self.assertGreaterEqual(data["outline_nodes_count"], 3)
        self.assertGreaterEqual(data["chapter_outline_nodes_count"], 3)
        self.assertEqual(data["section_outline_nodes_count"], 0)
        self.assertTrue(any("section-level outline nodes" in item for item in data["warnings"]))
        self.assertGreaterEqual(self.db.query(ChapterSummary).count(), 3)
        self.assertGreaterEqual(self.db.query(Character).count(), 1)
        self.assertGreaterEqual(self.db.query(OutlineNode).count(), 3)

    def test_file_read_mode_returns_path_without_chapter_or_indexes(self):
        """Managed CLI workers receive a file pointer instead of duplicate context."""
        from pathlib import Path

        from app.services.workspace.tools.external_cataloging import (
            get_next_external_cataloging_chapter,
            start_external_cataloging_job,
        )

        project_id = self.project.id
        result = _run(start_external_cataloging_job(
            self.db,
            project_id,
            {"chapter_ids": [self.chapters[0].id]},
        ))
        self.assertEqual(result["status"], "ok")

        result = _run(get_next_external_cataloging_chapter(
            self.db,
            project_id,
            {
                "job_id": result["data"]["job_id"],
                "include_content": False,
                "include_prompt_pack": False,
                "include_context_indexes": False,
            },
        ))
        self.assertEqual(result["status"], "ok")
        data = result["data"]
        self.assertIsNone(data["content"])
        self.assertFalse(data["content_included"])
        self.assertFalse(data["context_indexes_included"])
        self.assertIsNone(data["prompt_pack"])
        self.assertEqual(data["character_alias_index"], {})
        self.assertEqual(data["worldbuilding_title_index"], {})
        self.assertEqual(data["outline_neighborhood"], [])
        self.assertTrue(Path(data["content_file_path"]).is_file())
        self.assertTrue(Path(data["project_folder"]).is_dir())

    def test_candidates_phase_delivers_every_related_worldbuilding_identity(self):
        """The external agent must see every archive identity it must review."""
        from app.services.workspace.tools.external_cataloging import (
            get_next_external_cataloging_chapter,
            save_external_cataloging_facts,
            start_external_cataloging_job,
        )

        entries = [
            WorldbuildingEntry(
                project_id=self.project.id,
                dimension="history",
                title="《港务应急通信通知》（发文底档）",
                content="正式发文底档原件。",
                status="active",
            ),
            WorldbuildingEntry(
                project_id=self.project.id,
                dimension="history",
                title="馆藏发文底档著录索引",
                content="用于定位发文底档原件的馆藏索引。",
                status="active",
            ),
        ]
        self.db.add_all(entries)
        self.db.commit()

        started = _run(start_external_cataloging_job(
            self.db,
            self.project.id,
            {"chapter_ids": [self.chapters[0].id]},
        ))
        self.assertEqual(started["status"], "ok", started)
        job_id = started["data"]["job_id"]

        assigned = _run(get_next_external_cataloging_chapter(
            self.db,
            self.project.id,
            {"job_id": job_id, "phase": "facts"},
        ))
        self.assertEqual(assigned["status"], "ok", assigned)
        saved = _run(save_external_cataloging_facts(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": self.chapters[0].id,
                "facts": [
                    _overview_fact(
                        "本章核读发文底档及其馆藏索引。",
                        cataloging_worldbuilding_titles=["发文底档"],
                    ),
                    {
                        "fact_type": "worldbuilding_fact",
                        "evidence": "本章核读发文底档及其馆藏索引。",
                        "payload": {
                            "title_hint": "发文底档",
                            "archive_identity": "stable_setting",
                            "stable_setting_change": True,
                            "dimension_hint": "history",
                        },
                    },
                ],
            },
        ))
        self.assertEqual(saved["status"], "ok", saved)

        candidates = _run(get_next_external_cataloging_chapter(
            self.db,
            self.project.id,
            {"job_id": job_id, "phase": "candidates"},
        ))
        self.assertEqual(candidates["status"], "ok", candidates)
        delivered = candidates["data"]["worldbuilding_identity_review_required"]
        self.assertEqual({item["id"] for item in delivered}, {item.id for item in entries})
        self.assertEqual(
            {item["title"] for item in delivered},
            {item.title for item in entries},
        )

    def test_repeated_external_start_reuses_same_chapter_version_and_records_version(self):
        from app.services.workspace.tools.external_cataloging import start_external_cataloging_job

        chapter_id = self.chapters[0].id
        first = _run(start_external_cataloging_job(
            self.db,
            self.project.id,
            {"chapter_ids": [chapter_id]},
        ))
        second = _run(start_external_cataloging_job(
            self.db,
            self.project.id,
            {"chapter_ids": [chapter_id]},
        ))

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["data"]["job_id"], first["data"]["job_id"])
        self.assertTrue(second["data"]["idempotent_reuse"])
        runs = self.db.query(CatalogingChapterRun).filter_by(
            job_id=first["data"]["job_id"]
        ).all()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].chapter_version, self.chapters[0].current_version)

    def test_chinese_cataloging_candidates_are_persisted(self):
        """External cataloging must preserve Chinese names and archive text."""
        from app.services.workspace.tools.external_cataloging import (
            start_external_cataloging_job,
            get_next_external_cataloging_chapter,
            save_external_cataloging_facts,
            save_external_cataloging_candidates,
        )
        from app.services.workspace.tools.cataloging import apply_pending_cataloging

        project_id = self.project.id
        self.chapters[0].title = "第一章 穿越·着陆"
        self.chapters[0].content = "特昂糖在陆家府邸醒来，开始判断自身处境。"
        self.db.commit()
        result = _run(start_external_cataloging_job(
            self.db,
            project_id,
            {"chapter_ids": [self.chapters[0].id]},
        ))
        self.assertEqual(result["status"], "ok")
        job_id = result["data"]["job_id"]

        result = _run(get_next_external_cataloging_chapter(self.db, project_id, {"job_id": job_id}))
        self.assertEqual(result["status"], "ok")
        chapter_id = result["data"]["chapter_id"]

        facts = [
            _overview_fact(
                "特昂糖在陆家醒来，开始判断自身处境。",
                cataloging_characters=["特昂糖"],
                cataloging_worldbuilding_titles=["陆家府邸"],
            ),
            {
                "fact_type": "character_fact",
                "evidence": "特昂糖在陆家醒来。",
                "payload": {
                    "primary_name": "特昂糖",
                    "archive_identity": "stable_character",
                    "stable_profile_change": True,
                    "actions": ["醒来"],
                },
            },
            {
                "fact_type": "worldbuilding_fact",
                "evidence": "故事从陆家府邸开始。",
                "payload": {
                    "title_hint": "陆家府邸",
                    "archive_identity": "stable_setting",
                    "stable_setting_change": True,
                    "dimension_hint": "geography",
                },
            },
        ]
        result = _run(save_external_cataloging_facts(
            self.db,
            project_id,
            {"job_id": job_id, "chapter_id": chapter_id, "facts": facts},
        ))
        self.assertEqual(result["status"], "ok")

        candidates = [
            _summary_candidate(
                "特昂糖在陆家醒来，发现周围环境异常，开始判断自身处境。",
                characters=["特昂糖"],
                worldbuilding=["陆家府邸"],
                character_profiles=["特昂糖"],
            ),
            {
                "type": "character_create",
                "name": "特昂糖",
                "aliases": ["陆糖"],
                "role_type": "protagonist",
                "appearance": "幼童外貌",
                "age": "三岁",
                "personality": "冷静而善于观察。",
                "background": "穿越女娃，在陆家醒来并开始观察这个修仙世界。",
            },
            {
                "type": "character_state_update",
                "name": "特昂糖",
                "life_status": "alive",
                "current_location": "陆家府邸",
                "mental_state": "警惕而冷静",
            },
            {
                "type": "worldbuilding_create",
                "dimension": "geography",
                "title": "陆家府邸",
                "content": "特昂糖穿越后醒来的陆家宅院。",
            },
            {
                "type": "outline_create",
                "node_type": "chapter",
                "title": "第一章 穿越·着陆",
                "summary": "特昂糖在陆家醒来，意识到自己来到了修仙世界。",
            },
            {
                "type": "chapter_link",
                "character_names": ["特昂糖"],
                "worldbuilding_titles": ["陆家府邸"],
                "description": "本章角色与地点关联。",
            },
        ]
        result = _run(save_external_cataloging_candidates(
            self.db,
            project_id,
            {"job_id": job_id, "chapter_id": chapter_id, "candidates": candidates},
        ))
        self.assertEqual(result["status"], "ok")

        result = _run(apply_pending_cataloging(self.db, project_id, {"job_id": job_id}))
        self.assertEqual(result["status"], "ok", result)

        character = self.db.query(Character).filter(Character.project_id == project_id, Character.name == "特昂糖").first()
        self.assertIsNotNone(character)
        self.assertIn("穿越女娃", character.background)
        self.assertEqual(character.current_location, "陆家府邸")

        summary = self.db.query(ChapterSummary).filter(ChapterSummary.chapter_id == chapter_id).first()
        self.assertIsNotNone(summary)
        self.assertIn("特昂糖", summary.summary_text)

        outline = self.db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.title == "第一章 穿越·着陆",
        ).first()
        self.assertIsNotNone(outline)

    def test_job_id_recovers_project_scope_when_project_id_is_empty(self):
        """External agents can continue a cataloging job by job_id without losing project binding."""
        from app.services.workspace.tools.external_cataloging import (
            start_external_cataloging_job,
            get_next_external_cataloging_chapter,
            save_external_cataloging_facts,
            save_external_cataloging_candidates,
            verify_external_cataloging_progress,
        )
        from app.services.workspace.tools.cataloging import apply_pending_cataloging

        project_id = self.project.id
        result = _run(start_external_cataloging_job(
            self.db,
            project_id,
            {"chapter_ids": [self.chapters[0].id]},
        ))
        self.assertEqual(result["status"], "ok")
        job_id = result["data"]["job_id"]

        result = _run(get_next_external_cataloging_chapter(self.db, "", {"job_id": job_id}))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["project_id"], project_id)
        chapter_id = result["data"]["chapter_id"]

        result = _run(save_external_cataloging_facts(
            self.db,
            "",
            {"job_id": job_id, "chapter_id": chapter_id, "facts": _alice_facts("Alice appears in the forest.")},
        ))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["project_id"], project_id)

        result = _run(save_external_cataloging_candidates(
            self.db,
            "",
            {
                "job_id": job_id,
                "chapter_id": chapter_id,
                "candidates": _complete_alice_candidates(
                    "Chapter 1",
                    "Alice appears in the forest.",
                ),
            },
        ))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["project_id"], project_id)

        result = _run(apply_pending_cataloging(self.db, "", {"job_id": job_id}))
        self.assertEqual(result["status"], "ok", result)

        result = _run(verify_external_cataloging_progress(self.db, "", {"job_id": job_id}))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["project_id"], project_id)
        self.assertGreaterEqual(result["data"]["characters_count"], 1)
        self.assertGreaterEqual(result["data"]["outline_nodes_count"], 1)

    def test_no_chapters_returns_skipped(self):
        """Starting cataloging on empty project should skip."""
        from app.services.workspace.tools.external_cataloging import start_external_cataloging_job

        empty = Project(title="Empty")
        self.db.add(empty)
        self.db.commit()
        self.db.refresh(empty)

        result = _run(start_external_cataloging_job(self.db, empty.id, {}))
        self.assertEqual(result["status"], "skipped")

    def test_llm_gateway_not_called(self):
        """Verify external cataloging never calls LLMGateway."""
        from unittest.mock import patch, AsyncMock

        from app.services.workspace.tools.external_cataloging import (
            start_external_cataloging_job,
            get_next_external_cataloging_chapter,
            save_external_cataloging_facts,
            save_external_cataloging_candidates,
        )
        from app.services.workspace.tools.cataloging import apply_pending_cataloging

        project_id = self.project.id

        with patch("app.ai.gateway.LLMGateway.chat_completion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = AssertionError("LLM should not be called")

            result = _run(start_external_cataloging_job(self.db, project_id, {}))
            self.assertEqual(result["status"], "ok")
            job_id = result["data"]["job_id"]

            result = _run(get_next_external_cataloging_chapter(self.db, project_id, {"job_id": job_id}))
            self.assertEqual(result["status"], "ok")
            chapter_id = result["data"]["chapter_id"]

            result = _run(save_external_cataloging_facts(
                self.db, project_id,
                {"job_id": job_id, "chapter_id": chapter_id, "facts": _alice_facts("Alice appears in the forest.")},
            ))
            self.assertEqual(result["status"], "ok")

            result = _run(save_external_cataloging_candidates(
                self.db, project_id,
                {"job_id": job_id, "chapter_id": chapter_id,
                 "candidates": _complete_alice_candidates(
                     "Ch1",
                     "Alice appears in the forest.",
                 )},
            ))
            self.assertEqual(result["status"], "ok")

            result = _run(apply_pending_cataloging(self.db, project_id, {"job_id": job_id}))
            self.assertEqual(result["status"], "ok")

            mock_llm.assert_not_called()

    def test_each_chapter_must_finish_before_next_chapter_facts(self):
        """Facts, candidates, and application are serialized by chapter."""
        from app.services.workspace.tools.external_cataloging import (
            start_external_cataloging_job,
            get_next_external_cataloging_chapter,
            save_external_cataloging_facts,
            save_external_cataloging_candidates,
        )
        from app.services.workspace.tools.cataloging import apply_pending_cataloging

        project_id = self.project.id
        result = _run(start_external_cataloging_job(self.db, project_id, {}))
        self.assertEqual(result["status"], "ok", result)
        job_id = result["data"]["job_id"]

        first = _run(get_next_external_cataloging_chapter(
            self.db,
            project_id,
            {"job_id": job_id, "phase": "facts"},
        ))
        self.assertEqual(first["status"], "ok", first)
        first_chapter_id = first["data"]["chapter_id"]
        result = _run(save_external_cataloging_facts(
            self.db,
            project_id,
            {
                "job_id": job_id,
                "chapter_id": first_chapter_id,
                "facts": _alice_facts("first"),
            },
        ))
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"]["candidate_generation_allowed"])

        blocked = _run(get_next_external_cataloging_chapter(
            self.db,
            project_id,
            {"job_id": job_id, "phase": "facts"},
        ))
        self.assertEqual(blocked["status"], "ok", blocked)
        self.assertEqual(blocked["data"]["next_arguments"]["phase"], "candidates")
        self.assertEqual(
            blocked["data"]["next_candidate_run"]["chapter_id"],
            first_chapter_id,
        )

        second_chapter_id = (
            self.db.query(CatalogingChapterRun)
            .filter(
                CatalogingChapterRun.job_id == job_id,
                CatalogingChapterRun.chapter_id != first_chapter_id,
            )
            .order_by(CatalogingChapterRun.chapter_order)
            .first()
            .chapter_id
        )
        rejected = _run(save_external_cataloging_facts(
            self.db,
            project_id,
            {
                "job_id": job_id,
                "chapter_id": second_chapter_id,
                "facts": [_overview_fact("second")],
            },
        ))
        self.assertEqual(rejected["status"], "skipped", rejected)
        self.assertEqual(rejected["data"]["blocking_run"]["chapter_id"], first_chapter_id)

        result = _run(get_next_external_cataloging_chapter(
            self.db,
            project_id,
            {"job_id": job_id, "phase": "candidates"},
        ))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["chapter_id"], first_chapter_id)

        result = _run(save_external_cataloging_candidates(
            self.db,
            project_id,
            {
                "job_id": job_id,
                "chapter_id": first_chapter_id,
                "candidates": _complete_alice_candidates("Chapter 1", "first"),
            },
        ))
        self.assertEqual(result["status"], "ok", result)
        result = _run(apply_pending_cataloging(self.db, project_id, {"job_id": job_id}))
        self.assertEqual(result["status"], "ok", result)

        next_chapter = _run(get_next_external_cataloging_chapter(
            self.db,
            project_id,
            {"job_id": job_id, "phase": "facts"},
        ))
        self.assertEqual(next_chapter["status"], "ok", next_chapter)
        self.assertEqual(next_chapter["data"]["chapter_id"], second_chapter_id)

    def test_cataloging_read_pages_deliver_every_whole_record_with_stable_ties(self):
        from datetime import datetime
        from app.services.workspace.registry import registry
        from app.services.workspace.tool_result_projection import (
            ToolResultOverCapacity, model_tool_result_projector,
        )
        from app.services.workspace.tools.cataloging import (
            list_cataloging_candidates, list_cataloging_facts,
        )
        from app.services.workspace.tools.external_cataloging import start_external_cataloging_job

        project_id = self.project.id
        started = _run(start_external_cataloging_job(self.db, project_id, {}))
        job_id = started["data"]["job_id"]
        run = self.db.query(CatalogingChapterRun).filter_by(job_id=job_id).first()
        common = dict(job_id=job_id, project_id=project_id,
                      chapter_run_id=run.id, chapter_id=run.chapter_id,
                      created_at=datetime(2026, 1, 1))
        for index in range(18):
            payload = json.dumps({"summary": f"完整事实{index}：" + "证据不能截断。" * 180}, ensure_ascii=False)
            self.db.add(CatalogingFact(
                **common, id=f"page-fact-{index:02d}", sort_order=0,
                fact_type="chapter_overview", raw_payload=payload,
                evidence=f"事实证据{index}" + "来源" * 120,
            ))
            self.db.add(CatalogingCandidate(
                **common, id=f"page-candidate-{index:02d}", item_type="chapter_summary",
                status="pending", raw_payload=payload,
            ))
        self.db.commit()

        for handler, name, filter_arg, expected_ids in (
            (list_cataloging_facts, "list_cataloging_facts", {"fact_type": "chapter_overview"},
             [f"page-fact-{i:02d}" for i in range(18)]),
            (list_cataloging_candidates, "list_cataloging_candidates", {"status": "pending", "item_type": "chapter_summary"},
             [f"page-candidate-{i:02d}" for i in range(18)]),
        ):
            with self.subTest(tool=name):
                args = {"job_id": job_id, "chapter_run_id": run.id, **filter_arg}
                delivered = []
                offsets = []
                while True:
                    result = _run(handler(self.db, project_id, args))
                    projected = model_tool_result_projector.project(registry.get(name), result)
                    self.assertEqual(projected.payload, result)
                    page = projected.payload["data"]
                    self.assertEqual(page["total"], 18)
                    self.assertLessEqual(len(page["items"]), 2)
                    self.assertEqual(page["offset"], len(delivered))
                    offsets.append(page["offset"])
                    delivered.extend(page["items"])
                    if not page["has_more"]:
                        self.assertIsNone(page["next_arguments"])
                        self.assertIsNone(page["next_offset"])
                        break
                    args = page["next_arguments"]
                    self.assertEqual(args["chapter_run_id"], run.id)
                    for field, value in filter_arg.items():
                        self.assertEqual(args[field], value)
                self.assertGreaterEqual(len(offsets), 9)
                self.assertEqual([item["id"] for item in delivered], expected_ids)
                for index, item in enumerate(delivered):
                    self.assertEqual(item["payload"]["summary"], f"完整事实{index}：" + "证据不能截断。" * 180)
                with self.assertRaises(ToolResultOverCapacity):
                    model_tool_result_projector.project(
                        registry.get(name), {**result, "data": {"items": delivered, "total": 18}},
                    )
                empty = _run(handler(self.db, project_id, {**args, "offset": 30}))
                self.assertEqual(empty["data"]["items"], [])
                self.assertFalse(empty["data"]["has_more"])
                self.assertEqual(empty["data"]["total"], 18)
                denied = _run(handler(self.db, "another-project", args))
                self.assertEqual(denied["status"], "skipped")

    def test_managed_cli_turn_cannot_advance_to_another_chapter(self):
        from app.services.workspace.tools.external_cataloging import (
            get_next_external_cataloging_chapter,
            save_external_cataloging_candidates,
            save_external_cataloging_facts,
            start_external_cataloging_job,
        )
        from app.services.workspace.tools.cataloging import apply_pending_cataloging, list_cataloging_facts

        project_id = self.project.id
        started = _run(start_external_cataloging_job(self.db, project_id, {}))
        job_id = started["data"]["job_id"]
        runs = (
            self.db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.job_id == job_id)
            .order_by(CatalogingChapterRun.chapter_order)
            .all()
        )
        first_run, second_run = runs[:2]
        env = {
            "MOSHU_MANAGED_AGENT_KIND": "cataloging",
            "MOSHU_MANAGED_CATALOGING_PROJECT_ID": project_id,
            "MOSHU_MANAGED_CATALOGING_JOB_ID": job_id,
            "MOSHU_MANAGED_CATALOGING_CHAPTER_ID": first_run.chapter_id,
            "MOSHU_MANAGED_CATALOGING_CHAPTER_RUN_ID": first_run.id,
            "MOSHU_MANAGED_CATALOGING_STAGE": "facts",
        }
        with patch.dict(os.environ, env, clear=False):
            assigned = _run(get_next_external_cataloging_chapter(
                self.db,
                project_id,
                {"job_id": job_id, "phase": "facts"},
            ))
            self.assertEqual(assigned["data"]["chapter_id"], first_run.chapter_id)

            saved = _run(save_external_cataloging_facts(
                self.db,
                project_id,
                {
                    "job_id": job_id,
                    "chapter_id": first_run.chapter_id,
                    "facts": _alice_facts("first"),
                },
            ))
            self.assertEqual(saved["status"], "ok")

            other_fact = CatalogingFact(
                job_id=job_id,
                chapter_run_id=second_run.id,
                project_id=project_id,
                chapter_id=second_run.chapter_id,
                fact_type="chapter_overview",
                raw_payload=json.dumps({"summary": "second"}),
            )
            self.db.add(other_fact)
            self.db.commit()
            scoped_facts = _run(list_cataloging_facts(
                self.db,
                project_id,
                {"job_id": job_id},
            ))
            self.assertEqual(scoped_facts["data"]["total"], 2)
            self.assertTrue(all(
                item["chapter_run_id"] == first_run.id
                for item in scoped_facts["data"]["items"]
            ))

            saved = _run(save_external_cataloging_candidates(
                self.db,
                project_id,
                {
                    "job_id": job_id,
                    "chapter_id": first_run.chapter_id,
                    "candidates": _complete_alice_candidates(
                        "Chapter 1",
                        (
                            "Alice enters the mystical forest, studies the unfamiliar path, "
                            "and records what she must verify before traveling onward."
                        ),
                    ),
                },
            ))
            self.assertEqual(saved["status"], "ok")
            applied = _run(apply_pending_cataloging(self.db, project_id, {"job_id": job_id}))
            self.assertEqual(applied["status"], "ok")

            blocked = _run(get_next_external_cataloging_chapter(
                self.db,
                project_id,
                {"job_id": job_id, "phase": "facts"},
            ))
            self.assertEqual(blocked["status"], "skipped")
            self.assertTrue(blocked["data"]["managed_turn_complete"])

            wrong_write = _run(save_external_cataloging_facts(
                self.db,
                project_id,
                {
                    "job_id": job_id,
                    "chapter_id": second_run.chapter_id,
                    "facts": [_overview_fact("second")],
                },
            ))
            self.assertEqual(wrong_write["status"], "skipped")

        self.db.refresh(second_run)
        self.assertEqual(second_run.status, "pending")

    def test_fact_type_payload_shape_is_preserved(self):
        from app.services.workspace.tools.external_cataloging import (
            save_external_cataloging_facts,
            start_external_cataloging_job,
        )

        started = _run(start_external_cataloging_job(self.db, self.project.id, {}))
        job_id = started["data"]["job_id"]
        run = (
            self.db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.job_id == job_id)
            .order_by(CatalogingChapterRun.chapter_order)
            .first()
        )

        saved = _run(save_external_cataloging_facts(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "facts": [
                    _overview_fact(
                        "Alice enters the forest.",
                        cataloging_characters=["Alice"],
                    ),
                    {
                        "fact_type": "character_fact",
                        "payload": {
                            "character_name": "Alice",
                            "archive_identity": "stable_character",
                            "stable_profile_change": False,
                            "description": "red coat",
                        },
                        "confidence": 0.9,
                        "evidence": "Alice wore a red coat.",
                    },
                ],
            },
        ))

        self.assertEqual(saved["status"], "ok")
        fact = self.db.query(CatalogingFact).filter(
            CatalogingFact.chapter_run_id == run.id,
            CatalogingFact.fact_type == "character_fact",
        ).one()
        self.assertEqual(fact.fact_type, "character_fact")
        self.assertEqual(json.loads(fact.raw_payload)["character_name"], "Alice")
        self.assertEqual(fact.confidence, 0.9)
        self.assertEqual(fact.evidence, "Alice wore a red coat.")

    def test_empty_or_incomplete_candidates_cannot_complete_chapter(self):
        from app.services.workspace.tools.external_cataloging import (
            save_external_cataloging_candidates,
            save_external_cataloging_facts,
            start_external_cataloging_job,
        )
        from app.services.workspace.tools.cataloging import apply_pending_cataloging

        started = _run(start_external_cataloging_job(self.db, self.project.id, {}))
        job_id = started["data"]["job_id"]
        run = (
            self.db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.job_id == job_id)
            .order_by(CatalogingChapterRun.chapter_order)
            .first()
        )
        _run(save_external_cataloging_facts(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "facts": [_overview_fact("first")],
            },
        ))

        for malformed in ('[{"type":"chapter_summary"}]', [{"type": "chapter_summary"}, "invalid"]):
            rejected = _run(save_external_cataloging_candidates(
                self.db, self.project.id,
                {"job_id": job_id, "chapter_id": run.chapter_id, "candidates": malformed},
            ))
            self.assertEqual(rejected["status"], "skipped")
            self.assertTrue(rejected["data"]["validation_errors"])
            self.assertIn(rejected["data"]["validation_errors"][0], rejected["detail"])
            self.assertEqual(self.db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id).count(), 0)
            self.assertEqual(run.status, "facts_saved")

        empty = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {"job_id": job_id, "chapter_id": run.chapter_id, "candidates": []},
        ))
        self.assertEqual(empty["status"], "skipped")
        self.assertIn("No candidates were stored", empty["detail"])
        self.assertEqual(
            empty["data"]["no_effect_reason"],
            "empty_or_unusable_candidate_batch",
        )
        self.assertFalse(empty["data"]["candidate_set_complete"])
        self.assertEqual(
            set(empty["data"]["missing_required_items"]),
            {
                "chapter_summary",
                "chapter-level outline",
                "narrative-governance assessment",
                "chapter_summary.scene_count coverage declaration",
                "chapter_summary.characters coverage declaration",
                "chapter_summary.worldbuilding coverage declaration",
                "chapter_summary.relationships coverage declaration",
                "chapter_summary.character_profiles coverage declaration",
            },
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "facts_saved")

        wrapped = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "candidates": [{"candidates": [_summary_candidate("wrapped")]}],
            },
        ))
        self.assertEqual(wrapped["status"], "skipped")
        self.assertEqual(wrapped["data"]["candidates_saved"], 0)
        self.assertTrue(wrapped["data"]["warnings"])
        self.assertIn("No candidates were stored", wrapped["detail"])

        partial = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "candidates": [{"type": "chapter_summary", "summary_text": "first"}],
            },
        ))
        self.assertFalse(partial["data"]["candidate_set_complete"])
        self.assertEqual(
            set(partial["data"]["missing_required_items"]),
            {
                "chapter-level outline",
                "chapter_summary.scene_count coverage declaration",
                "chapter_summary.characters coverage declaration",
                "chapter_summary.worldbuilding coverage declaration",
                "chapter_summary.relationships coverage declaration",
                "chapter_summary.character_profiles coverage declaration",
            },
        )
        self.assertEqual(
            _run(apply_pending_cataloging(self.db, self.project.id, {"job_id": job_id}))["status"],
            "skipped",
        )

        chapter_title = next(
            chapter.title for chapter in self.chapters if chapter.id == run.chapter_id
        )
        self.assertEqual(
            self.db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type == "outline_create",
            ).count(),
            0,
        )

        complete = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "candidates": [
                    _summary_candidate("first"),
                    {
                        "type": "outline_create",
                        "title": chapter_title,
                        "node_type": "chapter",
                        "summary": "first",
                    },
                ],
            },
        ))
        self.assertTrue(complete["data"]["candidate_set_complete"], complete)
        self.assertEqual(complete["data"]["chapter_run_status"], "awaiting_confirmation")
        self.assertEqual(
            self.db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type == "outline_create",
            ).count(),
            1,
        )
        applied = _run(apply_pending_cataloging(self.db, self.project.id, {"job_id": job_id}))
        self.assertEqual(applied["status"], "ok")
        review = self.db.query(ChapterGovernanceReview).filter(
            ChapterGovernanceReview.project_id == self.project.id,
            ChapterGovernanceReview.chapter_id == run.chapter_id,
        ).one()
        self.assertEqual(review.status, "assessed")
        self.assertEqual(review.source, "provided")
        self.assertEqual(review.chapter_version, run.chapter.current_version or 1)

    def test_managed_candidates_cannot_complete_with_placeholder_summary_or_false_scene_manifest(self):
        from app.services.workspace.tools.external_cataloging import (
            save_external_cataloging_candidates,
            save_external_cataloging_facts,
            start_external_cataloging_job,
        )

        started = _run(start_external_cataloging_job(
            self.db,
            self.project.id,
            {"chapter_ids": [self.chapters[0].id]},
        ))
        job_id = started["data"]["job_id"]
        run = self.db.query(CatalogingChapterRun).filter_by(job_id=job_id).one()
        saved_facts = _run(save_external_cataloging_facts(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "facts": [{
                    "fact_type": "chapter_overview",
                    "evidence": "The chapter has four independently extracted scenes.",
                    "payload": {
                        "summary": "A complete facts-stage overview.",
                        "cataloging_characters": [],
                        "anonymous_participants": [],
                        "cataloging_worldbuilding_titles": [],
                        "incidental_worldbuilding_mentions": [],
                        "scenes": [
                            {"title": "scene one"},
                            {"title": "scene two"},
                            {"title": "scene three"},
                            {"title": "scene four"},
                        ],
                    },
                }],
            },
        ))
        self.assertEqual(saved_facts["status"], "ok", saved_facts)

        env = {
            "MOSHU_MANAGED_AGENT_KIND": "cataloging",
            "MOSHU_MANAGED_CATALOGING_PROJECT_ID": self.project.id,
            "MOSHU_MANAGED_CATALOGING_JOB_ID": job_id,
            "MOSHU_MANAGED_CATALOGING_CHAPTER_ID": run.chapter_id,
            "MOSHU_MANAGED_CATALOGING_CHAPTER_RUN_ID": run.id,
            "MOSHU_MANAGED_CATALOGING_STAGE": "candidates",
        }
        with patch.dict(os.environ, env, clear=False):
            saved = _run(save_external_cataloging_candidates(
                self.db,
                self.project.id,
                {
                    "job_id": job_id,
                    "chapter_id": run.chapter_id,
                    "candidates": [
                        _summary_candidate("test"),
                        {
                            "type": "outline_create",
                            "title": self.chapters[0].title,
                            "node_type": "chapter",
                            "summary": "test",
                        },
                    ],
                },
            ))

        self.assertEqual(saved["status"], "ok", saved)
        self.assertFalse(saved["data"]["candidate_set_complete"], saved)
        self.assertEqual(saved["data"]["chapter_run_status"], "facts_saved")
        self.assertIn(
            "chapter summary has fewer than 40 non-whitespace characters",
            saved["data"]["missing_required_items"],
        )
        self.assertIn(
            "chapter_overview scenes disagree with coverage_manifest.scene_count: facts=4, manifest=1",
            saved["data"]["missing_required_items"],
        )
        self.assertFalse(saved["data"]["auto_applied"])
        self.assertEqual(
            self.db.query(ChapterSummary).filter_by(chapter_id=run.chapter_id).count(),
            0,
        )

    def test_complete_summary_without_outline_remains_incomplete(self):
        from app.services.workspace.tools.external_cataloging import (
            save_external_cataloging_candidates,
            save_external_cataloging_facts,
            start_external_cataloging_job,
        )

        started = _run(start_external_cataloging_job(
            self.db,
            self.project.id,
            {"chapter_ids": [self.chapters[0].id]},
        ))
        job_id = started["data"]["job_id"]
        run = (
            self.db.query(CatalogingChapterRun)
            .filter(CatalogingChapterRun.job_id == job_id)
            .one()
        )
        facts = _run(save_external_cataloging_facts(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "facts": [_overview_fact("Only the complete summary arrived.")],
            },
        ))
        self.assertEqual(facts["status"], "ok", facts)

        saved = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "candidates": [_summary_candidate("Only the complete summary arrived.")],
            },
        ))

        self.assertFalse(saved["data"]["candidate_set_complete"], saved)
        self.assertEqual(saved["data"]["candidates_saved"], 1)
        self.assertEqual(saved["data"]["chapter_run_status"], "facts_saved")
        self.assertIn("chapter-level outline", saved["data"]["missing_required_items"])
        outlines = (
            self.db.query(CatalogingCandidate)
            .filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.item_type == "outline_create",
            )
            .all()
        )
        self.assertEqual(outlines, [])

if __name__ == "__main__":
    unittest.main()
