"""Regression tests for the project cataloging service layer."""

import asyncio
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    CatalogingCandidate,
    CatalogingFact,
    Chapter,
    Character,
    CharacterAIConfig,
    CharacterAlias,
    CharacterRelationship,
    CharacterVersion,
    OutlineNode,
    Project,
    WorldbuildingEntry,
)
from app.services.cataloging.applier import apply_candidates_for_run
from app.services.cataloging.candidate_store import try_create_candidate
from app.services.cataloging.context import build_light_context
from app.services.context_builders import _build_world_context
from app.services.cataloging.job_control import (
    cancel_job,
    first_blocking_run,
    mark_run_skipped,
    pause_job,
    refresh_job_progress,
    reset_run_for_retry,
    resume_job,
)
from app.services.cataloging.manual_ops import create_manual_candidate, has_usable_chapter_summary, recover_failed_run_for_review
from app.services.cataloging.orchestrator import create_cataloging_job
from app.services.cataloging import orchestrator as cataloging_orchestrator
from app.services.cataloging.background_compactor import merge_background
from app.services.cataloging.worldbuilding_ops import _normalize_dimension
from app.services.character_merge_service import build_character_merge_preview, find_duplicate_character_candidates, merge_characters


class CatalogingServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

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
            db.add(chapter)
            db.commit()

            job = create_cataloging_job(db, project.id, "auto", None, [])
            run = job.chapter_runs[0]
            for item_type, payload in [
                ("chapter_summary", {"summary_text": "张三来到青云宗。", "key_events": ["张三抵达青云宗"]}),
                ("outline_create", {"title": "第1章 开端", "node_type": "chapter", "summary": "张三来到青云宗。", "related_characters": ["张三"]}),
                ("outline_create", {"title": "第1章 开端-场景1 入宗门", "node_type": "section", "parent_title": "第1章 开端", "summary": "张三进入青云宗山门。", "related_characters": ["张三"]}),
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
            self.assertEqual(db.query(OutlineNode).count(), 2)
            section = db.query(OutlineNode).filter(OutlineNode.node_type == "section").first()
            self.assertIsNotNone(section.parent_id)
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

    def test_character_state_replaces_current_fields_and_versions_are_descriptive(self):
        db = self.Session()
        try:
            project = Project(title="State Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第二章 吐纳", content="Mira moves to the courtyard.")
            character = Character(
                project_id=project.id,
                name="Mira",
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
                    "age": "三岁半",
                    "current_location": "Courtyard",
                    "current_goal": "Learn breathing",
                }, ensure_ascii=False),
            ))
            db.commit()

            apply_candidates_for_run(db, job, run)

            self.assertEqual(character.age, "三岁半")
            self.assertEqual(character.current_location, "Courtyard")
            self.assertEqual(character.current_goal, "Learn breathing")
            version = (
                db.query(CharacterVersion)
                .filter(CharacterVersion.character_id == character.id)
                .order_by(CharacterVersion.version_number.desc())
                .first()
            )
            self.assertIn("年龄/时间状态", version.change_summary)
            self.assertIn("当前位置", version.change_summary)
            self.assertIn("当前目标", version.change_summary)
            self.assertNotIn("角色档案更新", version.change_summary)
        finally:
            db.close()

    def test_character_aliases_prevent_duplicate_cards(self):
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
                {"name": "特昂糖/陆糖", "aliases": ["糖糖"], "role_type": "protagonist"},
                {"name": "陆糖", "current_location": "陆家府邸"},
                {"name": "爷爷", "role_type": "mentor"},
                {"name": "陆老爷子", "background": "陆家长辈，负责教导特昂糖吐纳。"},
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
            self.assertIn("特昂糖/陆糖", sugar_aliases)
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

        async def fake_stream(cls, messages, **kwargs):
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
                        "payload": {"summary_text": "A hidden identity is revealed.", "key_events": ["reveal"]},
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
            job = create_cataloging_job(db, project.id, "manual", None, [])
            run = job.chapter_runs[0]
            cataloging_orchestrator.LLMGateway.stream_chat_completion = classmethod(fake_stream)

            async def collect():
                return [event async for event in cataloging_orchestrator._extract_run(db, job, run)]

            events = asyncio.run(collect())

            self.assertEqual(len(calls), 2)
            self.assertTrue(any('"type":"fact_extracted"' in event for event in events))
            self.assertEqual(db.query(CatalogingFact).count(), 2)
            self.assertEqual(db.query(CatalogingCandidate).count(), 2)
            self.assertEqual(run.status, "awaiting_confirmation")
        finally:
            cataloging_orchestrator.LLMGateway.stream_chat_completion = original_stream
            db.close()

    def test_extract_run_retries_fact_stage_without_duplicate_facts(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(cls, messages, **kwargs):
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
                body = json.dumps({
                    "type": "chapter_summary",
                    "payload": {"summary_text": "final", "key_events": ["ok"]},
                }, ensure_ascii=False) + "\n"
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
            cataloging_orchestrator.LLMGateway.stream_chat_completion = classmethod(fake_stream)

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

        async def fake_stream(cls, messages, **kwargs):
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
                    "payload": {"summary_text": "final", "key_events": ["ok"]},
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
            cataloging_orchestrator.LLMGateway.stream_chat_completion = classmethod(fake_stream)

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

    def test_extract_run_local_runtime_falls_back_when_fact_stage_empty(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(cls, messages, **kwargs):
            calls.append(messages[0]["content"])
            if len(calls) <= 3:
                yield "我无法抽取。"
                return
            body = json.dumps({
                "type": "chapter_summary",
                "payload": {"summary_text": "fallback facts reached candidate stage", "key_events": ["ok"]},
            }, ensure_ascii=False) + "\n"
            yield body

        try:
            project = Project(title="Local Runtime Fallback Project")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第1章 开端", content="张三来到青云宗，发现灵石账目异常。")
            db.add(chapter)
            db.commit()
            job = create_cataloging_job(db, project.id, "manual", "local_llama_cpp:qwen3-4b-q4", [])
            run = job.chapter_runs[0]
            cataloging_orchestrator.LLMGateway.stream_chat_completion = classmethod(fake_stream)

            async def collect():
                return [event async for event in cataloging_orchestrator._extract_run(db, job, run)]

            events = asyncio.run(collect())

            self.assertEqual(len(calls), 4)
            self.assertTrue(any('"type":"cataloging_warning"' in event and '"stage":"fact_extraction"' in event for event in events))
            self.assertGreaterEqual(db.query(CatalogingFact).count(), 2)
            self.assertEqual(db.query(CatalogingCandidate).count(), 1)
            self.assertEqual(run.status, "awaiting_confirmation")
        finally:
            cataloging_orchestrator.LLMGateway.stream_chat_completion = original_stream
            db.close()

    def test_extract_run_local_runtime_pauses_instead_of_template_candidate_fallback(self):
        db = self.Session()
        original_stream = cataloging_orchestrator.LLMGateway.stream_chat_completion
        calls = []

        async def fake_stream(cls, messages, **kwargs):
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
            cataloging_orchestrator.LLMGateway.stream_chat_completion = classmethod(fake_stream)

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
