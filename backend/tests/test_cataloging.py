"""Regression tests for the project cataloging service layer."""

import asyncio
import json
import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ValidationError
from app.database.models import (
    AgentRun,
    Base,
    CatalogingApplyLog,
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingFact,
    CatalogingJob,
    Chapter,
    ChapterGovernanceReview,
    ChapterSummary,
    Character,
    CharacterAIConfig,
    CharacterAlias,
    CharacterRelationship,
    CharacterTimeline,
    CharacterVersion,
    ContentSyncJob,
    Foreshadowing,
    NarrativeDebt,
    OutlineNode,
    OperationRun,
    Project,
    WorldbuildingEntry,
    WorldbuildingTimeline,
)
from app.services.cataloging.applier import apply_candidates_for_run
from app.services.cataloging.candidate_io import candidate_has_usable_summary, candidate_payload
from app.services.cataloging.candidate_store import (
    recover_candidates_from_raw_output,
    try_create_candidate,
    try_create_candidates,
)
from app.services.cataloging.candidate_validation import inspect_candidate_coverage
from app.services.cataloging.context import build_light_context
from app.services.cataloging.constants import CATALOGING_STAGE_MAX_ATTEMPTS
from app.services.context_builders import _build_world_context
from app.services.cataloging.job_control import (
    cancel_job,
    first_blocking_run,
    mark_run_skipped,
    pause_job,
    reconcile_cataloging_operation_projections,
    refresh_job_progress,
    reset_run_for_retry,
    resume_job,
)
from app.services.cataloging.manual_ops import create_manual_candidate, has_usable_chapter_summary, recover_failed_run_for_review
from app.services.cataloging.orchestrator import create_cataloging_job, job_to_dict
from app.services.cataloging import orchestrator as cataloging_orchestrator
from app.services.cataloging.jsonl import (
    candidate_response_attempts,
    parse_candidate_response_records,
)
from app.services.cataloging.background_compactor import merge_background
from app.services.cataloging.merge import merge_text
from app.services.cataloging.worldbuilding_ops import _normalize_dimension
from app.services.character_merge_service import build_character_merge_preview, find_duplicate_character_candidates, merge_characters
from app.routers.cataloging import recover_current_cataloging_chapter


