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
    CharacterAIConfig,
    CharacterTimeline,
    OutlineNode,
    Project,
    WorldbuildingEntry,
    WorldbuildingTimeline,
)
from app.services.cataloging.applier import apply_candidates_for_run
from app.services.cataloging.chapter_rollback import rollback_cataloging_from_chapter
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


def test_cataloging_preserves_author_owned_chapter_volume():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-volume-owner", title="分卷归属")
        first_volume = OutlineNode(
            id="volume-one",
            project_id=project.id,
            node_type="volume",
            title="第一卷",
            sort_order=0,
        )
        second_volume = OutlineNode(
            id="volume-two",
            project_id=project.id,
            node_type="volume",
            title="第二卷",
            sort_order=1000,
        )
        planned = OutlineNode(
            id="outline-chapter-22",
            project_id=project.id,
            parent_id=second_volume.id,
            node_type="chapter",
            title="核签原件",
            summary="作者规划摘要",
            planned_summary="作者规划摘要",
            sort_order=0,
        )
        chapter = Chapter(
            id="chapter-22",
            project_id=project.id,
            outline_node_id=planned.id,
            title=planned.title,
            content="正式正文",
            current_version=1,
            sort_order=22000,
        )
        planning_section = OutlineNode(
            id="planning-section",
            project_id=project.id,
            parent_id=planned.id,
            node_type="section",
            title="立项场景",
            summary="尚未发生的规划",
            sort_order=1000,
        )
        db.add_all([
            project,
            first_volume,
            second_volume,
            planned,
            planning_section,
            chapter,
        ])
        db.commit()
        planning_section_id = planning_section.id

        job, run = _new_run(db, project, chapter)
        row = _candidate(
            db,
            job,
            run,
            "outline_create",
            {
                "node_type": "chapter",
                "title": planned.title,
                "summary": "正文实际摘要",
                # A model may omit parent_id; this used to fall back to volume one.
            },
            1,
        )
        db.flush()
        apply_candidates_for_run(db, job, run)
        db.commit()

        db.refresh(planned)
        db.refresh(chapter)
        db.refresh(row)
        assert planned.parent_id == second_volume.id
        assert planned.planned_summary == "作者规划摘要"
        assert planned.actual_summary == "正文实际摘要"
        assert chapter.outline_node_id == planned.id
        assert row.target_id == planned.id
        assert db.get(OutlineNode, planning_section_id) is None
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_cataloging_replaces_completed_planning_outline_in_place():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-completed-outline", title="已完成大纲")
        planned = OutlineNode(
            id="outline-completed",
            project_id=project.id,
            node_type="chapter",
            title="第一章 归港",
            summary="作者已完成的大纲",
            planned_summary="作者已完成的大纲",
            status="completed",
            sort_order=1000,
        )
        sibling = OutlineNode(
            id="outline-sibling",
            project_id=project.id,
            node_type="chapter",
            title="第二章 起航",
            status="pending",
            sort_order=2000,
        )
        chapter = Chapter(
            id="chapter-completed-outline",
            project_id=project.id,
            outline_node_id=planned.id,
            title=planned.title,
            content="正式正文",
            current_version=1,
            sort_order=1000,
        )
        db.add_all([project, planned, sibling, chapter])
        db.commit()

        before_ids = {
            row.id
            for row in db.query(OutlineNode).filter_by(
                project_id=project.id,
                node_type="chapter",
            ).all()
        }
        job, run = _new_run(db, project, chapter)
        row = _candidate(
            db,
            job,
            run,
            "outline_create",
            {
                "node_type": "chapter",
                "title": "第一章：归港",
                "summary": "正文实际摘要",
            },
            1,
        )
        db.flush()
        apply_candidates_for_run(db, job, run)
        db.commit()

        db.refresh(planned)
        db.refresh(chapter)
        db.refresh(row)
        after_ids = {
            item.id
            for item in db.query(OutlineNode).filter_by(
                project_id=project.id,
                node_type="chapter",
            ).all()
        }
        assert after_ids == before_ids
        assert chapter.outline_node_id == planned.id
        assert row.target_id == planned.id
        assert planned.title == "第一章：归港"
        assert planned.status == "completed"
        assert planned.planned_summary == "作者已完成的大纲"
        assert planned.actual_summary == "正文实际摘要"
        assert planned.summary == "正文实际摘要"
        assert planned.source_chapter_id == chapter.id
        assert planned.cataloging_status == "cataloged"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_recataloging_replaces_projection_without_losing_outline_position():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-recatalog-position", title="重建档位置")
        first_volume = OutlineNode(
            id="volume-first",
            project_id=project.id,
            node_type="volume",
            title="第一卷",
            sort_order=0,
        )
        second_volume = OutlineNode(
            id="volume-second",
            project_id=project.id,
            node_type="volume",
            title="第二卷",
            sort_order=1000,
        )
        planned = OutlineNode(
            id="outline-stable-slot",
            project_id=project.id,
            parent_id=second_volume.id,
            node_type="chapter",
            title="立项旧名",
            summary="立项时的情节规划",
            planned_summary="立项时的情节规划",
            status="completed",
            sort_order=7300,
        )
        chapter = Chapter(
            id="chapter-without-number",
            project_id=project.id,
            outline_node_id=planned.id,
            title="没有序号的正文标题",
            content="第一版正文",
            current_version=1,
            sort_order=22000,
        )
        db.add_all([project, first_volume, second_volume, planned, chapter])
        db.commit()

        first_job, first_run = _new_run(db, project, chapter)
        _candidate(
            db,
            first_job,
            first_run,
            "outline_update",
            {
                "id": planned.id,
                "node_type": "chapter",
                "title": "第一版实际标题",
                "summary": "第一版实际摘要",
            },
            1,
        )
        _candidate(
            db,
            first_job,
            first_run,
            "outline_create",
            {
                "node_type": "section",
                "scene_number": 1,
                "title": "旧场景",
                "summary": "第一版场景",
            },
            2,
        )
        db.flush()
        apply_candidates_for_run(db, first_job, first_run)
        db.commit()

        first_section = db.query(OutlineNode).filter_by(
            project_id=project.id,
            node_type="section",
        ).one()
        first_section_id = first_section.id
        assert planned.title == "第一版实际标题"
        assert planned.parent_id == second_volume.id
        assert first_section.parent_id == planned.id

        rollback_cataloging_from_chapter(
            db,
            project.id,
            chapter.id,
            reason="semantic_edit",
        )
        db.commit()
        db.refresh(chapter)
        db.refresh(planned)
        assert chapter.outline_node_id == planned.id
        assert planned.parent_id == second_volume.id
        assert planned.sort_order == 7300
        assert planned.summary == "立项时的情节规划"
        assert planned.actual_summary is None
        assert planned.cataloging_status is None
        assert db.get(OutlineNode, first_section_id) is None

        chapter.current_version = 2
        chapter.content = "第二版正文"
        second_job, second_run = _new_run(db, project, chapter)
        chapter_candidate = _candidate(
            db,
            second_job,
            second_run,
            "outline_update",
            {
                "id": planned.id,
                "node_type": "chapter",
                "title": "与立项完全不同的新标题",
                "summary": "第二版实际摘要",
            },
            1,
        )
        _candidate(
            db,
            second_job,
            second_run,
            "outline_create",
            {
                "node_type": "section",
                "scene_number": 1,
                "title": "新场景",
                "summary": "第二版场景",
            },
            2,
        )
        _candidate(
            db,
            second_job,
            second_run,
            "chapter_link",
            {
                "outline_title": "立项旧名",
                "source": "立项旧名",
                "characters": [],
                "worldbuilding_titles": [],
            },
            3,
        )
        db.flush()
        apply_candidates_for_run(db, second_job, second_run)
        db.commit()

        db.refresh(chapter)
        db.refresh(planned)
        db.refresh(chapter_candidate)
        assert chapter.outline_node_id == planned.id
        assert chapter_candidate.target_id == planned.id
        assert planned.parent_id == second_volume.id
        assert planned.sort_order == 7300
        assert planned.title == "与立项完全不同的新标题"
        assert planned.summary == "第二版实际摘要"
        assert planned.actual_summary == "第二版实际摘要"
        assert planned.source_chapter_id == chapter.id
        sections = db.query(OutlineNode).filter_by(
            project_id=project.id,
            node_type="section",
        ).all()
        assert [(item.title, item.parent_id) for item in sections] == [
            ("新场景", planned.id)
        ]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_revised_chapter_updates_linked_projection_without_deleting_outline_nodes():
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
            ("character_update", {"id": db.query(Character).filter_by(project_id=project.id, name="阿舟").one().id,
                                  "name": "阿舟", "personality": "果断",
                                  "background_before": "旧经历", "background": "旧经历；新经历"}),
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
        assert chapter.outline_node_id == legacy_duplicate.id
        assert planned.title == "第一章 归港"
        assert planned.planned_summary == "作者原定：主角雨夜归港。"
        assert planned.actual_summary in {None, ""}
        db.refresh(legacy_duplicate)
        assert legacy_duplicate.title == "第一章 归航"
        assert legacy_duplicate.actual_summary == "新实际摘要"
        assert (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project.id, OutlineNode.node_type == "chapter")
            .count()
            == 2
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


