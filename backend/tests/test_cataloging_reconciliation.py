"""Regression coverage for re-cataloging a revised formal chapter."""

from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    CatalogingCandidate,
    Chapter,
    ChapterCharacter,
    ChapterSummary,
    ChapterWorldbuilding,
    Character,
    CharacterTimeline,
    OutlineNode,
    Project,
    WorldbuildingEntry,
    WorldbuildingTimeline,
)
from app.services.cataloging.applier import apply_candidates_for_run
from app.services.cataloging.orchestrator import create_cataloging_job


def _candidate(db, job, run, item_type: str, payload: dict, sort_order: int):
    row = CatalogingCandidate(
        job_id=job.id,
        chapter_run_id=run.id,
        project_id=run.project_id,
        chapter_id=run.chapter_id,
        item_type=item_type,
        raw_payload=json.dumps(payload, ensure_ascii=False),
        sort_order=sort_order,
    )
    db.add(row)
    return row


def _summary(text: str) -> dict:
    return {
        "summary_text": text,
        "key_events": [text],
        "narrative_state": {
            "events": [],
            "foreshadowing_planted": [],
            "foreshadowing_resolved": [],
            "storyline_progress": [],
            "unresolved_actions": [],
        },
        "narrative_review": {"source": "provided", "outcome": "assessed"},
    }


def _new_run(db, project: Project, chapter: Chapter):
    job = create_cataloging_job(db, project.id, "auto", "test:model", [chapter.id])
    return job, job.chapter_runs[0]