def complete_summary_payload(
    summary_text: str,
    *,
    scene_count: int = 1,
    characters: list[str] | None = None,
    worldbuilding: list[str] | None = None,
    relationships: list[dict] | None = None,
    character_profiles: list[str] | None = None,
    narrative_state: dict | None = None,
    **extra,
) -> dict:
    return {
        "summary_text": summary_text,
        "coverage_manifest": {
            "scene_count": scene_count,
            "characters": characters or [],
            "worldbuilding": worldbuilding or [],
            "relationships": relationships or [],
            "character_profiles": character_profiles or [],
        },
        "narrative_state": narrative_state or {
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
        "narrative_review": {
            "source": "provided",
            "outcome": "assessed",
            "evidence": "test fixture",
        },
        **extra,
    }


class CatalogingServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def test_job_history_paginates_past_twenty_without_cross_project_rows(self):
        from datetime import datetime
        from app.routers.cataloging import list_cataloging_jobs

        with self.Session() as db:
            project = Project(title="Long catalog history")
            other = Project(title="Other owner")
            db.add_all([project, other])
            db.flush()
            same_time = datetime(2026, 8, 31, 12, 0)
            identities = [f"job-{number:03}" for number in range(45)]
            db.add_all([CatalogingJob(id=identity, project_id=project.id, created_at=same_time)
                        for identity in identities])
            db.add(CatalogingJob(id="other-job", project_id=other.id, created_at=same_time))
            db.commit()
            received = []
            offset = 0
            page_sizes = []
            while True:
                page = list_cataloging_jobs(project.id, db, limit=20, offset=offset).data
                self.assertEqual(page["total"], 45)
                self.assertTrue(all(row["project_id"] == project.id for row in page["items"]))
                received.extend(row["id"] for row in page["items"])
                page_sizes.append(len(page["items"]))
                if page["next_offset"] is None:
                    break
                self.assertGreater(page["next_offset"], offset)
                offset = page["next_offset"]
            self.assertEqual(page_sizes, [20, 20, 5])
            self.assertEqual(received, sorted(identities, reverse=True))
            empty = list_cataloging_jobs(project.id, db, limit=20, offset=60).data
            self.assertEqual(empty["items"], [])
            self.assertEqual(empty["total"], 45)
            self.assertIsNone(empty["next_offset"])

    def test_job_projection_includes_authoritative_operation_activity(self):
        from datetime import datetime

        with self.Session() as db:
            project = Project(title="Visible catalog progress")
            db.add(project)
            db.flush()
            operation = OperationRun(
                id="operation-1",
                source_kind="cataloging",
                source_id="job-1",
                project_id=project.id,
                title="Catalog chapter",
                phase="candidates",
                current_message="模型进程仍在计算",
                process_metrics_json={"alive": True},
                last_activity_at=datetime(2026, 8, 31, 12, 34, 56),
            )
            job = CatalogingJob(
                id="job-1",
                project_id=project.id,
                status="running",
                operation_id=operation.id,
            )
            db.add_all([operation, job])
            db.commit()

            payload = job_to_dict(job)
            self.assertEqual(payload["current_stage"], "candidates")
            self.assertEqual(payload["current_message"], "模型进程仍在计算")
            self.assertTrue(payload["process_alive"])
            self.assertEqual(payload["last_activity_at"], "2026-08-31T12:34:56+00:00")

    def test_generated_target_ids_are_recorded_for_new_cataloging_rows(self):
        db = self.Session()
        try:
            project = Project(title="Generated ID lifecycle")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="Chapter One",
                content="The role crosses the old border.",
            )
            character = Character(
                project_id=project.id,
                name="Timeline role",
                role_type="supporting",
            )
            world = WorldbuildingEntry(
                project_id=project.id,
                dimension="geography",
                title="Old border",
                content="A disputed frontier.",
            )
            db.add_all([chapter, character, world])
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [chapter.id])
            run = job.chapter_runs[0]
            candidates = [
                CatalogingCandidate(
                    job_id=job.id,
                    chapter_run_id=run.id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    item_type="chapter_summary",
                    raw_payload=json.dumps(
                        {
                            "summary_text": "The role crosses the old border.",
                            "key_events": ["Crossed the border"],
                        }
                    ),
                ),
                CatalogingCandidate(
                    job_id=job.id,
                    chapter_run_id=run.id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    item_type="character_timeline",
                    raw_payload=json.dumps(
                        {
                            "name": character.name,
                            "event_description": "Crossed the old border.",
                        }
                    ),
                ),
                CatalogingCandidate(
                    job_id=job.id,
                    chapter_run_id=run.id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    item_type="worldbuilding_timeline",
                    raw_payload=json.dumps(
                        {
                            "title": world.title,
                            "event_description": "The border opened for one night.",
                        }
                    ),
                ),
            ]
            db.add_all(candidates)
            db.commit()

            events = apply_candidates_for_run(db, job, run)

            self.assertEqual(
                [event["type"] for event in events],
                ["candidate_applied"] * 3,
            )
            target_rows = {
                "chapter_summary": db.query(ChapterSummary).one(),
                "character_timeline": db.query(CharacterTimeline).one(),
                "worldbuilding_timeline": db.query(WorldbuildingTimeline).one(),
            }
            for candidate in candidates:
                with self.subTest(item_type=candidate.item_type):
                    self.assertEqual(
                        candidate.target_id,
                        target_rows[candidate.item_type].id,
                    )
                    log = (
                        db.query(CatalogingApplyLog)
                        .filter(CatalogingApplyLog.candidate_id == candidate.id)
                        .one()
                    )
                    self.assertEqual(log.target_id, target_rows[candidate.item_type].id)
        finally:
            db.close()

    def test_reconciles_completed_cataloging_job_into_task_center_projection(self):
        db = self.Session()
        try:
            project = Project(title="Projection repair")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第1章", content="正文")
            db.add(chapter)
            db.flush()
            operation = OperationRun(
                source_kind="cataloging",
                source_id="cataloging-repair",
                project_id=project.id,
                title="作品建档 · 1 章",
                status="running",
                health_status="disconnected",
                progress_mode="determinate",
                progress_current=0,
                progress_total=1,
            )
            db.add(operation)
            db.flush()
            job = CatalogingJob(
                id="cataloging-repair",
                project_id=project.id,
                operation_id=operation.id,
                status="completed",
                total_chapters=1,
                completed_chapters=1,
                execution_mode="auto",
            )
            db.add(job)
            db.flush()
            db.add(CatalogingChapterRun(
                job_id=job.id,
                project_id=project.id,
                chapter_id=chapter.id,
                status="completed",
                chapter_order=0,
            ))
            db.commit()

            self.assertEqual(reconcile_cataloging_operation_projections(db), 1)
            db.commit()
            db.refresh(operation)

            self.assertEqual(operation.status, "completed")
            self.assertEqual(operation.health_status, "active")
            self.assertEqual(operation.progress_current, 1)
            self.assertEqual(operation.progress_total, 1)
            self.assertEqual((operation.result_json or {}).get("outcome"), "completed_with_tools")
            self.assertIsNotNone(operation.completed_at)
        finally:
            db.close()

    def test_apply_candidates_updates_project_knowledge(self):
        db = self.Session()
        try:
            project = Project(title="Cataloging Project")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第1章 开端",
                content="张三来到青云宗。",
            )
            db.add_all([
                chapter,
                Character(project_id=project.id, name="李四", background="青云宗弟子。"),
            ])
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            for item_type, payload in [
                ("chapter_summary", {"summary_text": "张三来到青云宗。", "key_events": ["张三抵达青云宗"]}),
                ("outline_create", {"title": "第1章 开端", "node_type": "chapter", "summary": "张三来到青云宗。", "related_characters": ["张三"]}),
                ("outline_create", {"title": "第1章 开端-场景1 入宗门", "node_type": "section", "parent_title": "第1章 开端", "summary": "张三进入青云宗山门。", "scene_number": 1, "related_characters": ["张三"]}),
                ("character_create", {
                    "name": "张三",
                    "role_type": "protagonist",
                    "appearance": "原文未明示，按当前表现推定：少年修士，衣着朴素。",
                    "personality": "谨慎敏锐。",
                    "background": "初到青云宗。",
                    "abilities": ["观察灵气异常"],
                    "tone_style": "沉稳",
                    "catchphrases": ["先看清楚"],
                    "emotion_tendency": "克制",
                    "custom_system_prompt": "扮演张三时保持谨慎、克制，先观察局势再行动。",
                    "current_location": "青云宗",
                }),
                ("character_relationship", {"source_name": "张三", "target_name": "李四", "relationship_type": "同门", "description": "李四接引张三入宗。"}),
                ("worldbuilding_create", {"dimension": "geography", "title": "青云宗", "content": "修行宗门。"}),
                ("chapter_link", {"character_names": ["张三"], "worldbuilding_titles": ["青云宗"], "outline_title": "第1章 开端"}),
            ]:
                db.add(CatalogingCandidate(
                    job_id=job.id,
                    chapter_run_id=run.id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    item_type=item_type,
                    raw_payload=json.dumps(payload, ensure_ascii=False),
                ))
            db.commit()

            events = apply_candidates_for_run(db, job, run)

            self.assertEqual([event["type"] for event in events], ["candidate_applied"] * 7)
            self.assertEqual(db.query(Character).count(), 2)
            self.assertEqual(db.query(WorldbuildingEntry).count(), 1)
            self.assertEqual(db.query(CharacterRelationship).count(), 1)
            self.assertEqual(db.query(OutlineNode).count(), 3)
            volume = db.query(OutlineNode).filter(OutlineNode.node_type == "volume").one()
            chapter_node = db.query(OutlineNode).filter(OutlineNode.node_type == "chapter").one()
            self.assertEqual(chapter_node.parent_id, volume.id)
            section = db.query(OutlineNode).filter(OutlineNode.node_type == "section").first()
            self.assertEqual(section.parent_id, chapter_node.id)
            self.assertIsNotNone(chapter.summary)
            self.assertEqual(chapter.summary.summary_text, "张三来到青云宗。")
            self.assertIsNotNone(chapter.outline_node_id)
            character = db.query(Character).filter(Character.name == "张三").first()
            self.assertEqual(character.appearance, "原文未明示，按当前表现推定：少年修士，衣着朴素。")
            self.assertEqual(json.loads(character.abilities), ["观察灵气异常"])
            config = db.query(CharacterAIConfig).filter(CharacterAIConfig.character_id == character.id).first()
            self.assertEqual(config.tone_style, "沉稳")
            self.assertEqual(json.loads(config.catchphrases), ["先看清楚"])
            self.assertIn("谨慎", config.custom_system_prompt)
        finally:
            db.close()

    def test_retry_failed_run_clears_candidates_and_resets_job(self):
        db = self.Session()
        try:
            project = Project(title="Retry Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Retry Chapter", content="content")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            run.status = "failed"
            run.error = "parse failed"
            job.status = "paused_on_failure"
            job.blocked_chapter_id = run.chapter_id
            db.add(CatalogingCandidate(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                item_type="chapter_summary",
                raw_payload=json.dumps({"summary_text": "old"}, ensure_ascii=False),
            ))
            db.commit()

            reset_run_for_retry(db, job, first_blocking_run(db, job))
            db.commit()

            self.assertEqual(run.status, "pending")
            self.assertIsNone(run.error)
            self.assertEqual(job.status, "running")
            self.assertIsNone(job.blocked_chapter_id)
            self.assertEqual(db.query(CatalogingCandidate).count(), 0)
        finally:
            db.close()

    def test_try_create_candidate_skips_duplicate_worldbuilding_timeline_for_chapter(self):
        db = self.Session()
        try:
            project = Project(title="Duplicate Candidate Project")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第1章",
                content="特昂糖在议事厅揭露旁支账目问题。",
            )
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            line = json.dumps({
                "type": "worldbuilding_timeline",
                "payload": {
                    "dimension": "factions",
                    "title": "主脉与旁支的矛盾",
                    "event_description": "特昂糖在议事厅揭露旁支账目问题，引发冲突。",
                    "evidence": "特昂糖在议事厅揭露旁支账目问题",
                },
                "evidence": "特昂糖在议事厅揭露旁支账目问题",
            }, ensure_ascii=False)

            first = try_create_candidate(db, job, run, line, 0)
            self.assertIn("candidate", first)
            first["candidate"].status = "approved"
            db.commit()

            duplicate = try_create_candidate(db, job, run, line, 1)

            self.assertEqual(duplicate, {"duplicate": True})
            self.assertEqual(db.query(CatalogingCandidate).count(), 1)
        finally:
            db.close()

    def test_try_create_candidate_infers_empty_type_from_character_state_fields(self):
        db = self.Session()
        try:
            project = Project(title="Inferred Candidate Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第1章", content="张三来到青云宗。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            line = json.dumps({
                "name": "张三",
                "current_location": "青云宗山门",
                "current_goal": "通过入门考核",
                "life_status": "alive",
            }, ensure_ascii=False)

            created = try_create_candidate(db, job, run, line, 0)

            self.assertIn("candidate", created)
            self.assertEqual(created["candidate"].item_type, "character_state_update")
            self.assertEqual(db.query(CatalogingCandidate).count(), 1)
        finally:
            db.close()

    def test_try_create_candidate_accepts_appearance_only_character_state(self):
        db = self.Session()
        try:
            project = Project(title="Appearance State Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第9章", content="张三换上黑衣。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            line = json.dumps({
                "type": "character_state_update",
                "name": "张三",
                "appearance": "黑衣少年，袖口有新烧痕。",
            }, ensure_ascii=False)

            created = try_create_candidate(db, job, run, line, 0)

            self.assertIn("candidate", created)
            self.assertEqual(created["candidate"].item_type, "character_state_update")
            self.assertEqual(db.query(CatalogingCandidate).count(), 1)
        finally:
            db.close()

    def test_try_create_candidate_accepts_common_noncanonical_type_aliases(self):
        db = self.Session()
        try:
            project = Project(title="Alias Candidate Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第1章", content="张三来到青云宗。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            for index, raw in enumerate([
                {"type": "character_state", "name": "张三", "current_location": "青云宗"},
                {"type": "new_character", "name": "李四", "role_type": "同门"},
                {"type": "new_worldbuilding", "title": "青云宗", "category": "宗门", "content": "修仙宗门。"},
            ]):
                created = try_create_candidate(db, job, run, json.dumps(raw, ensure_ascii=False), index)
                self.assertIn("candidate", created)

            item_types = [item.item_type for item in db.query(CatalogingCandidate).order_by(CatalogingCandidate.sort_order).all()]
            self.assertEqual(item_types, ["character_state_update", "character_create", "worldbuilding_create"])
        finally:
            db.close()

    def test_try_create_candidate_accepts_plotpilot_style_chapter_state(self):
        db = self.Session()
        try:
            project = Project(title="Narrative State Candidate Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Chapter", content="A relay opens.")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            created = try_create_candidate(db, job, run, json.dumps({
                "type": "chapter_state",
                "events": [{"description": "The relay opens."}],
                "advanced_storylines": [{"description": "Network arc advances."}],
                "unresolved_actions": [{"description": "Trace the source."}],
            }, ensure_ascii=False), 0)

            self.assertIn("candidate", created)
            candidate = created["candidate"]
            self.assertEqual(candidate.item_type, "chapter_summary")
            payload = json.loads(candidate.raw_payload)
            self.assertIn("narrative_state", payload)
            self.assertEqual(len(payload["narrative_state"]["events"]), 1)
            self.assertIn("The relay opens.", payload["summary_text"])
            self.assertTrue(candidate_has_usable_summary(candidate))
        finally:
            db.close()

    def test_rich_narrative_candidate_without_summary_text_recovers_existing_run(self):
        """Regression for DeepSeek omitting only the redundant summary field."""

        db = self.Session()
        try:
            project = Project(title="Recovered Summary Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第一章 穿越", content="正文")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            summary = CatalogingCandidate(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                item_type="chapter_summary",
                operation="upsert",
                raw_payload=json.dumps({
                    "type": "chapter_summary",
                    "coverage_manifest": {
                        "scene_count": 1,
                        "characters": [],
                        "worldbuilding": [],
                        "relationships": [],
                        "character_profiles": [],
                    },
                    "narrative_state": {
                        "events": [
                            "主角穿越到陌生世界并确认自己的新身份。",
                            "家族议事中旁支发难，主角发现阵法账目异常。",
                            "父亲拿出查账结果，暂时化解本章冲突。",
                        ],
                        "unresolved_actions": ["调查石狮子眉心闪光。"],
                    },
                    "narrative_review": "本章已完成叙事治理检查。",
                }, ensure_ascii=False),
                status="pending",
                sort_order=0,
                source_task="chapter_cataloging",
            )
            outline = CatalogingCandidate(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                item_type="outline_create",
                operation="create",
                raw_payload=json.dumps({
                    "type": "outline_create",
                    "node_type": "chapter",
                    "title": chapter.title,
                    "summary": "主角发现阵法账目异常。",
                }, ensure_ascii=False),
                status="pending",
                sort_order=1,
                source_task="chapter_cataloging",
            )
            db.add_all([summary, outline])
            db.flush()

            recovered = candidate_payload(summary)
            coverage = inspect_candidate_coverage([summary, outline])
            self.assertTrue(candidate_has_usable_summary(summary))
            self.assertIn("家族议事", recovered["summary_text"])
            self.assertTrue(coverage.is_complete)
        finally:
            db.close()

    def test_try_create_candidate_infers_relationship_and_worldbuilding_without_type(self):
        db = self.Session()
        try:
            project = Project(title="Inferred Relationship Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Chapter", content="A meets B at Cloud Gate.")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            relationship = try_create_candidate(db, job, run, json.dumps({
                "source": "A",
                "target": "B",
                "relationship_type": "ally",
                "description": "A and B agree to travel together.",
            }, ensure_ascii=False), 0)
            worldbuilding = try_create_candidate(db, job, run, json.dumps({
                "worldbuilding_title": "Cloud Gate",
                "dimension": "geography",
                "content": "A mountain gate used by cultivators.",
            }, ensure_ascii=False), 1)

            self.assertIn("candidate", relationship)
            self.assertEqual(relationship["candidate"].item_type, "character_relationship")
            self.assertIn("candidate", worldbuilding)
            self.assertEqual(worldbuilding["candidate"].item_type, "worldbuilding_create")
        finally:
            db.close()

    def test_api_aggregate_archive_expands_and_applies_without_data_loss(self):
        """Regression for the one-object DeepSeek response seen in chapter two."""

        db = self.Session()
        try:
            project = Project(title="Aggregate API Project")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第二章 吐纳",
                content="约十六岁的林七身穿黑衣，完成吐纳并教给同伴。",
            )
            character = Character(project_id=project.id, name="林七")
            world = WorldbuildingEntry(
                project_id=project.id,
                title="吐纳体系",
                dimension="power_system",
                content="旧路径。",
            )
            db.add_all([chapter, character, world])
            db.commit()
            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            aggregate = {
                "chapter_summary": {
                    "title": chapter.title,
                    "summary": "林七完成吐纳并优化路径。",
                    "narrative_state": "修炼线正式推进。",
                    "narrative_review": "本章已检查修炼线和未决行动。",
                },
                "character_state_updates": [{
                    "name": "林七",
                    "operation": "update",
                    "data": {
                        "id": character.id,
                        "background": "林七在本章完成首次吐纳。",
                        "aliases": ["小七"],
                        "appearance": "黑衣少年",
                        "appearance_evidence": "林七身穿黑衣",
                        "age": "约十六岁",
                        "age_evidence": "约十六岁的林七",
                        "life_status": "alive",
                        "current_location": "练功院",
                        "realm_or_level": "引气入体",
                        "physical_state": "稳定",
                        "mental_state": "专注",
                        "current_goal": "验证新路径",
                        "active_conflict": "传统路径效率低",
                        "abilities_state": "能感知灵气",
                        "items_or_assets": "吐纳笔记",
                    },
                }],
                "worldbuilding_entries": [{
                    "operation": "update",
                    "id": world.id,
                    "dimension": "power_system",
                    "title": "吐纳体系",
                    "content": "林七将弯折路径改直后，灵气留存增加。",
                }],
                "outline_creates": [
                    {
                        "title": chapter.title,
                        "node_type": "chapter",
                        "summary": "完成吐纳并验证新路径。",
                    },
                    {
                        "title": "首次吐纳",
                        "node_type": "section",
                        "parent_id": chapter.title,
                        "scene_number": 1,
                        "purpose": "验证路径",
                        "location": "练功院",
                    },
                ],
                "chapter_links": [{
                    "source_type": "character",
                    "source_name": "林七",
                    "target_type": "outline",
                    "target_name": chapter.title,
                    "relation": "主角",
                    "order": 1,
                }],
                "narrative_ledger": [{
                    "type": "completed_beat",
                    "title": "首次吐纳完成",
                    "description": "林七成功引气入体。",
                    "evidence": "灵气在丹田稳定停留。",
                }],
            }

            results = try_create_candidates(
                db,
                job,
                run,
                json.dumps(aggregate, ensure_ascii=False),
                0,
            )
            candidates = [result["candidate"] for result in results if result.get("candidate")]

            self.assertFalse([result for result in results if result.get("bad_line") or result.get("skipped")])
            self.assertEqual(len(candidates), 7)
            self.assertEqual(
                [candidate.item_type for candidate in candidates].count("character_state_update"),
                1,
            )
            section_candidate = next(
                candidate
                for candidate in candidates
                if candidate.item_type == "outline_create"
                and json.loads(candidate.raw_payload).get("node_type") == "section"
            )
            section_payload = json.loads(section_candidate.raw_payload)
            self.assertEqual(section_payload["parent_title"], chapter.title)
            self.assertNotIn("parent_id", section_payload)

            events = apply_candidates_for_run(db, job, run)
            self.assertEqual(len(events), 7)
            volume = db.query(OutlineNode).filter(OutlineNode.node_type == "volume").one()
            chapter_node = db.query(OutlineNode).filter(OutlineNode.node_type == "chapter").one()
            section = db.query(OutlineNode).filter(OutlineNode.node_type == "section").one()
            self.assertEqual(chapter_node.parent_id, volume.id)
            self.assertEqual(section.parent_id, chapter_node.id)
            self.assertNotEqual(section.parent_id, chapter.title)
            self.assertEqual(character.current_location, "练功院")
            self.assertIn("首次吐纳", character.background)
            self.assertIn("改直", world.content)
        finally:
            db.close()

    def test_typed_chapter_summary_with_aggregate_arrays_expands_all_cards(self):
        """Regression for a typed DeepSeek summary wrapping the other cards."""

        db = self.Session()
        try:
            project = Project(title="Typed Aggregate API Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第一章 穿越", content="主角介入家族议事。")
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            aggregate = {
                "type": "chapter_summary",
                "summary_text": "主角介入家族议事并发现阵法账目异常。",
                "coverage_manifest": {
                    "scene_count": 1,
                    "characters": ["主角"],
                    "worldbuilding": ["护族大阵"],
                    "relationships": [],
                    "character_profiles": [],
                },
                "narrative_state": {
                    "events": ["主角指出阵法账目异常。"],
                    "unresolved_actions": ["调查石狮子闪光。"],
                },
                "narrative_review": "本章已完成叙事治理检查并记录未决行动。",
                "character_state_updates": [{
                    "character_name": "主角",
                    "appearance": "三岁幼童",
                    "age": "三岁",
                    "life_status": "alive",
                    "current_location": "家族议事厅",
                    "physical_state": "头疼",
                    "mental_state": "冷静",
                    "current_goal": "调查账目",
                    "active_conflict": "旁支阻挠",
                    "abilities_state": "观察灵气",
                    "items_or_assets": "无",
                }],
                "worldbuilding_entries": [{
                    "type": "worldbuilding_create",
                    "dimension": "power_system",
                    "name": "护族大阵",
                    "description": "依靠灵石维持的家族阵法。",
                }],
                "outline_creates": [{
                    "type": "outline_create",
                    "node_type": "chapter",
                    "title": chapter.title,
                    "summary": "主角揭穿阵法账目异常。",
                }],
                "chapter_links": [{
                    "type": "chapter_link",
                    "source_type": "character",
                    "source_name": "主角",
                    "target_type": "chapter",
                    "target_name": chapter.title,
                    "relation": "主角出场",
                }, {
                    "type": "chapter_link",
                    "worldbuilding_titles": ["护族大阵"],
                    "description": "本章涉及护族大阵。",
                }],
            }

            results = try_create_candidates(
                db,
                job,
                run,
                json.dumps(aggregate, ensure_ascii=False),
                0,
            )
            candidates = [result["candidate"] for result in results if result.get("candidate")]
            coverage = inspect_candidate_coverage(candidates)

            self.assertFalse([
                result
                for result in results
                if result.get("bad_line") or result.get("skipped")
            ])
            self.assertEqual(
                [candidate.item_type for candidate in candidates],
                [
                    "chapter_summary",
                    "character_state_update",
                    "worldbuilding_create",
                    "outline_create",
                    "chapter_link",
                    "chapter_link",
                ],
            )
            self.assertTrue(coverage.is_complete)
        finally:
            db.close()

    def test_candidate_response_parser_accepts_common_provider_shapes(self):
        summary = {
            "type": "chapter_summary",
            "summary_text": "主角发现阵法异常。",
            "narrative_state": {"events": ["发现异常"]},
        }
        outline = {
            "type": "outline_create",
            "node_type": "chapter",
            "title": "第一章",
            "summary": "主角发现阵法异常。",
        }
        typed_aggregate = {
            **summary,
            "outline_creates": [outline],
        }
        cases = {
            "jsonl": "\n".join([
                json.dumps(summary, ensure_ascii=False),
                json.dumps(outline, ensure_ascii=False),
            ]),
            "array": json.dumps([summary, outline], ensure_ascii=False, indent=2),
            "fenced_typed_aggregate": (
                "模型结果如下：\n```json\n"
                + json.dumps(typed_aggregate, ensure_ascii=False, indent=2)
                + "\n```"
            ),
            "collection_wrapper": json.dumps(
                {"candidates": [summary, outline]},
                ensure_ascii=False,
                indent=2,
            ),
            "string_output_wrapper": json.dumps(
                {"output": json.dumps([summary, outline], ensure_ascii=False)},
                ensure_ascii=False,
            ),
            "mixed_json_and_prose": (
                json.dumps(summary, ensure_ascii=False)
                + "\n下面是大纲：\n"
                + json.dumps(outline, ensure_ascii=False, indent=2)
            ),
        }

        for label, response in cases.items():
            with self.subTest(label=label):
                records = parse_candidate_response_records(response)
                self.assertEqual(
                    [record.get("type") for record in records],
                    ["chapter_summary", "outline_create"],
                )

    def test_failed_run_recovers_complete_typed_aggregate_from_raw_output(self):
        db = self.Session()
        try:
            project = Project(title="Raw Recovery Project")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第一章 穿越",
                content="主角发现阵法异常。",
            )
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", "deepseek:test", [])
            run = job.chapter_runs[0]

            summary_line = json.dumps({
                "type": "chapter_summary",
                "summary_text": "主角发现阵法异常。",
            }, ensure_ascii=False)
            created_summary = try_create_candidate(db, job, run, summary_line, 0)
            self.assertIn("candidate", created_summary)

            aggregate = {
                "type": "chapter_summary",
                "summary_text": "主角发现阵法异常并决定追查。",
                "coverage_manifest": {
                    "scene_count": 1,
                    "characters": [],
                    "worldbuilding": ["护族大阵"],
                    "relationships": [],
                    "character_profiles": [],
                },
                "narrative_state": {
                    "events": ["发现阵法异常"],
                    "unresolved_actions": ["追查异常来源"],
                },
                "outline_creates": [{
                    "type": "outline_create",
                    "node_type": "chapter",
                    "title": chapter.title,
                    "summary": "主角发现异常并决定追查。",
                }],
                "worldbuilding_entries": [{
                    "type": "worldbuilding_create",
                    "title": "护族大阵",
                    "dimension": "power_system",
                    "content": "阵法依赖灵石维持。",
                }],
                "chapter_links": [{
                    "type": "chapter_link",
                    "worldbuilding_titles": ["护族大阵"],
                    "description": "本章发现阵法异常。",
                }],
            }
            response = json.dumps(aggregate, ensure_ascii=False, indent=2)
            run.raw_output = (
                f"=== CANDIDATE RESOLUTION ===\n{response}\n\n"
                f"=== CANDIDATE RESOLUTION RETRY 2 ===\n{response}\n\n"
                f"=== CANDIDATE RESOLUTION RETRY 3 ===\n{response}"
            )
            run.status = "failed"
            db.flush()

            self.assertEqual(len(candidate_response_attempts(run.raw_output)), 3)
            recovered = recover_candidates_from_raw_output(db, job, run)
            candidates = db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
            ).all()
            coverage = inspect_candidate_coverage(candidates)

            self.assertEqual(recovered["attempt_from_end"], 1)
            self.assertTrue(recovered["coverage"].is_complete)
            self.assertTrue(coverage.is_complete)
            self.assertEqual(
                {candidate.item_type for candidate in candidates},
                {"chapter_summary", "outline_create", "worldbuilding_create", "chapter_link"},
            )

            count_before = len(candidates)
            recovered_again = recover_candidates_from_raw_output(db, job, run)
            self.assertTrue(recovered_again["coverage"].is_complete)
            self.assertEqual(
                db.query(CatalogingCandidate).filter(
                    CatalogingCandidate.chapter_run_id == run.id,
                ).count(),
                count_before,
            )
        finally:
            db.close()

    def test_try_create_candidate_recovers_candidate_type_misplaced_in_node_type(self):
        """DeepSeek may use node_type for the candidate type itself.

        This mirrors the packaged-app incident where scenes were stored as
        chapter summaries and worldbuilding cards were stored as characters.
        """
        db = self.Session()
        try:
            project = Project(title="DeepSeek Recovery Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第二章 吐纳", content="陆家议事厅内灵气骤变。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            raw_candidates = [
                {
                    "node_type": "outline_create",
                    "title": "议事厅对峙",
                    "parent_title": "第二章 吐纳",
                    "scene_number": 1,
                    "purpose": "揭示旁支矛盾",
                    "entry_state": "众人争执",
                    "exit_state": "真相初显",
                    "summary": "特昂糖在议事厅与旁支对峙。",
                },
                {
                    "node_type": "worldbuilding_create",
                    "name": "陆家护族大阵",
                    "dimension": "power_system",
                    "description": "与陆家血脉绑定的祖传防护阵法。",
                    "significance": "后续危机的重要防线。",
                },
                {
                    "node_type": "worldbuilding_create",
                    "name": "游戏世界规则",
                    "dimension": "culture",
                    "description": "物品会自动归位，NPC 行为遵循固定模式。",
                },
            ]

            results = [
                try_create_candidate(db, job, run, json.dumps(raw, ensure_ascii=False), index)
                for index, raw in enumerate(raw_candidates)
            ]

            self.assertTrue(all("candidate" in result for result in results))
            candidates = [result["candidate"] for result in results]
            self.assertEqual(
                [candidate.item_type for candidate in candidates],
                ["outline_create", "worldbuilding_create", "worldbuilding_create"],
            )
            outline_payload = json.loads(candidates[0].raw_payload)
            self.assertEqual(outline_payload["node_type"], "section")
            self.assertEqual(outline_payload["title"], "议事厅对峙")
            world_payload = json.loads(candidates[1].raw_payload)
            self.assertEqual(world_payload["title"], "陆家护族大阵")
            self.assertEqual(world_payload["content"], "与陆家血脉绑定的祖传防护阵法。")
        finally:
            db.close()

    def test_try_create_candidate_accepts_single_key_cataloging_wrappers(self):
        db = self.Session()
        try:
            project = Project(title="Wrapped Candidate Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第一章 穿越·着陆", content="特昂糖揭露账目问题。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            samples = [
                {
                    "completed_beat": {
                        "beat": "特昂糖当众揭露账目问题",
                        "chapter": 1,
                        "evidence": "陆承宇拿出证据证实",
                    }
                },
                {
                    "revealed_clue": {
                        "clue": "石狮子眉心闪光",
                        "chapter": 1,
                        "evidence": "眉心闪了一下",
                    }
                },
                {
                    "narrative_promise": {
                        "promise": "特昂糖的回归之路",
                        "chapter": 1,
                        "description": "必须回到原来的世界",
                        "status": "open",
                    }
                },
                {
                    "storyline_state": {
                        "storyline": "主脉与旁支的矛盾",
                        "chapter": 1,
                        "state": "矛盾暂时平息但未解决",
                    }
                },
                {
                    "worldbuilding_timeline": {
                        "event": "议事厅账目对峙",
                        "description": "周氏被迫退让",
                        "related_worldbuilding": ["陆家", "护族大阵"],
                    }
                },
                {
                    "chapter_link": {
                        "source": "第一章 穿越·着陆",
                        "target": "护族大阵",
                        "relation": "introduces",
                        "description": "第一章引入护族大阵",
                    }
                },
                {
                    "chapter_link": {
                        "source": "陆老爷子",
                        "target": "特昂糖",
                        "relation": "grandfather_of",
                        "description": "陆老爷子是特昂糖的爷爷",
                    }
                },
            ]

            created = [
                try_create_candidate(db, job, run, json.dumps(raw, ensure_ascii=False), index)
                for index, raw in enumerate(samples)
            ]

            self.assertTrue(all("candidate" in result for result in created), created)
            candidates = [result["candidate"] for result in created]
            self.assertEqual(
                [candidate.item_type for candidate in candidates],
                [
                    "chapter_summary",
                    "chapter_summary",
                    "chapter_summary",
                    "chapter_summary",
                    "worldbuilding_timeline",
                    "chapter_link",
                    "character_relationship",
                ],
            )
            narrative_payloads = [json.loads(candidate.raw_payload) for candidate in candidates[:4]]
            self.assertTrue(all(payload.get("narrative_state") for payload in narrative_payloads))
            timeline_payload = json.loads(candidates[4].raw_payload)
            self.assertEqual(timeline_payload["title"], "陆家")
            self.assertEqual(timeline_payload["event_description"], "周氏被迫退让")
            relationship_payload = json.loads(candidates[6].raw_payload)
            self.assertEqual(relationship_payload["relationship_type"], "grandfather_of")
        finally:
            db.close()

    def test_typed_ledger_candidates_populate_structured_narrative_governance(self):
        db = self.Session()
        try:
            project = Project(title="Typed Ledger Project")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第一章 穿越·着陆",
                content="石狮异动，特昂糖决定追查回归之路。",
            )
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            samples = [
                {"type": "completed_beat", "beat": "特昂糖确认自己已经穿越", "evidence": "她认出陌生院落"},
                {"type": "revealed_clue", "clue": "石狮眉心会发光", "evidence": "石狮眉心闪了一下"},
                {
                    "type": "narrative_promise",
                    "promise": "寻找返回原世界的方法",
                    "status": "open",
                    "evidence": "特昂糖决定追查穿越原因",
                },
                {"type": "storyline_state", "storyline": "回归主线", "state": "开始调查"},
                {
                    "type": "narrative_promise",
                    "promise": "石狮异动已经解释",
                    "status": "fulfilled",
                    "evidence": "只有标题相似，没有原治理项 ID",
                },
                {
                    "type": "chapter_summary",
                    "summary_text": "特昂糖确认穿越，并开始调查石狮与回归线索。",
                    "narrative_state": {
                        "unresolved_actions": [
                            {"title": "查明石狮异动原因", "evidence": "本章尚未查明"},
                        ],
                    },
                },
            ]
            created = [
                try_create_candidate(db, job, run, json.dumps(raw, ensure_ascii=False), index)
                for index, raw in enumerate(samples)
            ]

            self.assertTrue(all("candidate" in result for result in created), created)
            payloads = [json.loads(result["candidate"].raw_payload) for result in created]
            self.assertEqual(payloads[0]["narrative_state"]["events"][0]["title"], "特昂糖确认自己已经穿越")
            self.assertEqual(payloads[1]["narrative_state"]["reader_known_facts"][0]["title"], "石狮眉心会发光")
            self.assertEqual(payloads[2]["narrative_state"]["foreshadowing_planted"][0]["title"], "寻找返回原世界的方法")
            self.assertEqual(payloads[3]["narrative_state"]["storyline_progress"][0]["title"], "回归主线")
            self.assertEqual(payloads[4]["narrative_state"]["foreshadowing_resolved"][0]["status"], "pending_review")

            events = apply_candidates_for_run(db, job, run)

            self.assertTrue(all(event["type"] == "candidate_applied" for event in events), events)
            hooks = db.query(Foreshadowing).all()
            debts = db.query(NarrativeDebt).all()
            self.assertEqual(len(hooks), 1)
            self.assertEqual(hooks[0].title, "寻找返回原世界的方法")
            self.assertEqual(hooks[0].status, "open")
            self.assertEqual(len(debts), 1)
            self.assertEqual(debts[0].title, "查明石狮异动原因")
            self.assertEqual(debts[0].status, "open")
            self.assertTrue(any(
                "unlinked_foreshadowing_resolution"
                in event["data"]["new_value"]["narrative_governance"]["warnings"]
                for event in events
                if event.get("data", {}).get("new_value", {}).get("narrative_governance")
            ))
            review = db.query(ChapterGovernanceReview).one()
            self.assertEqual(review.status, "assessed")
            self.assertEqual(review.source, "provided")
            self.assertEqual(review.findings_count, 3)
        finally:
            db.close()

    def test_missing_chapter_outline_title_falls_back_to_source_chapter(self):
        db = self.Session()
        try:
            project = Project(title="Outline Recovery Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第一章 穿越·着陆", content="正文。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            result = try_create_candidate(
                db,
                job,
                run,
                json.dumps({
                    "type": "outline_create",
                    "payload": {"node_type": "chapter", "summary": "特昂糖在陆家醒来。"},
                }, ensure_ascii=False),
                0,
            )

            self.assertIn("candidate", result)
            payload = json.loads(result["candidate"].raw_payload)
            self.assertEqual(payload["title"], chapter.title)
        finally:
            db.close()

    def test_unknown_empty_type_error_contains_fields_and_snippet(self):
        db = self.Session()
        try:
            project = Project(title="Unknown Candidate Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Chapter", content="Nothing useful.")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            result = try_create_candidate(db, job, run, json.dumps({"foo": "bar"}, ensure_ascii=False), 0)

            self.assertIn("error", result)
            self.assertIn("<empty>", result["error"])
            self.assertIn("raw_fields", result["error"])
            self.assertIn("snippet", result["error"])
            self.assertNotEqual(result["error"].strip(), "未知 type:")
        finally:
            db.close()

    def test_try_create_candidate_skips_empty_character_and_worldbuilding_shells(self):
        db = self.Session()
        try:
            project = Project(title="Empty Shell Candidate Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Chapter", content="A vague chapter.")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            examples = [
                {"type": "character_state_update", "current_location": "山门"},
                {"type": "character_create", "name": "未命名角色"},
                {
                    "type": "character_update",
                    "name": "边界石碑之谜",
                    "description": "石碑阵纹异动，成为后续线索。",
                    "evidence": "章末石碑发光。",
                },
                {
                    "type": "character_create",
                    "name": "巫妖争夺遗骨",
                    "summary": "两个势力围绕遗骨展开争夺。",
                },
                {"type": "worldbuilding_create", "title": "灵气潮汐", "dimension": "power_system"},
                {"type": "worldbuilding_timeline", "event_description": "灵潮出现"},
            ]

            results = [
                try_create_candidate(db, job, run, json.dumps(raw, ensure_ascii=False), index)
                for index, raw in enumerate(examples)
            ]

            self.assertTrue(all(item.get("skipped") for item in results))
            self.assertTrue(any("未命名角色" in item.get("reason", "") for item in results))
            self.assertTrue(any("边界石碑之谜" in item.get("reason", "") for item in results))
            self.assertTrue(any("巫妖争夺遗骨" in item.get("reason", "") for item in results))
            self.assertTrue(any("世界观候选" in item.get("reason", "") for item in results))
            self.assertEqual(db.query(CatalogingCandidate).count(), 0)
        finally:
            db.close()

    def test_try_create_candidate_accepts_character_with_persistable_person_detail(self):
        db = self.Session()
        try:
            project = Project(title="Character Candidate Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Chapter", content="陆老爷子按住刀柄。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            result = try_create_candidate(db, job, run, json.dumps({
                "type": "character_create",
                "name": "陆老爷子",
                "role_type": "mentor",
                "personality": "沉稳谨慎",
            }, ensure_ascii=False), 0)

            self.assertFalse(result.get("skipped"))
            self.assertEqual(result["candidate"].target_name, "陆老爷子")
        finally:
            db.close()

    def test_character_candidate_preserves_structured_role_enum(self):
        db = self.Session()
        try:
            project = Project(title="Canonical Role Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Chapter", content="特昂糖推门而入。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            result = try_create_candidate(db, job, run, json.dumps({
                "type": "character_create",
                "name": "特昂糖",
                "role_type": "protagonist",
                "background": "前世为研究员，现为陆家幼女",
            }, ensure_ascii=False), 0)

            payload = candidate_payload(result["candidate"])
            self.assertEqual(payload["role_type"], "protagonist")
            self.assertEqual(payload["background"], "前世为研究员，现为陆家幼女")
        finally:
            db.close()

    def test_context_includes_richer_character_and_worldbuilding_details(self):
        db = self.Session()
        try:
            project = Project(title="Context Project")
            db.add(project)
            db.flush()
            previous = Chapter(project_id=project.id, title="Previous", content="previous")
            current = Chapter(project_id=project.id, title="Current", content="current")
            character = Character(
                project_id=project.id,
                name="Hero",
                role_type="protagonist",
                appearance="plain robe",
                personality="careful",
                background="escaped from the old sect",
                abilities=json.dumps(["array reading"], ensure_ascii=False),
                life_status="alive",
                current_location="valley",
                realm_or_level="foundation",
                mental_state="focused",
                active_conflict="must seal the gate",
                abilities_state="cannot use full power",
                items_or_assets="jade token",
            )
            entry = WorldbuildingEntry(
                project_id=project.id,
                dimension="power_system",
                title="Array Rules",
                content="Arrays require anchor stones and fail when anchors are corrupted.",
            )
            db.add_all([previous, current, character, entry])
            db.flush()
            db.add(CharacterAIConfig(
                character_id=character.id,
                tone_style="calm",
                catchphrases=json.dumps(["wait"], ensure_ascii=False),
                custom_system_prompt="Keep the hero calm and tactical.",
            ))
            db.commit()

            context = build_light_context(db, project.id, current)

            self.assertEqual(context["character_details"][0]["background"], "escaped from the old sect")
            self.assertEqual(context["character_details"][0]["mental_state"], "focused")
            self.assertEqual(context["character_details"][0]["items_or_assets"], "jade token")
            self.assertEqual(context["character_details"][0]["ai_style"]["tone_style"], "calm")
            self.assertEqual(context["worldbuilding_details"][0]["content"], "Arrays require anchor stones and fail when anchors are corrupted.")
            self.assertEqual(context["previous_character_states"][0]["active_conflict"], "must seal the gate")
        finally:
            db.close()

    def test_world_context_selects_relevant_entries_beyond_initial_sort_window(self):
        db = self.Session()
        try:
            project = Project(title="World Context Project")
            db.add(project)
            db.flush()
            for index in range(40):
                db.add(WorldbuildingEntry(
                    project_id=project.id,
                    dimension="culture",
                    title=f"无关习俗{index}",
                    content="普通年节礼仪，与当前归寂谷剧情无关。",
                    sort_order=index,
                ))
            late_entry = WorldbuildingEntry(
                project_id=project.id,
                dimension="power_system",
                title="归寂谷黄泉回路",
                content="归寂谷可以用黄泉回路引导死气，但会受到归墟阵灵石余量限制。",
                sort_order=999,
            )
            outline = OutlineNode(
                project_id=project.id,
                node_type="chapter",
                title="第151章 旧档藏线",
                summary="特昂糖在归寂谷查看旧档，追查黄泉回路与归墟阵灵石消耗。",
                sort_order=151,
            )
            db.add_all([late_entry, outline])
            db.commit()

            context = _build_world_context(
                db,
                project.id,
                outline.id,
                query_context="继续写归寂谷黄泉回路和归墟阵灵石倒计时",
            )

            self.assertIn("归寂谷黄泉回路", context)
            self.assertIn("已从 41 条世界观中筛选", context)
        finally:
            db.close()

    def test_worldbuilding_dimension_accepts_common_aliases(self):
        self.assertEqual(_normalize_dimension("修炼体系"), "power_system")
        self.assertEqual(_normalize_dimension("宗门"), "factions")
        self.assertEqual(_normalize_dimension("地点"), "geography")
        self.assertEqual(_normalize_dimension("culture"), "culture")
        self.assertEqual(
            _normalize_dimension("", {"title": "血魔病毒", "content": "感染者会被黑线回收"}),
            "power_system",
        )

    def test_background_merge_compacts_repeated_history(self):
        chapter = Chapter(title="Chapter 9")
        existing = (
            "As heir, Mira guarded the valley.\n\n"
            "《Chapter 8》：As heir, Mira guarded the valley.\n\n"
            "She once hid under a false name."
        )
        incoming = "As heir, Mira guarded the valley. She revealed the false name to protect her sect."

        merged = merge_background(existing, incoming, chapter, limit=140)

        self.assertLessEqual(len(merged), 140)
        self.assertEqual(merged.count("As heir"), 1)
        self.assertNotIn("《Chapter 8》", merged)
        self.assertIn("false name", merged)

    def test_background_merge_rewrite_replaces_every_contained_fragment(self):
        chapter = Chapter(title="空出的排期")
        existing = (
            "栏目负责人交办综述稿；"
            "周芷确认三份材料不能独立证明18:50；"
            "她拒稿并让出排期；"
            "组织者无法说明模板的原始依据"
        )
        incoming = (
            "栏目负责人交办综述稿，周芷确认三份材料不能独立证明18:50，"
            "她拒稿并让出排期；她转去公开演练，组织者无法说明模板的原始依据"
        )

        merged = merge_background(existing, incoming, chapter, limit=1000)

        self.assertEqual(merged.count("周芷确认三份材料不能独立证明18:50"), 1)
        self.assertEqual(merged.count("她拒稿并让出排期"), 1)
        self.assertEqual(merged.count("组织者无法说明模板的原始依据"), 1)

    def test_merge_text_replaces_same_chapter_section_and_keeps_later_sections(self):
        chapter = Chapter(title="第二章")
        existing = "谨慎\n\n《第二章》：旧版变化\n\n《第三章》：后续变化"

        merged = merge_text(existing, "新版变化", chapter)

        self.assertEqual(merged.count("《第二章》"), 1)
        self.assertNotIn("旧版变化", merged)
        self.assertIn("《第二章》：新版变化", merged)
        self.assertIn("《第三章》：后续变化", merged)

    def test_merge_text_removes_exact_fragments_from_cumulative_world_update(self):
        chapter = Chapter(title="模板上的18:50")
        existing = (
            "用途：通信值班场所。"
            "环境：单层值班台与蓝白灯光。"
            "进入条件：内部证据调阅需审批。"
        )
        incoming = (
            "用途：港务调度通信值班场所。"
            "环境：单层值班台与蓝白灯光。"
            "进入条件：内部证据调阅需审批。"
            "本章新增：2015年3月通知的收文单位包含本值班室。"
        )

        merged = merge_text(existing, incoming, chapter)

        self.assertEqual(merged.count("环境：单层值班台与蓝白灯光"), 1)
        self.assertEqual(merged.count("进入条件：内部证据调阅需审批"), 1)
        self.assertIn("《模板上的18:50》：用途：港务调度通信值班场所", merged)
        self.assertIn("本章新增：2015年3月通知", merged)

    def test_merge_text_same_chapter_shorter_rewrite_removes_old_contribution(self):
        chapter = Chapter(title="第二章")
        existing = "作者基线。\n\n《第二章》：旧事实一。旧事实二。"

        merged = merge_text(existing, "修订后只保留事实一。", chapter)

        self.assertEqual(merged, "作者基线。\n\n《第二章》：修订后只保留事实一。")
        self.assertNotIn("旧事实二", merged)

    def test_character_state_omits_unchanged_fields_without_losing_the_card(self):
        db = self.Session()
        try:
            project = Project(title="Sparse State Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Reply", content="Mira agrees to meet.")
            character = Character(
                project_id=project.id,
                name="Mira",
                appearance="Short hair, a repaired watch strap and ink on her sleeve.",
                age="29",
                background="A reporter with three years of experience.",
                personality="Patient and precise.",
                current_location="Office",
                current_goal="Wait for a reply",
                items_or_assets="Notebook and the signed receipt",
            )
            db.add_all([chapter, character])
            db.commit()
            preserved_fields = (
                "appearance", "age", "background", "personality", "current_location", "items_or_assets",
            )
            before = {field: getattr(character, field) for field in preserved_fields}
            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            db.add(CatalogingCandidate(
                job_id=job.id, chapter_run_id=run.id, project_id=project.id,
                chapter_id=chapter.id, item_type="character_state_update",
                raw_payload=json.dumps({"id": character.id, "name": "Mira", "current_goal": "Attend the agreed meeting"}),
            ))
            db.commit()

            apply_candidates_for_run(db, job, run)
            db.expire_all()

            self.assertEqual({field: getattr(character, field) for field in preserved_fields}, before)
            self.assertEqual(character.current_goal, "Attend the agreed meeting")
            self.assertEqual(character.last_seen_chapter_id, chapter.id)
            version = db.query(CharacterVersion).filter(CharacterVersion.character_id == character.id).one()
            self.assertIn("当前目标", version.change_summary)
            self.assertNotIn("外貌", version.change_summary)
            self.assertNotIn("年龄/时间状态", version.change_summary)
        finally:
            db.close()

    def test_rest_and_workspace_apply_share_terminal_state_and_mirror_outbox(self):
        from app.routers.cataloging import apply_pending_cataloging as apply_via_rest
        from app.services.workspace.tools.cataloging import (
            apply_pending_cataloging as apply_via_workspace,
        )

        db = self.Session(autoflush=False)
        db.info["siming_skip_content_sync_dispatch"] = True

        def prepare(label: str):
            project = Project(title=f"Transport parity {label}")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title=f"第1章 {label}",
                content="林舟打开档案，确认记录仍然有效。",
                current_version=1,
                cataloging_required=True,
            )
            db.add(chapter)
            db.flush()
            planned_outline = OutlineNode(
                project_id=project.id,
                node_type="chapter",
                title=chapter.title,
                summary="作者规划摘要",
                planned_summary="作者规划摘要",
                status="pending",
                sort_order=1,
            )
            db.add(planned_outline)
            db.flush()
            chapter.outline_node_id = planned_outline.id
            operation = OperationRun(
                source_kind="cataloging",
                source_id=f"transport-{label}",
                project_id=project.id,
                title=f"作品建档 {label}",
                status="waiting_user",
                progress_mode="determinate",
                progress_current=0,
                progress_total=1,
            )
            db.add(operation)
            db.flush()
            agent_run = AgentRun(
                project_id=project.id,
                source="internal",
                title=f"建档 Agent {label}",
                operation_id=operation.id,
                status="waiting_confirmation",
            )
            db.add(agent_run)
            db.flush()
            job = CatalogingJob(
                project_id=project.id,
                status="waiting_confirmation",
                execution_mode="manual",
                execution_backend="external_agent",
                agent_run_id=agent_run.id,
                operation_id=operation.id,
                current_chapter_id=chapter.id,
                blocked_chapter_id=chapter.id,
                total_chapters=1,
            )
            db.add(job)
            db.flush()
            run = CatalogingChapterRun(
                job_id=job.id,
                project_id=project.id,
                chapter_id=chapter.id,
                chapter_version=1,
                status="awaiting_confirmation",
                chapter_order=0,
            )
            db.add(run)
            db.flush()
            create_manual_candidate(
                db,
                job,
                run,
                "chapter_summary",
                complete_summary_payload(
                    "林舟确认档案记录仍然有效。",
                    scene_count=1,
                ),
                "accepted",
            )
            create_manual_candidate(
                db,
                job,
                run,
                "outline_create",
                {
                    "title": chapter.title,
                    "node_type": "chapter",
                    "summary": "林舟确认档案记录仍然有效。",
                },
                "accepted",
            )
            return project, chapter, job, run, agent_run, operation

        try:
            rest = prepare("REST")
            workspace = prepare("Workspace")
            db.commit()

            rest_response = asyncio.run(apply_via_rest(rest[0].id, rest[2].id, db))
            workspace_response = asyncio.run(
                apply_via_workspace(db, workspace[0].id, {"job_id": workspace[2].id})
            )
            db.flush()

            self.assertEqual(rest_response.code, 0)
            self.assertEqual(workspace_response["status"], "ok")
            self.assertEqual(
                rest_response.data["run"]["status"],
                workspace_response["data"]["run"]["status"],
            )
            self.assertIn(rest_response.data["run"]["status"], {"completed", "completed_with_warnings"})

            for project, chapter, job, run, agent_run, operation in (rest, workspace):
                for entity in (chapter, job, run, agent_run, operation):
                    db.refresh(entity)
                self.assertEqual(job.status, "completed")
                self.assertEqual(job.completed_chapters, 1)
                self.assertIsNotNone(job.completed_at)
                self.assertIsNone(job.current_chapter_id)
                self.assertIsNone(job.blocked_chapter_id)
                self.assertFalse(chapter.cataloging_required)
                outline = db.query(OutlineNode).filter(
                    OutlineNode.id == chapter.outline_node_id
                ).one()
                self.assertEqual(outline.status, "completed")
                self.assertEqual(outline.planned_summary, "作者规划摘要")
                self.assertEqual(agent_run.status, "completed")
                self.assertIsNotNone(agent_run.completed_at)
                self.assertEqual(operation.status, "completed")
                self.assertEqual(operation.progress_current, 1)
                self.assertEqual(
                    (operation.result_json or {}).get("outcome"),
                    "completed_with_tools",
                )
                sync_jobs = db.query(ContentSyncJob).filter(
                    ContentSyncJob.project_id == project.id,
                    ContentSyncJob.target == "project",
                ).all()
                self.assertEqual(len(sync_jobs), 1)
                self.assertEqual(sync_jobs[0].source, "cataloging_apply")
        finally:
            db.close()

    def test_worldbuilding_create_requires_model_identity_review_in_existing_project(self):
        db = self.Session()
        try:
            project = Project(title="World identity review")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="原件与范围", content="核读发文底档原件。")
            existing = WorldbuildingEntry(
                project_id=project.id,
                dimension="history",
                title="《关于规范港务应急通信汇总口径的通知》（发文底档）",
                content="2015年3月11日正式发文底档。",
                status="active",
            )
            related_existing = WorldbuildingEntry(
                project_id=project.id,
                dimension="history",
                title="馆藏发文底档著录索引",
                content="用于定位发文底档原件的馆藏索引。",
                status="active",
            )
            db.add_all([chapter, existing, related_existing])
            db.commit()
            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            db.add(CatalogingFact(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                fact_type="worldbuilding_fact",
                raw_payload=json.dumps(
                    {
                        "canonical_title_hint": "发文底档",
                        "details": "本章核读发文底档及其馆藏索引。",
                    },
                    ensure_ascii=False,
                ),
                status="active",
            ))
            db.commit()
            raw = {
                "type": "worldbuilding_create",
                "title": "发文底档",
                "dimension": "history",
                "content": "本章核读的原件底档。",
            }

            missing = try_create_candidate(
                db, job, run, json.dumps(raw, ensure_ascii=False), 1
            )
            self.assertIn("identity_resolution", missing["error"])
            self.assertEqual(db.query(CatalogingCandidate).count(), 0)

            invalid = try_create_candidate(
                db,
                job,
                run,
                json.dumps(
                    {
                        **raw,
                        "identity_resolution": {
                            "decision": "create",
                            "reviewed_existing_ids": ["not-a-real-id"],
                            "reason": "不同实体",
                        },
                    },
                    ensure_ascii=False,
                ),
                1,
            )
            self.assertIn("不存在", invalid["error"])
            self.assertEqual(db.query(CatalogingCandidate).count(), 0)

            incomplete_review = try_create_candidate(
                db,
                job,
                run,
                json.dumps(
                    {
                        **raw,
                        "title": "独立鉴定机构业务规则",
                        "identity_resolution": {
                            "decision": "create",
                            "reviewed_existing_ids": [existing.id],
                            "reason": "旧条目是具体发文原件，本条描述独立机构的业务受理规则。",
                        },
                    },
                    ensure_ascii=False,
                ),
                1,
            )
            self.assertIn("未覆盖本章已交付", incomplete_review["error"])
            self.assertIn(related_existing.id, incomplete_review["error"])
            self.assertEqual(db.query(CatalogingCandidate).count(), 0)

            accepted = try_create_candidate(
                db,
                job,
                run,
                json.dumps(
                    {
                        **raw,
                        "title": "独立鉴定机构业务规则",
                        "identity_resolution": {
                            "decision": "create",
                            "reviewed_existing_ids": [existing.id, related_existing.id],
                            "reason": "两个旧条目分别是具体发文原件和馆藏索引，本条描述独立机构的业务受理规则。",
                        },
                    },
                    ensure_ascii=False,
                ),
                1,
            )
            self.assertIn("candidate", accepted)
            stored = json.loads(accepted["candidate"].raw_payload)
            self.assertEqual(
                stored["identity_resolution"]["reviewed_existing_ids"],
                [existing.id, related_existing.id],
            )
        finally:
            db.close()

    def test_character_state_replaces_current_fields_and_versions_are_descriptive(self):
        db = self.Session()
        try:
            project = Project(title="State Project")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第二章 吐纳",
                content="Mira三岁半，换上练功短衫，左臂仍有绷带，随后走到庭院。",
            )
            character = Character(
                project_id=project.id,
                name="Mira",
                appearance="三岁幼女，穿旧外袍。",
                age="三岁《第一章》：三岁半",
                current_location="Hall《第一章》：Courtyard",
                current_goal="Wait for orders",
            )
            db.add_all([chapter, character])
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            db.add(CatalogingCandidate(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                item_type="character_state_update",
                raw_payload=json.dumps({
                    "name": "Mira",
                    "appearance": "三岁半幼女，换上练功短衫，左臂仍有绷带。",
                    "appearance_before": "三岁幼女，穿旧外袍。",
                    "appearance_evidence": "Mira三岁半，换上练功短衫，左臂仍有绷带",
                    "age": "三岁半",
                    "age_before": "三岁《第一章》：三岁半",
                    "age_evidence": "Mira三岁半",
                    "current_location": "Courtyard",
                    "current_goal": "Learn breathing",
                }, ensure_ascii=False),
            ))
            db.commit()

            apply_candidates_for_run(db, job, run)

            self.assertEqual(character.age, "三岁半")
            self.assertEqual(character.appearance, "三岁半幼女，换上练功短衫，左臂仍有绷带。")
            self.assertEqual(character.current_location, "Courtyard")
            self.assertEqual(character.current_goal, "Learn breathing")
            version = (
                db.query(CharacterVersion)
                .filter(CharacterVersion.character_id == character.id)
                .order_by(CharacterVersion.version_number.desc())
                .first()
            )
            self.assertIn("外貌", version.change_summary)
            self.assertIn("年龄/时间状态", version.change_summary)
            self.assertIn("当前位置", version.change_summary)
            self.assertIn("当前目标", version.change_summary)
            self.assertNotIn("角色档案更新", version.change_summary)
        finally:
            db.close()

    def test_model_supplied_canonical_names_and_aliases_remain_separate_fields(self):
        db = self.Session()
        try:
            project = Project(title="Alias Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第二章 吐纳", content="糖糖在陆家见到爷爷。")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            for index, payload in enumerate([
                {"name": "特昂糖", "aliases": ["陆糖", "糖糖"], "role_type": "protagonist"},
                {"name": "特昂糖", "current_location": "陆家府邸"},
                {"name": "陆老爷子", "aliases": ["爷爷"], "role_type": "mentor",
                 "background": "陆家长辈，负责教导特昂糖吐纳。"},
            ]):
                db.add(CatalogingCandidate(
                    job_id=job.id,
                    chapter_run_id=run.id,
                    project_id=project.id,
                    chapter_id=chapter.id,
                    item_type="character_state_update" if payload.get("current_location") else "character_create",
                    raw_payload=json.dumps(payload, ensure_ascii=False),
                    sort_order=index,
                ))
            db.commit()

            apply_candidates_for_run(db, job, run)

            characters = db.query(Character).order_by(Character.name.asc()).all()
            self.assertEqual(len(characters), 2)
            sugar = next(item for item in characters if item.name == "特昂糖")
            elder = next(item for item in characters if item.name == "陆老爷子")
            self.assertEqual(sugar.current_location, "陆家府邸")
            sugar_aliases = [item.alias for item in db.query(CharacterAlias).filter(CharacterAlias.character_id == sugar.id).all()]
            elder_aliases = [item.alias for item in db.query(CharacterAlias).filter(CharacterAlias.character_id == elder.id).all()]
            self.assertIn("陆糖", sugar_aliases)
            self.assertNotIn("特昂糖/陆糖", sugar_aliases)
            self.assertIn("糖糖", sugar_aliases)
            self.assertIn("爷爷", elder_aliases)
        finally:
            db.close()

    def test_character_merge_candidate_marks_alias_and_merges_background(self):
        db = self.Session()
        try:
            project = Project(title="Merge Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Reveal", content="Black Cloak is the Master.")
            primary = Character(project_id=project.id, name="Master", background="Controls the hidden net.")
            secondary = Character(project_id=project.id, name="Black Cloak", background="Met the rebels in disguise.")
            db.add_all([chapter, primary, secondary])
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            db.add(CatalogingCandidate(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                item_type="character_merge_candidate",
                raw_payload=json.dumps({
                    "primary_name": "Master",
                    "secondary_name": "Black Cloak",
                    "canonical_name": "Master",
                    "aliases": ["Black Cloak", "the voice behind the net"],
                    "confidence_reason": "Both command the same rebel contact.",
                    "background_append": "以黑袍人身份接触叛徒，随后暴露为幕后主使。",
                }, ensure_ascii=False),
            ))
            db.commit()

            events = apply_candidates_for_run(db, job, run)

            self.assertEqual(events[0]["type"], "candidate_applied")
            self.assertIn("Met the rebels", primary.background)
            self.assertIn("黑袍人身份", primary.background)
            self.assertEqual(secondary.role_type, "merged_alias")
            self.assertIn("合并到", secondary.background)
            aliases = db.query(CharacterAlias).filter(CharacterAlias.character_id == primary.id).all()
            self.assertIn("Black Cloak", [alias.alias for alias in aliases])
        finally:
            db.close()

    def test_manual_character_merge_preview_and_apply_moves_links(self):
        db = self.Session()
        try:
            project = Project(title="Manual Merge Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第三章", content="爷爷就是陆老爷子。")
            primary = Character(
                project_id=project.id,
                name="陆老爷子",
                role_type="mentor",
                background="陆家长辈。",
                appearance="白发老者。",
                personality="沉稳。",
            )
            secondary = Character(
                project_id=project.id,
                name="爷爷",
                role_type="mentor",
                background="教导特昂糖吐纳。",
                appearance="坐在太师椅上的老人。",
                personality="慈祥。",
            )
            db.add_all([chapter, primary, secondary])
            db.flush()
            from app.database.models import ChapterCharacter, CharacterRelationship, CharacterTimeline
            db.add_all([
                CharacterAlias(project_id=project.id, character_id=primary.id, alias="爷爷", alias_type="alias", description="旧称呼"),
                ChapterCharacter(chapter_id=chapter.id, character_id=secondary.id, appearance_type="出场", description="爷爷教导糖糖"),
                CharacterTimeline(character_id=secondary.id, chapter_id=chapter.id, event_description="决定教特昂糖吐纳", event_type="decision"),
                CharacterRelationship(project_id=project.id, character_a_id=secondary.id, character_b_id=primary.id, relationship_type="同一人", description="称呼不同"),
            ])
            db.commit()

            duplicates = find_duplicate_character_candidates(db, project.id)
            self.assertTrue(any(item["primary"]["id"] == primary.id and item["secondary"]["id"] == secondary.id for item in duplicates))

            preview = build_character_merge_preview(db, project.id, primary.id, secondary.id, {"aliases": ["爷爷"]})
            self.assertEqual(preview["stats"]["secondary_chapter_appearances"], 1)
            self.assertIn("爷爷", preview["aliases"])
            self.assertIn("手动合并", preview["merged_preview"]["appearance"])

            merge_characters(db, project.id, primary.id, secondary.id, {"aliases": ["爷爷"], "confidence_reason": "同一人物不同称呼"})
            db.commit()

            self.assertEqual(secondary.role_type, "merged_alias")
            self.assertEqual(db.query(ChapterCharacter).filter(ChapterCharacter.character_id == secondary.id).count(), 0)
            self.assertEqual(db.query(ChapterCharacter).filter(ChapterCharacter.character_id == primary.id).count(), 1)
            self.assertEqual(db.query(CharacterTimeline).filter(CharacterTimeline.character_id == primary.id).count(), 1)
            self.assertEqual(db.query(CharacterRelationship).filter(CharacterRelationship.character_a_id == secondary.id).count(), 0)
            aliases = [item.alias for item in db.query(CharacterAlias).filter(CharacterAlias.character_id == primary.id).all()]
            self.assertIn("爷爷", aliases)
        finally:
            db.close()

    def test_extract_run_uses_fact_stage_then_candidate_stage(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(messages, **kwargs):
            calls.append(messages[0]["content"])
            if len(calls) == 1:
                body = "\n".join([
                    json.dumps({
                        "fact_type": "chapter_overview",
                        "payload": {"summary": "A hidden identity is revealed."},
                    }, ensure_ascii=False),
                    json.dumps({
                        "fact_type": "identity_hint",
                        "payload": {
                            "names": ["Master", "Black Cloak"],
                            "reason": "same contact",
                            "evidence_points": ["same signal"],
                        },
                    }, ensure_ascii=False),
                ]) + "\n"
            else:
                body = "\n".join([
                    json.dumps({
                        "type": "chapter_summary",
                        "payload": complete_summary_payload(
                            "A hidden identity is revealed.",
                            characters=["Master", "Black Cloak"],
                            key_events=["reveal"],
                        ),
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "outline_create",
                        "payload": {"title": "Reveal", "node_type": "chapter", "summary": "Identity reveal."},
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "character_merge_candidate",
                        "payload": {
                            "primary_name": "Master",
                            "secondary_name": "Black Cloak",
                            "confidence_reason": "same contact",
                            "evidence_points": ["same signal"],
                        },
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "character_state_update",
                        "payload": {"name": "Master", "life_status": "alive"},
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "character_state_update",
                        "payload": {"name": "Black Cloak", "life_status": "alive"},
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "chapter_link",
                        "payload": {"character_names": ["Master", "Black Cloak"]},
                    }, ensure_ascii=False),
                ]) + "\n"
            yield body[:40]
            yield body[40:]

        try:
            project = Project(title="Staged Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Reveal", content="Master and Black Cloak use the same signal.")
            db.add_all([
                chapter,
                Character(project_id=project.id, name="Master"),
                Character(project_id=project.id, name="Black Cloak"),
            ])
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", "deepseek:test", [])
            run = job.chapter_runs[0]
            cataloging_orchestrator.LLMGateway.stream_chat_completion = fake_stream

            async def collect():
                return [event async for event in cataloging_orchestrator._extract_run(db, job, run)]

            events = asyncio.run(collect())

            self.assertEqual(len(calls), 2)
            self.assertTrue(any('"type":"fact_extracted"' in event for event in events))
            self.assertEqual(db.query(CatalogingFact).count(), 2)
            self.assertEqual(db.query(CatalogingCandidate).count(), 6)
            self.assertEqual(run.status, "awaiting_confirmation")
        finally:
            cataloging_orchestrator.LLMGateway.stream_chat_completion = original_stream
            db.close()

    def test_extract_run_local_runtime_uses_the_same_staged_pipeline(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(messages, **kwargs):
            calls.append((messages, kwargs))
            if len(calls) == 1:
                body = json.dumps({
                    "fact_type": "chapter_overview",
                    "payload": {"summary": "The local model should use staged facts."},
                }, ensure_ascii=False) + "\n"
            else:
                body = "\n".join([
                    json.dumps({
                        "type": "chapter_summary",
                        "payload": complete_summary_payload(
                            "The local model resolved compact candidates.",
                            key_events=["ok"],
                        ),
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "outline_create",
                        "payload": {"title": "Local Door", "node_type": "chapter", "summary": "The door opens."},
                    }, ensure_ascii=False),
                ]) + "\n"
            yield body

        try:
            project = Project(title="Local Runtime Cataloging Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Local Door", content="The door opens locally.")
            db.add_all([
                chapter,
                Character(project_id=project.id, name="Lin", background="A very long background." * 80),
            ])
            db.commit()
            job = create_cataloging_job(
                db,
                project.id,
                "manual",
                "local_llama_cpp:qwen3-14b-q4",
                [],
            )
            run = job.chapter_runs[0]
            cataloging_orchestrator.LLMGateway.stream_chat_completion = fake_stream

            async def collect():
                return [event async for event in cataloging_orchestrator._extract_run(db, job, run)]

            events = asyncio.run(collect())

            self.assertEqual(len(calls), 2)
            self.assertIn("事实抽取器", calls[0][0][0]["content"])
            self.assertIn("第二阶段决策器", calls[1][0][0]["content"])
            self.assertNotIn("single_stage_cataloging", calls[1][0][1]["content"])
            self.assertEqual(calls[1][1]["max_tokens"], 4096)
            self.assertEqual(db.query(CatalogingFact).count(), 1)
            self.assertEqual(db.query(CatalogingCandidate).count(), 2)
            self.assertTrue(any('"type":"chapter_extracted"' in event for event in events))
            self.assertEqual(run.status, "awaiting_confirmation")
        finally:
            cataloging_orchestrator.LLMGateway.stream_chat_completion = original_stream
            db.close()

    def test_extract_run_retries_fact_stage_without_duplicate_facts(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(messages, **kwargs):
            calls.append(messages[0]["content"])
            if len(calls) == 1:
                body = json.dumps({
                    "fact_type": "chapter_overview",
                    "payload": {"summary": "partial"},
                }, ensure_ascii=False) + "\n"
                yield body
                raise RuntimeError("peer closed connection without sending complete message body")
            if len(calls) == 2:
                body = json.dumps({
                    "fact_type": "chapter_overview",
                    "payload": {"summary": "final"},
                }, ensure_ascii=False) + "\n"
            else:
                body = "\n".join([
                    json.dumps({
                        "type": "chapter_summary",
                        "payload": complete_summary_payload("final", key_events=["ok"]),
                    }, ensure_ascii=False),
                    json.dumps({
                        "type": "outline_create",
                        "payload": {"title": "Retry", "node_type": "chapter", "summary": "final"},
                    }, ensure_ascii=False),
                ]) + "\n"
            yield body

        try:
            project = Project(title="Fact Retry Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Retry", content="Retry content.")
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", None, [])
            run = job.chapter_runs[0]
            cataloging_orchestrator.LLMGateway.stream_chat_completion = fake_stream

            async def collect():
                return [event async for event in cataloging_orchestrator._extract_run(db, job, run)]

            events = asyncio.run(collect())

            self.assertEqual(len(calls), 3)
            self.assertTrue(any('"type":"cataloging_retry"' in event and '"stage":"fact_extraction"' in event for event in events))
            self.assertEqual(db.query(CatalogingFact).count(), 1)
            fact = db.query(CatalogingFact).first()
            self.assertIn("final", fact.raw_payload)
            self.assertEqual(run.status, "awaiting_confirmation")
        finally:
            cataloging_orchestrator.LLMGateway.stream_chat_completion = original_stream
            db.close()

    def test_extract_run_retries_candidate_stage_and_clears_partial_candidates(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(messages, **kwargs):
            calls.append(messages[0]["content"])
            if len(calls) == 1:
                body = json.dumps({
                    "fact_type": "chapter_overview",
                    "payload": {"summary": "candidate retry"},
                }, ensure_ascii=False) + "\n"
                yield body
                return
            if len(calls) == 2:
                body = json.dumps({
                    "type": "chapter_summary",
                    "payload": {"summary_text": "partial", "key_events": ["partial"]},
                }, ensure_ascii=False) + "\n"
                yield body
                raise RuntimeError("incomplete chunked read")
            body = "\n".join([
                json.dumps({
                    "type": "chapter_summary",
                    "payload": complete_summary_payload("final", key_events=["ok"]),
                }, ensure_ascii=False),
                json.dumps({
                    "type": "outline_create",
                    "payload": {"title": "Retry", "node_type": "chapter", "summary": "final"},
                }, ensure_ascii=False),
            ]) + "\n"
            yield body

        try:
            project = Project(title="Candidate Retry Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Retry", content="Retry content.")
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", None, [])
            run = job.chapter_runs[0]
            cataloging_orchestrator.LLMGateway.stream_chat_completion = fake_stream

            async def collect():
                return [event async for event in cataloging_orchestrator._extract_run(db, job, run)]

            events = asyncio.run(collect())

            self.assertEqual(len(calls), 3)
            self.assertTrue(any('"type":"cataloging_retry"' in event and '"stage":"candidate_resolution"' in event for event in events))
            self.assertEqual(db.query(CatalogingFact).count(), 1)
            self.assertEqual(db.query(CatalogingCandidate).count(), 2)
            summaries = db.query(CatalogingCandidate).filter(CatalogingCandidate.item_type == "chapter_summary").all()
            self.assertEqual(len(summaries), 1)
            self.assertIn("final", summaries[0].raw_payload)
            self.assertEqual(run.status, "awaiting_confirmation")
        finally:
            cataloging_orchestrator.LLMGateway.stream_chat_completion = original_stream
            db.close()

    def test_local_runtime_fact_prompt_inlines_chapter_content(self):
        messages = cataloging_orchestrator._fact_prompt_messages(
            chapter_title="第1章 开端",
            chapter_content="张三来到青云宗，发现灵石账目异常。",
            chapter_file=r"D:\novels\chapter.md",
            model="local_llama_cpp:qwen3-4b-q4",
        )

        self.assertIn("张三来到青云宗", messages[1]["content"])
        self.assertNotIn("镜像文件", messages[1]["content"])
        self.assertLess(len(messages[0]["content"]), 600)

    def test_extract_run_local_runtime_pauses_when_fact_stage_is_empty(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(messages, **kwargs):
            calls.append(messages[0]["content"])
            yield "我无法抽取。"

        try:
            project = Project(title="Local Runtime Failure Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第1章 开端", content="张三来到青云宗，发现灵石账目异常。")
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", "local_llama_cpp:qwen3-4b-q4", [])
            run = job.chapter_runs[0]
            cataloging_orchestrator.LLMGateway.stream_chat_completion = fake_stream

            async def collect():
                return [event async for event in cataloging_orchestrator._extract_run(db, job, run)]

            events = asyncio.run(collect())

            self.assertEqual(len(calls), CATALOGING_STAGE_MAX_ATTEMPTS)
            self.assertTrue(any('"type":"cataloging_retry"' in event and '"stage":"fact_extraction"' in event for event in events))
            self.assertEqual(db.query(CatalogingFact).count(), 0)
            self.assertEqual(db.query(CatalogingCandidate).count(), 0)
            self.assertEqual(run.status, "failed")
            self.assertIn("模型未输出可用事实", run.error)
        finally:
            cataloging_orchestrator.LLMGateway.stream_chat_completion = original_stream
            db.close()

    def test_extract_run_local_runtime_pauses_instead_of_template_candidate_fallback(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(messages, **kwargs):
            calls.append(messages[0]["content"])
            if len(calls) == 1:
                body = json.dumps({
                    "fact_type": "chapter_overview",
                    "payload": {"summary": "张三来到青云宗。"},
                }, ensure_ascii=False) + "\n"
            else:
                body = "我会稍后整理候选。\n"
            yield body

        try:
            project = Project(title="Local Candidate Pause Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第1章 开端", content="张三来到青云宗。")
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", "local_llama_cpp:qwen3-4b-q4", [])
            run = job.chapter_runs[0]
            cataloging_orchestrator.LLMGateway.stream_chat_completion = fake_stream

            async def collect():
                return [event async for event in cataloging_orchestrator._extract_run(db, job, run)]

            events = asyncio.run(collect())

            self.assertEqual(len(calls), 4)
            self.assertTrue(any("不会用模板生成候选" in event for event in events))
            self.assertEqual(db.query(CatalogingCandidate).count(), 0)
            self.assertEqual(run.status, "failed")
            self.assertIn("JSONL", run.error)
        finally:
            cataloging_orchestrator.LLMGateway.stream_chat_completion = original_stream
            db.close()

    def test_skip_and_cancel_update_job_state(self):
        db = self.Session()
        try:
            project = Project(title="Control Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Control Chapter", content="content")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "manual", None, [])
            run = job.chapter_runs[0]
            run.status = "awaiting_confirmation"
            job.status = "waiting_confirmation"
            job.blocked_chapter_id = run.chapter_id
            db.commit()

            mark_run_skipped(db, job, first_blocking_run(db, job))
            db.commit()

            self.assertEqual(run.status, "skipped_by_user")
            self.assertEqual(job.status, "running")
            self.assertIsNone(job.blocked_chapter_id)
            self.assertEqual(job.context_integrity, "skipped_chapter")

            cancel_job(job)
            db.commit()

            self.assertEqual(job.status, "cancelled")
            self.assertIsNone(job.current_chapter_id)
            self.assertIsNone(job.blocked_chapter_id)
            self.assertIsNotNone(job.completed_at)
        finally:
            db.close()

    def test_manual_repair_can_recover_failed_run_for_review(self):
        db = self.Session()
        try:
            project = Project(title="Repair Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Repair Chapter", content="content")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "manual", None, [])
            run = job.chapter_runs[0]
            run.status = "failed"
            run.error = "bad jsonl"
            job.status = "paused_on_failure"
            job.blocked_chapter_id = run.chapter_id
            db.commit()

            self.assertFalse(has_usable_chapter_summary(db, run))
            create_manual_candidate(
                db,
                job,
                run,
                "chapter_summary",
                {"narrative_state": {"events": [{"title": "ledger only"}]}},
                "edited",
            )
            self.assertFalse(has_usable_chapter_summary(db, run))
            create_manual_candidate(
                db,
                job,
                run,
                "chapter_summary",
                {"summary_text": "manual summary", "key_events": ["fixed"]},
                "edited",
            )
            self.assertTrue(has_usable_chapter_summary(db, run))

            recover_failed_run_for_review(db, job, run)
            db.commit()

            self.assertEqual(run.status, "awaiting_confirmation")
            self.assertIsNone(run.error)
            self.assertEqual(job.status, "waiting_confirmation")
            self.assertEqual(job.blocked_chapter_id, run.chapter_id)
        finally:
            db.close()

    def test_recover_current_endpoint_reparses_raw_output_before_review(self):
        db = self.Session()
        try:
            project = Project(title="Endpoint Recovery Project")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第一章 回收",
                content="主角发现了旧档案。",
            )
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", "deepseek:test", [])
            run = job.chapter_runs[0]
            run.status = "failed"
            run.error = "候选覆盖不完整，缺少 chapter-level outline"
            run.raw_output = "=== CANDIDATE RESOLUTION ===\n" + json.dumps({
                "type": "chapter_summary",
                **complete_summary_payload(
                    "主角发现了旧档案。",
                    narrative_state={"events": ["发现旧档案"]},
                ),
                "outline_creates": [{
                    "type": "outline_create",
                    "node_type": "chapter",
                    "title": chapter.title,
                    "summary": "主角发现了旧档案。",
                }],
            }, ensure_ascii=False, indent=2)
            job.status = "paused_on_failure"
            job.blocked_chapter_id = run.chapter_id
            db.commit()

            response = recover_current_cataloging_chapter(project.id, job.id, db)
            candidates = db.query(CatalogingCandidate).filter(
                CatalogingCandidate.chapter_run_id == run.id,
            ).all()

            self.assertEqual(run.status, "awaiting_confirmation")
            self.assertEqual(job.status, "waiting_confirmation")
            self.assertTrue(inspect_candidate_coverage(candidates).is_complete)
            self.assertEqual(response.data["recovered_candidates"], 2)
        finally:
            db.close()

    def test_recover_current_endpoint_keeps_incomplete_raw_output_failed(self):
        db = self.Session()
        try:
            project = Project(title="Incomplete Endpoint Recovery Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第一章", content="只有摘要。")
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", "deepseek:test", [])
            run = job.chapter_runs[0]
            run.status = "failed"
            run.raw_output = "=== CANDIDATE RESOLUTION ===\n" + json.dumps({
                "type": "chapter_summary",
                "summary_text": "只有摘要。",
            }, ensure_ascii=False)
            job.status = "paused_on_failure"
            job.blocked_chapter_id = run.chapter_id
            db.commit()

            with self.assertRaises(ValidationError) as raised:
                recover_current_cataloging_chapter(project.id, job.id, db)

            self.assertIn("chapter-level outline", str(raised.exception))
            self.assertIn("coverage declaration", str(raised.exception))
            self.assertEqual(run.status, "failed")
            self.assertEqual(
                db.query(CatalogingCandidate).filter(
                    CatalogingCandidate.chapter_run_id == run.id,
                ).count(),
                0,
            )
        finally:
            db.close()

    def test_refresh_job_progress_flushes_pending_run_status(self):
        db = self.Session()
        try:
            project = Project(title="Progress Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Progress Chapter", content="content")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "manual", None, [])
            run = job.chapter_runs[0]
            run.status = "completed"

            refresh_job_progress(db, job)

            self.assertEqual(job.completed_chapters, 1)
        finally:
            db.close()

    def test_pause_and_resume_job(self):
        db = self.Session()
        try:
            project = Project(title="Pause Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="Pause Chapter", content="content")
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            pause_job(job)
            self.assertEqual(job.status, "paused")
            resume_job(job)
            self.assertEqual(job.status, "running")
            self.assertIsNone(job.error)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