def test_revised_chapter_supersedes_unshared_worldbuilding_removed_from_projection():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-world-retire", title="旧设定隔离")
        chapter = Chapter(
            id="chapter-world-retire",
            project_id=project.id,
            title="第一章",
            content="旧版正文出现了错误站点。",
            current_version=1,
            sort_order=1000,
        )
        db.add_all([project, chapter])
        db.commit()

        first_job, first_run = _new_run(db, project, chapter)
        _candidate(db, first_job, first_run, "chapter_summary", _summary("旧摘要"), 1)
        _candidate(
            db,
            first_job,
            first_run,
            "outline_create",
            {"node_type": "chapter", "title": chapter.title, "summary": "旧摘要"},
            2,
        )
        _candidate(
            db,
            first_job,
            first_run,
            "worldbuilding_create",
            {"title": "错误站点", "dimension": "geography", "content": "旧版错误设定"},
            3,
        )
        db.flush()
        apply_candidates_for_run(db, first_job, first_run)
        db.commit()
        entry = db.query(WorldbuildingEntry).filter_by(
            project_id=project.id,
            title="错误站点",
        ).one()
        assert entry.status == "active"

        chapter.current_version = 2
        chapter.content = "新版正文删除了错误站点。"
        db.commit()
        second_job, second_run = _new_run(db, project, chapter)
        _candidate(db, second_job, second_run, "chapter_summary", _summary("新摘要"), 1)
        _candidate(
            db,
            second_job,
            second_run,
            "outline_create",
            {"node_type": "chapter", "title": chapter.title, "summary": "新摘要"},
            2,
        )
        # A model may repeat an older card in the weak chapter link even though
        # the revised projection no longer contains a direct worldbuilding
        # candidate for it.  That link must not keep the obsolete card active.
        _candidate(
            db,
            second_job,
            second_run,
            "chapter_link",
            {"worldbuilding_titles": ["错误站点"]},
            3,
        )
        db.flush()
        apply_candidates_for_run(db, second_job, second_run)
        db.commit()

        db.refresh(entry)
        assert entry.status == "superseded"
        assert db.query(ChapterWorldbuilding).filter_by(
            chapter_id=chapter.id,
            worldbuilding_entry_id=entry.id,
        ).count() == 0
        assert "旧 chapter_link 不会继续激活" in (second_run.review_warning or "")
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_revised_chapter_restores_omitted_update_to_shared_worldbuilding_snapshot():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-world-restore", title="共享设定回退")
        chapter = Chapter(
            id="chapter-world-restore",
            project_id=project.id,
            title="第一章",
            content="旧版正文把错误描述写进共享地点。",
            current_version=1,
            sort_order=1000,
        )
        entry = WorldbuildingEntry(
            id="shared-world-entry",
            project_id=project.id,
            title="港务档案保管点",
            dimension="geography",
            content="作者确认的基线内容。",
            status="active",
        )
        db.add_all([project, chapter, entry])
        db.commit()

        first_job, first_run = _new_run(db, project, chapter)
        _candidate(
            db,
            first_job,
            first_run,
            "worldbuilding_update",
            {
                "id": entry.id,
                "title": entry.title,
                "dimension": "geography",
                "content": "旧版章节追加的错误描述。",
            },
            1,
        )
        db.flush()
        apply_candidates_for_run(db, first_job, first_run)
        db.commit()
        db.refresh(entry)
        assert "旧版章节追加的错误描述" in entry.content

        chapter.current_version = 2
        chapter.content = "新版正文删除了错误描述，但仍发生在该地点。"
        db.commit()
        second_job, second_run = _new_run(db, project, chapter)
        _candidate(db, second_job, second_run, "chapter_summary", _summary("新摘要"), 1)
        _candidate(
            db,
            second_job,
            second_run,
            "chapter_link",
            {"worldbuilding_titles": [entry.title]},
            2,
        )
        db.flush()
        apply_candidates_for_run(db, second_job, second_run)
        db.commit()

        db.refresh(entry)
        db.refresh(second_run)
        assert entry.content == "作者确认的基线内容。"
        assert entry.status == "active"
        assert db.query(ChapterWorldbuilding).filter_by(
            chapter_id=chapter.id,
            worldbuilding_entry_id=entry.id,
        ).count() == 1
        assert "已恢复旧版本写入前快照" in (second_run.review_warning or "")
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_revised_chapter_never_overwrites_author_edit_when_restoring_old_update():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-world-author-edit", title="作者修改优先")
        chapter = Chapter(
            id="chapter-world-author-edit",
            project_id=project.id,
            title="第一章",
            content="旧版正文。",
            current_version=1,
            sort_order=1000,
        )
        entry = WorldbuildingEntry(
            id="author-edited-world-entry",
            project_id=project.id,
            title="水文站",
            dimension="geography",
            content="初始内容。",
            status="active",
        )
        db.add_all([project, chapter, entry])
        db.commit()

        first_job, first_run = _new_run(db, project, chapter)
        _candidate(
            db,
            first_job,
            first_run,
            "worldbuilding_update",
            {
                "id": entry.id,
                "title": entry.title,
                "dimension": entry.dimension,
                "content": "旧版模型追加。",
            },
            1,
        )
        db.flush()
        apply_candidates_for_run(db, first_job, first_run)
        db.commit()

        # Explicit author action after the old catalog run.  Snapshot mismatch
        # is the deterministic ownership boundary for the rollback.
        entry.content = "作者在旧版建档后亲自确认的内容。"
        db.commit()
        chapter.current_version = 2
        chapter.content = "新版正文。"
        db.commit()

        second_job, second_run = _new_run(db, project, chapter)
        _candidate(db, second_job, second_run, "chapter_summary", _summary("新摘要"), 1)
        db.flush()
        apply_candidates_for_run(db, second_job, second_run)
        db.commit()

        db.refresh(entry)
        db.refresh(second_run)
        assert entry.content == "作者在旧版建档后亲自确认的内容。"
        assert "系统未自动覆盖，请人工核对" in (second_run.review_warning or "")
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_recataloging_does_not_reactivate_worldbuilding_retired_by_author():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-author-retired", title="作者停用优先")
        chapter = Chapter(
            id="chapter-author-retired",
            project_id=project.id,
            title="第一章",
            content="旧版正文",
            current_version=1,
            sort_order=1000,
        )
        db.add_all([project, chapter])
        db.commit()

        first_job, first_run = _new_run(db, project, chapter)
        first_candidate = _candidate(
            db,
            first_job,
            first_run,
            "worldbuilding_create",
            {"title": "旧版错误地点", "dimension": "geography", "content": "错误内容"},
            1,
        )
        db.flush()
        apply_candidates_for_run(db, first_job, first_run)
        db.commit()
        entry = db.query(WorldbuildingEntry).filter_by(title="旧版错误地点").one()
        assert first_candidate.target_id == entry.id
        assert db.query(ChapterWorldbuilding).filter_by(
            chapter_id=chapter.id,
            worldbuilding_entry_id=entry.id,
        ).count() == 1

        # This is an explicit author decision between catalog runs.
        entry.status = "superseded"
        chapter.current_version = 2
        chapter.content = "新版正文仍触及地点，但作者已把旧卡并入权威实体。"
        db.commit()

        second_job, second_run = _new_run(db, project, chapter)
        second_candidate = _candidate(
            db,
            second_job,
            second_run,
            "worldbuilding_create",
            {"title": "旧版错误地点", "dimension": "geography", "content": "改正后的内容"},
            1,
        )
        db.flush()
        events = apply_candidates_for_run(db, second_job, second_run)
        db.commit()

        db.refresh(entry)
        db.refresh(second_run)
        db.refresh(second_candidate)
        assert entry.status == "superseded"
        assert db.query(WorldbuildingEntry).filter_by(project_id=project.id).count() == 1
        assert db.query(ChapterWorldbuilding).filter_by(chapter_id=chapter.id).count() == 0
        assert second_candidate.status == "applied"
        assert second_candidate.target_id == entry.id
        assert second_candidate.target_type == "worldbuilding_suppressed"
        assert "未重新激活或新建重复词条" in (second_run.review_warning or "")
        assert events[0]["data"]["old_value"]["status"] == "superseded"
        assert events[0]["data"]["new_value"]["status"] == "superseded"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_revised_chapter_preserves_worldbuilding_shared_by_another_chapter():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-world-shared", title="共享设定")
        source = Chapter(
            id="chapter-source",
            project_id=project.id,
            title="第一章",
            content="建立港区。",
            current_version=1,
            sort_order=1000,
        )
        later = Chapter(
            id="chapter-later",
            project_id=project.id,
            title="第二章",
            content="继续使用港区。",
            current_version=1,
            sort_order=2000,
        )
        db.add_all([project, source, later])
        db.commit()

        first_job, first_run = _new_run(db, project, source)
        _candidate(db, first_job, first_run, "chapter_summary", _summary("旧摘要"), 1)
        _candidate(
            db,
            first_job,
            first_run,
            "outline_create",
            {"node_type": "chapter", "title": source.title, "summary": "旧摘要"},
            2,
        )
        _candidate(
            db,
            first_job,
            first_run,
            "worldbuilding_create",
            {"title": "共享港区", "dimension": "geography", "content": "两章共用"},
            3,
        )
        db.flush()
        apply_candidates_for_run(db, first_job, first_run)
        db.commit()
        entry = db.query(WorldbuildingEntry).filter_by(title="共享港区").one()
        db.add(ChapterWorldbuilding(
            chapter_id=later.id,
            worldbuilding_entry_id=entry.id,
            description="第二章仍使用",
        ))
        db.commit()

        source.current_version = 2
        source.content = "新版第一章不再提及港区。"
        db.commit()
        second_job, second_run = _new_run(db, project, source)
        _candidate(db, second_job, second_run, "chapter_summary", _summary("新摘要"), 1)
        _candidate(
            db,
            second_job,
            second_run,
            "outline_create",
            {"node_type": "chapter", "title": source.title, "summary": "新摘要"},
            2,
        )
        db.flush()
        apply_candidates_for_run(db, second_job, second_run)
        db.commit()

        db.refresh(entry)
        assert entry.status == "active"
        assert db.query(ChapterWorldbuilding).filter_by(
            chapter_id=later.id,
            worldbuilding_entry_id=entry.id,
        ).count() == 1
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


