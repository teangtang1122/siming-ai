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
    CatalogingJob,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingCandidate,
    Character,
    OutlineNode,
    ChapterSummary,
)

engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
TestSession = sessionmaker(bind=engine)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


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
        """Full external cataloging: start -> get chapter -> save candidates -> verify."""
        from app.services.workspace.tools.external_cataloging import (
            start_external_cataloging_job,
            get_next_external_cataloging_chapter,
            save_external_cataloging_candidates,
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
            self.assertEqual(result["data"]["phase"], "merged")
            self.assertEqual(result["data"]["next_tool"], "save_external_cataloging_candidates")
            self.assertIn("source language", result["data"]["workflow_reminder"]["language_rule"])
            self.assertIn('node_type="section"', result["data"]["outline_granularity_policy"])
            self.assertIn("outline_granularity_policy", result["data"]["workflow_reminder"])
            chapter_id = result["data"]["chapter_id"]

            # Save candidates
            candidates = [
                {"type": "character", "action": "create", "name": "Alice",
                 "personality": "brave", "background": "traveler"},
                {"type": "outline", "action": "create",
                 "title": f"Chapter {i + 1}", "summary": f"Summary {i + 1}"},
                {"type": "summary", "action": "create",
                 "summary": f"Chapter {i + 1} summary"},
            ]
            result = _run(save_external_cataloging_candidates(
                self.db, project_id,
                {"job_id": job_id, "chapter_id": chapter_id, "phase": "merged", "candidates": candidates},
            ))
            self.assertEqual(result["status"], "ok", f"save_candidates {i} failed: {result}")
            self.assertEqual(result["data"]["candidates_saved"], 3)
            self.assertEqual(result["data"]["chapter_run_status"], "awaiting_confirmation")
            self.assertEqual(result["data"]["next_tool"], "apply_pending_cataloging")
            self.assertIn("workflow_reminder", result["data"])

            result = _run(apply_pending_cataloging(self.db, project_id, {"job_id": job_id}))
            self.assertEqual(result["status"], "ok", f"apply_pending {i} failed: {result}")
            self.assertEqual(result["data"]["next_tool"], "verify_external_cataloging_progress")

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
            {"type": "character_appearance", "data": {"name": "特昂糖", "evidence": "特昂糖在陆家醒来。"}},
            {"type": "setting", "data": {"title": "陆家府邸", "evidence": "故事从陆家府邸开始。"}},
        ]
        result = _run(save_external_cataloging_facts(
            self.db,
            project_id,
            {"job_id": job_id, "chapter_id": chapter_id, "facts": facts},
        ))
        self.assertEqual(result["status"], "ok")

        candidates = [
            {
                "type": "character",
                "action": "create",
                "name": "特昂糖",
                "aliases": ["陆糖"],
                "role_type": "主角",
                "background": "穿越女娃，在陆家醒来并开始观察这个修仙世界。",
                "current_location": "陆家府邸",
            },
            {
                "type": "outline",
                "action": "create",
                "title": "第一章 穿越·着陆",
                "summary": "特昂糖在陆家醒来，意识到自己来到了修仙世界。",
            },
            {
                "type": "summary",
                "action": "create",
                "summary": "特昂糖在陆家醒来，发现周围环境异常，开始判断自身处境。",
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
            {"job_id": job_id, "chapter_id": chapter_id, "facts": [{"type": "character", "data": {"name": "Alice"}}]},
        ))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["project_id"], project_id)

        result = _run(save_external_cataloging_candidates(
            self.db,
            "",
            {
                "job_id": job_id,
                "chapter_id": chapter_id,
                "candidates": [
                    {"type": "character", "action": "create", "name": "Alice", "background": "A traveler."},
                    {"type": "outline", "action": "create", "title": "Chapter 1", "summary": "Alice appears."},
                    {"type": "summary", "action": "create", "summary": "Alice appears in the forest."},
                ],
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
                {"job_id": job_id, "chapter_id": chapter_id, "facts": [{"type": "test", "data": {}}]},
            ))
            self.assertEqual(result["status"], "ok")

            result = _run(save_external_cataloging_candidates(
                self.db, project_id,
                {"job_id": job_id, "chapter_id": chapter_id,
                 "candidates": [
                     {"type": "chapter_summary", "summary": "Chapter summary"},
                     {"type": "outline_create", "action": "create", "title": "Ch1", "node_type": "chapter", "summary": "Alice appears in the forest."},
                 ]},
            ))
            self.assertEqual(result["status"], "ok")

            result = _run(apply_pending_cataloging(self.db, project_id, {"job_id": job_id}))
            self.assertEqual(result["status"], "ok")

            mock_llm.assert_not_called()

    def test_candidates_are_serial_even_when_facts_are_parallel(self):
        """Facts may be saved out ahead, but candidates must follow chapter_order."""
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
            {"job_id": job_id, "chapter_id": first_chapter_id, "facts": [{"type": "event", "data": {"summary": "first"}}]},
        ))
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["data"]["candidate_generation_allowed"])

        second = _run(get_next_external_cataloging_chapter(
            self.db,
            project_id,
            {"job_id": job_id, "phase": "facts"},
        ))
        self.assertEqual(second["status"], "ok", second)
        second_chapter_id = second["data"]["chapter_id"]
        result = _run(save_external_cataloging_facts(
            self.db,
            project_id,
            {"job_id": job_id, "chapter_id": second_chapter_id, "facts": [{"type": "event", "data": {"summary": "second"}}]},
        ))
        self.assertEqual(result["status"], "ok", result)
        self.assertFalse(result["data"]["candidate_generation_allowed"])
        self.assertEqual(result["data"]["blocking_run"]["chapter_id"], first_chapter_id)

        result = _run(save_external_cataloging_candidates(
            self.db,
            project_id,
            {
                "job_id": job_id,
                "chapter_id": second_chapter_id,
                "candidates": [{"type": "outline", "action": "create", "title": "Chapter 2", "summary": "second"}],
            },
        ))
        self.assertEqual(result["status"], "skipped", result)
        self.assertFalse(result["data"]["candidate_generation_allowed"])
        self.assertEqual(result["data"]["blocking_run"]["chapter_id"], first_chapter_id)
        self.assertEqual(result["data"]["next_arguments"]["phase"], "candidates")

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
                "candidates": [
                    {"type": "chapter_summary", "summary": "first"},
                    {"type": "outline", "action": "create", "title": "Chapter 1", "summary": "first", "node_type": "chapter"},
                ],
            },
        ))
        self.assertEqual(result["status"], "ok", result)
        result = _run(apply_pending_cataloging(self.db, project_id, {"job_id": job_id}))
        self.assertEqual(result["status"], "ok", result)

        result = _run(get_next_external_cataloging_chapter(
            self.db,
            project_id,
            {"job_id": job_id, "phase": "candidates"},
        ))
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["data"]["chapter_id"], second_chapter_id)

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
            "MOSHU_MANAGED_CATALOGING_STAGE": "full",
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
                    "facts": [{"type": "event", "data": {"summary": "first"}}],
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
            self.assertEqual(scoped_facts["data"]["total"], 1)
            self.assertEqual(
                scoped_facts["data"]["items"][0]["chapter_run_id"],
                first_run.id,
            )

            saved = _run(save_external_cataloging_candidates(
                self.db,
                project_id,
                {
                    "job_id": job_id,
                    "chapter_id": first_run.chapter_id,
                    "candidates": [
                        {"type": "chapter_summary", "action": "create", "summary": "first"},
                        {"type": "outline_create", "action": "create", "title": "Chapter 1", "node_type": "chapter", "summary": "first chapter events"},
                    ],
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
                    "facts": [{"type": "event", "data": {"summary": "second"}}],
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
                "facts": [{
                    "fact_type": "character_appearance",
                    "payload": {"character_name": "Alice", "description": "red coat"},
                    "confidence": 0.9,
                    "evidence": "Alice wore a red coat.",
                }],
            },
        ))

        self.assertEqual(saved["status"], "ok")
        fact = self.db.query(CatalogingFact).filter(CatalogingFact.chapter_run_id == run.id).one()
        self.assertEqual(fact.fact_type, "character_fact")
        self.assertEqual(json.loads(fact.raw_payload)["character_name"], "Alice")
        self.assertEqual(fact.confidence, 0.9)
        self.assertEqual(fact.evidence, "Alice wore a red coat.")

    def test_fact_type_is_inferred_for_provider_specific_shapes(self):
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
                    {"name": "Alice", "updates": {"traits": ["careful"]}},
                    {"title": "Forest law", "description": "Do not whistle after dark."},
                    {"title": "Chapter 1", "summary": "Alice enters the forest."},
                    {"title": "First warning", "parent_title": "Chapter 1", "summary": "A sign appears."},
                    {"source_name": "Alice", "target_name": "Bob", "relationship_type": "friend"},
                ],
            },
        ))
        self.assertEqual(saved["status"], "ok")
        types = [
            item.fact_type
            for item in (
                self.db.query(CatalogingFact)
                .filter(CatalogingFact.chapter_run_id == run.id)
                .order_by(CatalogingFact.sort_order)
                .all()
            )
        ]
        self.assertEqual(
            types,
            [
                "character_fact",
                "worldbuilding_fact",
                "chapter_overview",
                "outline_fact",
                "relationship_fact",
            ],
        )

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
                "facts": [{"fact_type": "chapter_overview", "payload": {"summary": "first"}}],
            },
        ))

        empty = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {"job_id": job_id, "chapter_id": run.chapter_id, "candidates": []},
        ))
        self.assertFalse(empty["data"]["candidate_set_complete"])
        self.assertEqual(
            set(empty["data"]["missing_required_items"]),
            {"chapter_summary", "chapter-level outline"},
        )
        self.db.refresh(run)
        self.assertEqual(run.status, "facts_saved")

        partial = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "candidates": [{"candidate_type": "chapter_summary", "payload": {"summary": "first"}}],
            },
        ))
        self.assertFalse(partial["data"]["candidate_set_complete"])
        self.assertEqual(partial["data"]["missing_required_items"], ["chapter-level outline"])
        self.assertEqual(
            _run(apply_pending_cataloging(self.db, self.project.id, {"job_id": job_id}))["status"],
            "skipped",
        )

        complete = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "candidates": [{
                    "candidate_type": "outline_create",
                    "payload": {"title": "Chapter 1", "node_type": "chapter", "summary": "first"},
                }],
            },
        ))
        self.assertTrue(complete["data"]["candidate_set_complete"])
        self.assertEqual(complete["data"]["chapter_run_status"], "awaiting_confirmation")
        applied = _run(apply_pending_cataloging(self.db, self.project.id, {"job_id": job_id}))
        self.assertEqual(applied["status"], "ok")

    def test_provider_fields_and_changes_candidate_shapes_are_applied(self):
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
                "facts": [{"name": "Alice", "description": "A careful traveler."}],
            },
        ))
        staged = _run(save_external_cataloging_candidates(
            self.db,
            self.project.id,
            {
                "job_id": job_id,
                "chapter_id": run.chapter_id,
                "candidates": [
                    {"candidate_type": "chapter_summary", "fields": {"summary": "Alice finds a wolf."}},
                    {
                        "candidate_type": "outline_create",
                        "target_name": "Chapter 1",
                        "fields": {"node_type": "chapter", "summary": "Alice enters the forest."},
                    },
                    {
                        "candidate_type": "character_create",
                        "target_name": "Alice",
                        "fields": {
                            "name": "Alice",
                            "appearance": "Red coat",
                            "background": "A careful traveler.",
                        },
                    },
                    {
                        "candidate_type": "character_update",
                        "target_name": "Alice",
                        "changes": ["mental_state:alert", "current_location:forest"],
                    },
                    {
                        "candidate_type": "worldbuilding_create",
                        "target_name": "Iron Wolf",
                        "fields": {"category": "creature", "description": "A fast low-level beast."},
                    },
                ],
            },
        ))
        self.assertTrue(staged["data"]["candidate_set_complete"])
        applied = _run(apply_pending_cataloging(self.db, self.project.id, {"job_id": job_id}))
        self.assertEqual(applied["status"], "ok")
        failures = [
            event for event in applied["data"]["events"]
            if event["type"] == "candidate_apply_failed"
        ]
        self.assertEqual(failures, [])
        alice = self.db.query(Character).filter(Character.project_id == self.project.id, Character.name == "Alice").one()
        self.assertEqual(alice.current_location, "forest")


if __name__ == "__main__":
    unittest.main()
