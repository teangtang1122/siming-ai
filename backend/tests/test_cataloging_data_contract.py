"""End-to-end checks for cataloging character and source coverage."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    CatalogingCandidate,
    CatalogingFact,
    Chapter,
    Character,
    CharacterAlias,
    CharacterRelationship,
    CharacterTimeline,
    Project,
    WorldbuildingEntry,
    WorldbuildingTimeline,
)
from app.services.cataloging.candidate_validation import (
    candidate_coverage_review_message,
    inspect_candidate_coverage,
)
from app.services.cataloging.character_ops import (
    apply_character_create,
    apply_character_relationship,
)
from app.services.cataloging.context import _worldbuilding_detail
from app.services.cataloging.orchestrator import (
    _compact_local_runtime_context,
    create_cataloging_job,
)
from app.services.cataloging.snapshots import character_snapshot
from app.services.cataloging.targeted_context import (
    _character_context,
    _worldbuilding_context,
    build_targeted_context,
)


def database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def summary_payload(**manifest):
    return {
        "summary_text": "本章事实摘要。",
        "coverage_manifest": {
            "scene_count": 1,
            "characters": [],
            "worldbuilding": [],
            "relationships": [],
            "character_profiles": [],
            **manifest,
        },
        "narrative_state": {
            "events": [],
            "foreshadowing_planted": [],
            "foreshadowing_resolved": [],
            "storyline_progress": [],
            "unresolved_actions": [],
        },
        "narrative_review": {"source": "provided", "outcome": "assessed"},
    }


def candidate(db, job, run, chapter, item_type, payload, sort_order=0):
    row = CatalogingCandidate(
        job_id=job.id,
        chapter_run_id=run.id,
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        item_type=item_type,
        raw_payload=json.dumps(payload, ensure_ascii=False),
        sort_order=sort_order,
    )
    db.add(row)
    db.flush()
    return row


def test_pending_timeline_rows_are_safe_to_sort_in_cataloging_contexts():
    character = Character(
        id="character-1",
        project_id="project-1",
        name="Pending role",
        role_type="supporting",
    )
    character.timeline_events.extend(
        [
            CharacterTimeline(
                character_id=character.id,
                chapter_id=f"chapter-{index}",
                event_description=f"Pending event {index}",
                event_type="key_event",
            )
            for index in range(2)
        ]
    )
    world = WorldbuildingEntry(
        id="world-1",
        project_id="project-1",
        dimension="history",
        title="Pending history",
        content="Pending events.",
    )
    world.timeline_events.extend(
        [
            WorldbuildingTimeline(
                entry_id=world.id,
                chapter_id=f"chapter-{index}",
                event_description=f"Pending world event {index}",
                event_type="fact_change",
            )
            for index in range(2)
        ]
    )

    assert len(_character_context(character)["recent_timeline"]) == 2
    assert len(_worldbuilding_context(world)["recent_timeline"]) == 2
    assert len(_worldbuilding_detail(world)["recent_timeline"]) == 2


def test_fact_inventory_prevents_a_false_empty_manifest():
    engine, db = database()
    try:
        project = Project(id="project-1", title="事实核对")
        chapter = Chapter(id="chapter-1", project_id=project.id, title="第一章", content="张三推门而入。")
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        db.add(CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            fact_type="character_fact",
            raw_payload=json.dumps({"name": "张三"}, ensure_ascii=False),
            status="active",
        ))
        rows = [
            candidate(db, job, run, chapter, "chapter_summary", summary_payload()),
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter", "title": chapter.title, "summary": "张三入门。",
            }, 1),
        ]

        coverage = inspect_candidate_coverage(rows, db=db, project_id=project.id)

        assert coverage.is_complete is True
        assert any(
            item.startswith("source characters missing from coverage_manifest.characters")
            and "张三" in item
            for item in coverage.review_warnings
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_persistence_coverage_errors_are_presented_in_chinese():
    engine, db = database()
    try:
        project = Project(id="project-1", title="错误提示")
        chapter = Chapter(id="chapter-1", project_id=project.id, title="第一章", content="张三出现。")
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        db.add(CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            fact_type="character_fact",
            raw_payload=json.dumps({"name": "张三"}, ensure_ascii=False),
            status="active",
        ))
        rows = [
            candidate(db, job, run, chapter, "chapter_summary", summary_payload()),
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter", "title": chapter.title, "summary": "张三出现。",
            }, 1),
        ]

        message = candidate_coverage_review_message(
            inspect_candidate_coverage(rows, db=db, project_id=project.id)
        )

        assert "原文角色未进入章节覆盖清单：张三" in message
        assert "source characters missing" not in message
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_character_aliases_are_canonicalized_before_identity_coverage():
    engine, db = database()
    try:
        project = Project(id="project-1", title="别名核对")
        chapter = Chapter(id="chapter-1", project_id=project.id, title="第一章", content="爷爷示意众人落座。")
        character = Character(project_id=project.id, name="陆老爷子", background="陆家家主。")
        db.add_all([project, chapter, character])
        db.flush()
        db.add(CharacterAlias(project_id=project.id, character_id=character.id, alias="爷爷"))
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        rows = [
            candidate(db, job, run, chapter, "chapter_summary", summary_payload(characters=["爷爷"])),
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter", "title": chapter.title, "summary": "众人落座。",
            }, 1),
            candidate(db, job, run, chapter, "character_state_update", {
                "name": "陆老爷子", "current_location": "议事厅",
            }, 2),
            candidate(db, job, run, chapter, "chapter_link", {
                "character_names": ["爷爷"],
            }, 3),
        ]

        coverage = inspect_candidate_coverage(rows, db=db, project_id=project.id)

        assert coverage.is_complete is True
        assert coverage.declared_character_identities == ("陆老爷子",)
        assert coverage.character_state_identities == ("陆老爷子",)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_staged_aliases_and_folded_worldbuilding_terms_match_source_facts():
    """Reproduce the 特昂糖/陆糖 cataloging checkpoint false positive."""

    engine, db = database()
    try:
        project = Project(id="project-1", title="候选身份归一化")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章 穿越·着陆",
            content="特昂糖穿越到游戏世界，察觉灵气波动。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        fact_payloads = [
            (
                "chapter_overview",
                {
                    "characters": ["特昂糖/陆糖"],
                    "worldbuilding_titles": ["游戏世界", "灵气波动"],
                },
            ),
            (
                "character_fact",
                {
                    "names": ["特昂糖", "陆糖"],
                    "primary_name": "陆糖",
                    "aliases": ["特昂糖"],
                    "profile_clues": {"background": "异世穿越者"},
                },
            ),
            ("identity_hint", {"names": ["特昂糖", "陆糖"]}),
            (
                "worldbuilding_fact",
                {
                    "title_hint": "游戏世界设定",
                    "keywords": ["游戏世界", "NPC", "灵气波动"],
                },
            ),
        ]
        db.add_all([
            CatalogingFact(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                fact_type=fact_type,
                raw_payload=json.dumps(payload, ensure_ascii=False),
                status="active",
            )
            for fact_type, payload in fact_payloads
        ])
        rows = [
            candidate(
                db,
                job,
                run,
                chapter,
                "chapter_summary",
                summary_payload(
                    characters=["陆糖"],
                    worldbuilding=["游戏世界设定"],
                    character_profiles=["陆糖"],
                ),
            ),
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter",
                "title": chapter.title,
                "summary": "陆糖穿越并感知灵气。",
            }, 1),
            candidate(db, job, run, chapter, "character_create", {
                "name": "陆糖",
                "aliases": ["特昂糖", "糖糖"],
                "background": "异世穿越者。",
            }, 2),
            candidate(db, job, run, chapter, "character_state_update", {
                "name": "陆糖",
                "current_location": "陆家院子",
            }, 3),
            candidate(db, job, run, chapter, "worldbuilding_create", {
                "title": "游戏世界设定",
                "content": "这是游戏世界，灵气分布呈现规律性波动。",
                "keywords": ["游戏世界", "NPC", "灵气波动"],
            }, 4),
            candidate(db, job, run, chapter, "chapter_link", {
                "character_names": ["陆糖"],
                "worldbuilding_titles": ["游戏世界设定"],
            }, 5),
        ]

        coverage = inspect_candidate_coverage(rows, db=db, project_id=project.id)

        assert coverage.is_complete is True
        assert coverage.persistence_missing == ()
        assert coverage.declared_character_identities == ("陆糖",)
        assert coverage.declared_character_profile_identities == ("陆糖",)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_parenthetical_display_names_resolve_to_staged_character_cards():
    engine, db = database()
    try:
        project = Project(id="project-1", title="括号称呼归一化")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="特昂糖被爷爷抱到腿上。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        rows = [
            candidate(
                db,
                job,
                run,
                chapter,
                "chapter_summary",
                summary_payload(
                    characters=["特昂糖（陆糖）", "爷爷（陆家老爷子）"],
                    relationships=[{
                        "source_name": "陆糖",
                        "target_name": "爷爷",
                        "relationship_type": "祖孙",
                    }],
                    character_profiles=["特昂糖（陆糖）", "爷爷（陆家老爷子）"],
                ),
            ),
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter", "title": chapter.title, "summary": "祖孙交谈。",
            }, 1),
            candidate(db, job, run, chapter, "character_create", {
                "name": "特昂糖", "aliases": ["陆糖"], "background": "穿越者。",
            }, 2),
            candidate(db, job, run, chapter, "character_create", {
                "name": "爷爷", "aliases": ["陆家老爷子"], "background": "陆家掌权者。",
            }, 3),
            candidate(db, job, run, chapter, "character_state_update", {
                "name": "特昂糖", "current_location": "议事厅",
            }, 4),
            candidate(db, job, run, chapter, "character_state_update", {
                "name": "爷爷", "current_location": "议事厅",
            }, 5),
            candidate(db, job, run, chapter, "character_relationship", {
                "source_name": "陆糖", "target_name": "爷爷", "relationship_type": "祖孙",
            }, 6),
            candidate(db, job, run, chapter, "chapter_link", {
                "character_names": ["特昂糖（陆糖）", "爷爷（陆家老爷子）"],
            }, 7),
        ]

        coverage = inspect_candidate_coverage(rows, db=db, project_id=project.id)

        assert coverage.is_complete is True
        assert coverage.declared_character_identities == ("爷爷", "特昂糖")
        assert coverage.character_state_identities == ("爷爷", "特昂糖")
        assert coverage.declared_character_profile_identities == ("爷爷", "特昂糖")
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_folded_worldbuilding_term_requires_explicit_candidate_evidence():
    engine, db = database()
    try:
        project = Project(id="project-1", title="设定证据核对")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="夜色渐深，她察觉游戏世界里的灵气波动。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        db.add(CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            fact_type="chapter_overview",
            raw_payload=json.dumps({
                "worldbuilding_titles": ["游戏世界", "灵气波动"],
            }, ensure_ascii=False),
            status="active",
        ))
        rows = [
            candidate(
                db,
                job,
                run,
                chapter,
                "chapter_summary",
                summary_payload(worldbuilding=["游戏世界设定"]),
            ),
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter",
                "title": chapter.title,
                "summary": "进入异世。",
            }, 1),
            candidate(db, job, run, chapter, "worldbuilding_create", {
                "title": "游戏世界设定",
                "content": "NPC遵循固定行为规则。",
            }, 2),
            candidate(db, job, run, chapter, "chapter_link", {
                "worldbuilding_titles": ["游戏世界设定"],
            }, 3),
        ]

        coverage = inspect_candidate_coverage(rows, db=db, project_id=project.id)

        assert coverage.is_complete is True
        assert any(
            item.startswith("source worldbuilding missing from coverage_manifest.worldbuilding")
            and "灵气波动" in item
            for item in coverage.review_warnings
        )
        assert all("游戏世界、" not in item for item in coverage.review_warnings)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_conflicting_staged_aliases_are_not_silently_merged():
    engine, db = database()
    try:
        project = Project(id="project-1", title="别名冲突核对")
        chapter = Chapter(id="chapter-1", project_id=project.id, title="第一章", content="阿糖回头。")
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        db.add(CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            fact_type="character_fact",
            raw_payload=json.dumps({"name": "阿糖"}, ensure_ascii=False),
            status="active",
        ))
        rows = [
            candidate(
                db,
                job,
                run,
                chapter,
                "chapter_summary",
                summary_payload(characters=["甲", "乙"], character_profiles=["甲", "乙"]),
            ),
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter", "title": chapter.title, "summary": "两人现身。",
            }, 1),
            candidate(db, job, run, chapter, "character_create", {
                "name": "甲", "aliases": ["阿糖"], "background": "甲的档案。",
            }, 2),
            candidate(db, job, run, chapter, "character_create", {
                "name": "乙", "aliases": ["阿糖"], "background": "乙的档案。",
            }, 3),
            candidate(db, job, run, chapter, "character_state_update", {
                "name": "甲", "current_location": "院中",
            }, 4),
            candidate(db, job, run, chapter, "character_state_update", {
                "name": "乙", "current_location": "院中",
            }, 5),
            candidate(db, job, run, chapter, "chapter_link", {
                "character_names": ["甲", "乙"],
            }, 6),
        ]

        coverage = inspect_candidate_coverage(rows, db=db, project_id=project.id)

        assert coverage.is_complete is True
        assert any(
            item.startswith("source characters missing from coverage_manifest.characters")
            and "阿糖" in item
            for item in coverage.review_warnings
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_relationship_fact_must_be_declared_and_written():
    engine, db = database()
    try:
        project = Project(id="project-1", title="关系闭环")
        chapter = Chapter(id="chapter-1", project_id=project.id, title="第一章", content="甲正式拜乙为师。")
        db.add_all([
            project,
            chapter,
            Character(project_id=project.id, name="甲", background="求道者。"),
            Character(project_id=project.id, name="乙", background="山门长老。"),
        ])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        db.add(CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            fact_type="relationship_fact",
            raw_payload=json.dumps({
                "source_name": "甲",
                "target_name": "乙",
                "relationship_type": "师徒",
            }, ensure_ascii=False),
            status="active",
        ))
        db.flush()
        summary = candidate(
            db,
            job,
            run,
            chapter,
            "chapter_summary",
            summary_payload(characters=["甲", "乙"]),
        )
        rows = [
            summary,
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter", "title": chapter.title, "summary": "甲拜师。",
            }, 1),
            candidate(db, job, run, chapter, "character_state_update", {"name": "甲", "current_location": "山门"}, 2),
            candidate(db, job, run, chapter, "character_state_update", {"name": "乙", "current_location": "山门"}, 3),
            candidate(db, job, run, chapter, "chapter_link", {"character_names": ["甲", "乙"]}, 4),
        ]

        incomplete = inspect_candidate_coverage(rows, db=db, project_id=project.id)
        assert incomplete.is_complete is True
        assert any(
            item.startswith("source relationships missing from coverage_manifest.relationships")
            for item in incomplete.review_warnings
        )

        payload = summary_payload(
            characters=["甲", "乙"],
            relationships=[{
                "source_name": "甲",
                "target_name": "乙",
                "relationship_type": "师徒",
            }],
        )
        summary.edited_payload = json.dumps(payload, ensure_ascii=False)
        rows.append(candidate(db, job, run, chapter, "character_relationship", {
            "source_name": "甲",
            "target_name": "乙",
            "relationship_type": "师徒",
            "description": "甲在本章正式拜乙为师。",
        }, 5))

        complete = inspect_candidate_coverage(rows, db=db, project_id=project.id)
        assert complete.is_complete is True
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_stable_character_fact_must_update_character_profile():
    engine, db = database()
    try:
        project = Project(id="project-1", title="角色画像闭环")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="姜尘发誓绝不拿无辜者作饵，说话仍旧简短。",
        )
        db.add_all([
            project,
            chapter,
            Character(project_id=project.id, name="姜尘", background="边荒少年。"),
        ])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        db.add(CatalogingFact(
            job_id=job.id,
            chapter_run_id=run.id,
            project_id=project.id,
            chapter_id=chapter.id,
            fact_type="character_fact",
            raw_payload=json.dumps({
                "primary_name": "姜尘",
                "profile_clues": {
                    "moral_taboo": "不拿无辜者作饵",
                    "voice": "简短",
                },
            }, ensure_ascii=False),
            status="active",
        ))
        db.flush()
        summary = candidate(
            db,
            job,
            run,
            chapter,
            "chapter_summary",
            summary_payload(characters=["姜尘"]),
        )
        rows = [
            summary,
            candidate(db, job, run, chapter, "outline_create", {
                "node_type": "chapter", "title": chapter.title, "summary": "姜尘立誓。",
            }, 1),
            candidate(db, job, run, chapter, "character_state_update", {
                "name": "姜尘", "mental_state": "坚定",
            }, 2),
            candidate(db, job, run, chapter, "chapter_link", {
                "character_names": ["姜尘"],
            }, 3),
        ]

        incomplete = inspect_candidate_coverage(rows, db=db, project_id=project.id)
        assert incomplete.is_complete is True
        assert any(
            item.startswith(
                "source character profile evidence missing from coverage_manifest.character_profiles"
            )
            for item in incomplete.review_warnings
        )

        summary.edited_payload = json.dumps(
            summary_payload(characters=["姜尘"], character_profiles=["姜尘"]),
            ensure_ascii=False,
        )
        rows.append(candidate(db, job, run, chapter, "character_update", {
            "name": "姜尘",
            "profile": {
                "moral_taboo": "不拿无辜者作饵",
                "voice": "简短",
            },
        }, 4))

        complete = inspect_candidate_coverage(rows, db=db, project_id=project.id)
        assert complete.is_complete is True
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_relationship_applier_never_manufactures_blank_character_cards():
    engine, db = database()
    try:
        project = Project(id="project-1", title="关系引用")
        chapter = Chapter(id="chapter-1", project_id=project.id, title="第一章", content="甲拜乙为师。")
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        row = candidate(db, job, run, chapter, "character_relationship", {
            "source_name": "甲",
            "target_name": "乙",
            "relationship_type": "师徒",
        })

        with pytest.raises(ValueError, match="必须先生成角色档案候选"):
            apply_character_relationship(db, row, chapter, json.loads(row.raw_payload))

        assert db.query(Character).count() == 0
        assert db.query(CharacterRelationship).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_full_character_card_round_trips_through_storage_and_model_context():
    engine, db = database()
    try:
        project = Project(id="project-1", title="角色闭环")
        chapter = Chapter(id="chapter-1", project_id=project.id, title="第一章", content="姜尘压下伤势，决定回城。")
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(db, project.id, "auto", "deepseek:test", [chapter.id])
        run = job.chapter_runs[0]
        payload = {
            "name": "姜尘",
            "aliases": ["小尘"],
            "role_type": "protagonist",
            "appearance": "黑发少年，左肩带伤。",
            "age": "约十六岁",
            "personality": "谨慎而坚韧。",
            "background": "边荒少年，本章被多方势力察觉。",
            "abilities": ["感知窥视", "骨光"],
            "life_status": "alive",
            "current_location": "边荒城外",
            "realm_or_level": "初醒",
            "physical_state": "左肩剧痛",
            "mental_state": "警惕",
            "current_goal": "回城寻找石翁",
            "active_conflict": "被巫妖双方锁定",
            "abilities_state": "骨光刚刚苏醒",
            "items_or_assets": "石翁留下的骨片",
            "profile": {
                "core_motivation": "活下去并查清身世",
                "inner_lack": "缺乏归属感",
                "core_belief": "命运应由自己争取",
                "public_persona": "沉静少年",
                "hidden_persona": "对背叛高度敏感",
                "reveal_chapter": 1,
                "moral_taboo": "不牺牲无辜者",
                "voice": "短句、先判断后行动",
                "action_habit": "疼痛时按住左肩",
                "trauma_trigger": "被强者隔空注视",
            },
            "tone_style": "克制、冷静",
            "catchphrases": ["先回城。"],
            "verbosity": "brief",
            "emotion_tendency": "警惕",
            "custom_system_prompt": "保持姜尘谨慎、克制且行动优先的表达。",
        }
        row = candidate(db, job, run, chapter, "character_create", payload)

        apply_character_create(db, row, chapter, payload)
        db.commit()
        character = db.query(Character).filter_by(project_id=project.id, name="姜尘").one()
        snapshot = character_snapshot(character)
        context = build_targeted_context(
            db,
            project.id,
            chapter,
            [{"fact_type": "character_fact", "payload": {"name": "姜尘"}}],
        )
        model_character = context["relevant_characters"][0]
        local_model_character = _compact_local_runtime_context(context)["relevant_characters"][0]

        assert character.appearance == payload["appearance"]
        assert character.current_goal == payload["current_goal"]
        assert character.profile_json["voice"] == payload["profile"]["voice"]
        assert character.ai_config.verbosity == "brief"
        assert snapshot["profile"]["action_habit"] == "疼痛时按住左肩"
        assert snapshot["ai_config"]["tone_style"] == "克制、冷静"
        assert model_character["profile"]["trauma_trigger"] == "被强者隔空注视"
        assert model_character["ai_style"]["custom_system_prompt"].startswith("保持姜尘")
        assert local_model_character["profile"]["voice"] == "短句、先判断后行动"
        assert local_model_character["ai_style"]["verbosity"] == "brief"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