def test_revised_chapter_reconciles_projection_instead_of_appending_duplicates():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-1", title="重建档")
        planned = OutlineNode(
            id="outline-planned",
            project_id=project.id,
            node_type="chapter",
            title="第一章 归港",
            summary="作者原定：主角雨夜归港。",
            planned_summary="作者原定：主角雨夜归港。",
            sort_order=1000,
        )
        legacy_duplicate = OutlineNode(
            id="outline-legacy-duplicate",
            project_id=project.id,
            node_type="chapter",
            title="第一章：归港",
            summary="旧版建档误建的重复章节节点。",
            actual_summary="旧实际摘要",
            source_chapter_id="chapter-1",
            cataloging_status="cataloged",
            sort_order=2000,
        )
        legacy_scene = OutlineNode(
            id="outline-legacy-scene",
            project_id=project.id,
            parent_id=legacy_duplicate.id,
            node_type="section",
            title="码头",
            summary="旧码头场景",
            actual_summary="旧码头场景",
            source_chapter_id="chapter-1",
            cataloging_status="cataloged",
            sort_order=1000,
        )
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            outline_node_id=legacy_duplicate.id,
            title=planned.title,
            content="旧正文",
            current_version=1,
            sort_order=1000,
        )
        db.add_all([project, planned, legacy_duplicate, legacy_scene, chapter])
        db.commit()

        first_job, first_run = _new_run(db, project, chapter)
        first_rows = [
            ("chapter_summary", _summary("旧摘要")),
            (
                "outline_create",
                {"node_type": "chapter", "title": "第一章：归港", "summary": "旧实际摘要"},
            ),
            (
                "outline_create",
                {
                    "node_type": "section",
                    "title": "码头",
                    "scene_number": 1,
                    "summary": "旧码头场景",
                    "related_characters": ["阿舟", "掌柜"],
                },
            ),
            (
                "outline_create",
                {
                    "node_type": "section",
                    "title": "客栈",
                    "scene_number": 2,
                    "summary": "旧客栈场景",
                },
            ),
            ("character_create", {"name": "阿舟", "personality": "谨慎", "background": "旧经历"}),
            ("character_create", {"name": "掌柜", "personality": "圆滑", "background": "客栈掌柜"}),
            ("character_state_update", {"name": "阿舟", "current_location": "码头"}),
            (
                "character_timeline",
                {
                    "name": "阿舟",
                    "event_type": "decision",
                    "event_description": "决定住店",
                    "sort_order": 1,
                },
            ),
            (
                "character_timeline",
                {
                    "name": "阿舟",
                    "event_type": "conflict",
                    "event_description": "与掌柜争执",
                    "sort_order": 2,
                },
            ),
            (
                "worldbuilding_create",
                {"title": "旧港", "dimension": "geography", "content": "旧港常年多雨"},
            ),
            (
                "worldbuilding_timeline",
                {
                    "title": "旧港",
                    "event_type": "introduced",
                    "event_description": "首次出现",
                    "sort_order": 1,
                },
            ),
            (
                "chapter_link",
                {"character_names": ["阿舟", "掌柜"], "worldbuilding_titles": ["旧港"]},
            ),
        ]
        for index, (item_type, payload) in enumerate(first_rows, start=1):
            _candidate(db, first_job, first_run, item_type, payload, index)
        db.flush()
        apply_candidates_for_run(db, first_job, first_run)
        db.commit()

        chapter.current_version = 2
        chapter.content = "新正文只保留码头，并改写决定。"
        second_job, second_run = _new_run(db, project, chapter)
        second_rows = [
            ("chapter_summary", _summary("新摘要")),
            (
                "outline_create",
                {"node_type": "chapter", "title": "第一章 归航", "summary": "新实际摘要"},
            ),
            (
                "outline_create",
                {
                    "node_type": "section",
                    "title": "雨夜码头",
                    "scene_number": 1,
                    "summary": "新码头场景",
                    "related_characters": ["阿舟"],
                },
            ),
            ("character_update", {"name": "阿舟", "personality": "果断", "background": "新经历"}),
            ("character_state_update", {"name": "阿舟", "current_location": "码头"}),
            (
                "character_timeline",
                {
                    "name": "阿舟",
                    "event_type": "decision",
                    "event_description": "决定连夜离港",
                    "sort_order": 1,
                },
            ),
            (
                "worldbuilding_update",
                {"title": "旧港", "dimension": "geography", "content": "旧港在雨季封航"},
            ),
            (
                "worldbuilding_timeline",
                {
                    "title": "旧港",
                    "event_type": "introduced",
                    "event_description": "确认雨季封航",
                    "sort_order": 1,
                },
            ),
            ("chapter_link", {"character_names": ["阿舟"], "worldbuilding_titles": ["旧港"]}),
        ]
        for index, (item_type, payload) in enumerate(second_rows, start=1):
            _candidate(db, second_job, second_run, item_type, payload, index)
        db.flush()
        apply_candidates_for_run(db, second_job, second_run)
        db.commit()

        db.refresh(chapter)
        db.refresh(planned)
        assert chapter.outline_node_id == planned.id
        assert planned.title == "第一章 归港"
        assert planned.planned_summary == "作者原定：主角雨夜归港。"
        assert planned.actual_summary == "新实际摘要"
        assert (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project.id, OutlineNode.node_type == "chapter")
            .count()
            == 1
        )
        sections = (
            db.query(OutlineNode)
            .filter(OutlineNode.source_chapter_id == chapter.id, OutlineNode.node_type == "section")
            .all()
        )
        assert [(node.title, node.actual_summary) for node in sections] == [
            ("雨夜码头", "新码头场景")
        ]
        assert [link.character.name for link in sections[0].linked_characters] == ["阿舟"]
        assert (
            db.query(CharacterTimeline).filter(CharacterTimeline.chapter_id == chapter.id).count()
            == 1
        )
        assert db.query(CharacterTimeline).one().event_description == "决定连夜离港"
        assert (
            db.query(WorldbuildingTimeline)
            .filter(WorldbuildingTimeline.chapter_id == chapter.id)
            .count()
            == 1
        )
        assert db.query(WorldbuildingTimeline).one().event_description == "确认雨季封航"
        assert (
            db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).count()
            == 1
        )
        assert (
            db.query(ChapterWorldbuilding)
            .filter(ChapterWorldbuilding.chapter_id == chapter.id)
            .count()
            == 1
        )
        assert db.query(Character).filter(Character.project_id == project.id).count() == 2
        assert (
            db.query(WorldbuildingEntry).filter(WorldbuildingEntry.project_id == project.id).count()
            == 1
        )
        assert (
            db.query(ChapterSummary)
            .filter(ChapterSummary.chapter_id == chapter.id)
            .one()
            .summary_text
            == "新摘要"
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_recataloging_an_older_chapter_does_not_regress_latest_character_state():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-2", title="状态顺序")
        earlier = Chapter(
            id="chapter-early",
            project_id=project.id,
            title="第一章",
            content="旧",
            current_version=2,
            sort_order=1000,
        )
        later = Chapter(
            id="chapter-late",
            project_id=project.id,
            title="第二章",
            content="新",
            current_version=1,
            sort_order=2000,
        )
        character = Character(
            id="character-1",
            project_id=project.id,
            name="阿舟",
            current_location="山城",
            last_updated_chapter_id=later.id,
        )
        db.add_all([project, earlier, later, character])
        db.commit()
        job, run = _new_run(db, project, earlier)
        _candidate(
            db, job, run, "character_state_update", {"name": "阿舟", "current_location": "旧港"}, 1
        )
        db.flush()

        apply_candidates_for_run(db, job, run)
        db.commit()

        db.refresh(character)
        assert character.current_location == "山城"
        assert character.last_updated_chapter_id == later.id
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