def test_recataloging_character_replaces_owned_text_but_preserves_author_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-character-author-edit", title="角色作者修改优先")
        chapter = Chapter(
            id="chapter-character-author-edit",
            project_id=project.id,
            title="第一章",
            content="旧版正文。",
            current_version=1,
            sort_order=1000,
        )
        character = Character(
            id="character-author-edit",
            project_id=project.id,
            name="周芷",
            personality="谨慎",
            background="记者基础经历",
        )
        config = CharacterAIConfig(
            character_id=character.id,
            custom_system_prompt="作者初始提示",
        )
        db.add_all([project, chapter, character, config])
        db.commit()

        first_job, first_run = _new_run(db, project, chapter)
        _candidate(
            db,
            first_job,
            first_run,
            "character_update",
            {
                "id": character.id,
                "name": character.name,
                "personality": "旧版变化",
                "background_before": "记者基础经历",
                "background": "记者基础经历；旧版章节经历",
                "custom_system_prompt": "旧版模型提示",
            },
            1,
        )
        db.flush()
        apply_candidates_for_run(db, first_job, first_run)
        db.commit()

        db.refresh(character)
        db.refresh(config)
        assert "旧版章节经历" in (character.background or "")
        assert config.custom_system_prompt == "旧版模型提示"

        # Explicit author edits create an ownership boundary before v2.
        character.background = "作者审定经历"
        config.custom_system_prompt = "作者审定提示"
        chapter.current_version = 2
        chapter.content = "新版正文。"
        db.commit()

        second_job, second_run = _new_run(db, project, chapter)
        second_candidate = _candidate(
            db,
            second_job,
            second_run,
            "character_update",
            {
                "id": character.id,
                "name": character.name,
                "personality": "新版变化",
                "background_before": "作者审定经历",
                "background": "作者审定经历；新版章节经历",
                "custom_system_prompt": "新版模型提示",
            },
            1,
        )
        db.flush()
        apply_candidates_for_run(db, second_job, second_run)
        db.commit()

        db.refresh(character)
        db.refresh(config)
        db.refresh(second_run)
        db.refresh(second_candidate)
        assert character.background == "作者审定经历"
        assert config.custom_system_prompt == "作者审定提示"
        assert character.personality == "新版变化"
        assert second_candidate.status == "applied"
        assert "background" in (second_run.review_warning or "")
        assert "custom_system_prompt" in (second_run.review_warning or "")
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_character_update_preserves_complete_background_and_appends_new_history_once():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-complete-character-profile", title="完整角色档案替换")
        chapter = Chapter(
            id="chapter-complete-character-profile",
            project_id=project.id,
            title="第二十二章 原件与范围",
            content="本章正文。",
            current_version=1,
            sort_order=22000,
        )
        existing_background = (
            "32岁，档案馆声像修复师；"
            "第十一章中逐项核验十二盘材料并保留全部限制；"
            "第十二章中确认设备编号并拒绝统一校时"
        )
        character = Character(
            id="character-complete-character-profile",
            project_id=project.id,
            name="林澄",
            personality="谨慎、坚持原则",
            background=existing_background,
        )
        db.add_all([project, chapter, character])
        db.commit()

        rewritten_background = existing_background + "；第二十二章中核读发文底档原件并重排批次"
        job, run = _new_run(db, project, chapter)
        _candidate(
            db,
            job,
            run,
            "character_update",
            {
                "id": character.id,
                "name": character.name,
                "personality": "谨慎、坚持原则、愿意承担返工代价",
                "background_before": existing_background,
                "background": rewritten_background,
            },
            1,
        )
        db.flush()

        apply_candidates_for_run(db, job, run)
        db.commit()
        db.refresh(character)

        assert character.background == rewritten_background
        assert character.background.count("第十一章") == 1
        assert character.background.count("第十二章") == 1
        assert "保留全部限制" in character.background
        assert character.personality == "谨慎、坚持原则、愿意承担返工代价"
        assert "《第二十二章 原件与范围》" not in character.personality
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
