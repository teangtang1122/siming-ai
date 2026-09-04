"""Regression tests for the core cumulative cataloging repair path."""
from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    CatalogingCandidate,
    CatalogingFact,
    Chapter,
    Character,
    OutlineNode,
    Project,
    WorldbuildingEntry,
    WorldbuildingTimeline,
)
from app.services.cataloging.candidate_store import create_candidate_from_raw
from app.services.cataloging.applier import apply_candidates_for_run
from app.services.cataloging.outline_ops import apply_outline
from app.services.cataloging.candidate_validation import (
    candidate_coverage_error_message,
    candidate_coverage_should_retry,
    inspect_candidate_coverage,
)
from app.services.cataloging.orchestrator import (
    _candidate_coverage_error,
    create_cataloging_job,
)


def database():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def summary_payload(**manifest):
    return {
        "summary_text": "本章完成了可验证的连续性变化。",
        "coverage_manifest": {
            "scene_count": 1,
            "characters": [],
            "worldbuilding": [],
            "relationships": [],
            "character_profiles": [],
            **manifest,
        },
        "narrative_state": {
            "events": [{"title": "本章事件", "description": "事件已经发生"}],
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


def candidate(db, job, run, chapter, item_type, payload, sort_order=0):
    row = CatalogingCandidate(
        job_id=job.id,
        chapter_run_id=run.id,
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        item_type=item_type,
        raw_payload=json.dumps(payload, ensure_ascii=False),
        sort_order=sort_order,
        status="pending",
    )
    db.add(row)
    db.flush()
    return row


def base_rows(db, job, run, chapter, **manifest):
    return [
        candidate(
            db,
            job,
            run,
            chapter,
            "chapter_summary",
            summary_payload(**manifest),
        ),
        candidate(
            db,
            job,
            run,
            chapter,
            "outline_create",
            {
                "node_type": "chapter",
                "title": chapter.title,
                "summary": "本章结构摘要。",
            },
            1,
        ),
    ]


def test_decorated_worldbuilding_title_matches_manifest_identity():
    engine, db = database()
    try:
        project = Project(id="project-1", title="标题归一化")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="系统没有界面。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        rows = base_rows(db, job, run, chapter, worldbuilding=["系统"])
        rows.extend(
            [
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "worldbuilding_create",
                    {
                        "title": "系统（无界面·无沟通·自行探索型）",
                        "dimension": "power_system",
                        "content": "系统没有界面和主动沟通能力。",
                    },
                    2,
                ),
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "chapter_link",
                    {
                        "worldbuilding_titles": [
                            "系统（无界面·无沟通·自行探索型）"
                        ]
                    },
                    3,
                ),
            ]
        )

        coverage = inspect_candidate_coverage(
            rows,
            db=db,
            project_id=project.id,
        )

        assert coverage.is_complete is True
        assert coverage.declared_worldbuilding_identities == ("系统",)
        assert coverage.worldbuilding_candidate_identities == ("系统",)
        assert coverage.chapter_link_worldbuilding_identities == ("系统",)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_existing_unchanged_worldbuilding_requires_link_not_fake_update():
    engine, db = database()
    try:
        project = Project(id="project-1", title="既有设定引用")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="系统再次出现。",
        )
        entry = WorldbuildingEntry(
            project_id=project.id,
            dimension="power_system",
            title="系统",
            content="既有设定。",
        )
        db.add_all([project, chapter, entry])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        rows = base_rows(db, job, run, chapter, worldbuilding=["系统"])
        rows.append(
            candidate(
                db,
                job,
                run,
                chapter,
                "chapter_link",
                {
                    "worldbuilding_titles": ["系统"],
                    "description": "本章关键引用",
                },
                2,
            )
        )

        coverage = inspect_candidate_coverage(
            rows,
            db=db,
            project_id=project.id,
        )

        assert coverage.is_complete is True
        assert coverage.worldbuilding_candidate_count == 1
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_unresolved_figure_stays_chapter_local_without_blank_character_card():
    engine, db = database()
    try:
        project = Project(id="project-1", title="匿名角色")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="神秘人影一闪而逝。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        rows = base_rows(
            db,
            job,
            run,
            chapter,
            characters=["神秘人影"],
            character_profiles=["神秘人影"],
        )
        rows.extend(
            [
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "character_state_update",
                    {
                        "name": "神秘人影",
                        "life_status": "unknown",
                        "current_location": "屋外",
                        "physical_state": "一闪而逝",
                    },
                    2,
                ),
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "chapter_link",
                    {
                        "character_names": ["神秘人影"],
                        "description": "身份尚未确认",
                    },
                    3,
                ),
            ]
        )

        coverage = inspect_candidate_coverage(
            rows,
            db=db,
            project_id=project.id,
        )

        assert coverage.is_complete is True
        assert coverage.declared_character_profile_count == 0
        assert any("身份未确认角色" in item for item in coverage.review_warnings)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_stable_named_newcomer_with_appearance_is_persistable_profile():
    engine, db = database()
    try:
        project = Project(id="project-1", title="最低角色资料")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="陈叙推门而入。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        rows = base_rows(
            db,
            job,
            run,
            chapter,
            characters=["陈叙"],
            character_profiles=["陈叙"],
        )
        rows.extend(
            [
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "character_create",
                    {
                        "name": "陈叙",
                        "appearance": "身形清瘦，右眉有旧伤。",
                        "age": "约二十岁",
                    },
                    2,
                ),
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "character_state_update",
                    {
                        "name": "陈叙",
                        "current_location": "门内",
                        "life_status": "alive",
                    },
                    3,
                ),
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "chapter_link",
                    {"character_names": ["陈叙"]},
                    4,
                ),
            ]
        )

        coverage = inspect_candidate_coverage(
            rows,
            db=db,
            project_id=project.id,
        )

        assert coverage.is_complete is True
        assert coverage.character_profile_candidate_identities == ("陈叙",)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_incremental_profile_card_completes_retained_summary_without_replay():
    engine, db = database()
    try:
        project = Project(id="project-1", title="增量角色资料")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="刘三踢翻木桶，逼沈青冥交出馒头。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        rows = base_rows(
            db,
            job,
            run,
            chapter,
            characters=["刘三"],
            character_profiles=[],
        )
        rows.extend(
            [
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "character_state_update",
                    {
                        "name": "刘三",
                        "current_location": "杂役院",
                        "mental_state": "盛气凌人",
                    },
                    2,
                ),
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "chapter_link",
                    {"character_names": ["刘三"]},
                    3,
                ),
            ]
        )

        incomplete = inspect_candidate_coverage(
            rows,
            db=db,
            project_id=project.id,
        )
        assert incomplete.is_complete is False
        assert "刘三" in candidate_coverage_error_message(incomplete)

        created = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "character_create",
                "name": "刘三",
                "role_type": "other",
                "background": "杂役院管事弟子，长期欺压杂役。",
                "personality": "盛气凌人。",
            },
            4,
            source_task="incremental_repair",
        )
        rows.append(created["candidate"])

        complete = inspect_candidate_coverage(
            rows,
            db=db,
            project_id=project.id,
        )
        assert complete.is_complete is True
        assert complete.declared_character_profile_identities == ("刘三",)
        assert complete.character_profile_candidate_identities == ("刘三",)
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_real_missing_identity_is_retryable_and_reported_exactly():
    engine, db = database()
    try:
        project = Project(id="project-1", title="定向补缺")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="系统启动归墟。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        rows = base_rows(
            db,
            job,
            run,
            chapter,
            worldbuilding=["系统", "归墟"],
        )
        rows.extend(
            [
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "worldbuilding_create",
                    {
                        "title": "系统",
                        "dimension": "power_system",
                        "content": "负责引导探索。",
                    },
                    2,
                ),
                candidate(
                    db,
                    job,
                    run,
                    chapter,
                    "chapter_link",
                    {"worldbuilding_titles": ["系统", "归墟"]},
                    3,
                ),
            ]
        )

        coverage = inspect_candidate_coverage(
            rows,
            db=db,
            project_id=project.id,
        )
        message = candidate_coverage_error_message(coverage)

        assert coverage.is_complete is False
        assert candidate_coverage_should_retry(coverage) is True
        assert "归墟" in message
        assert "缺少世界观候选或既有设定关联" in message
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_incremental_retry_cannot_shrink_the_accepted_summary_contract():
    engine, db = database()
    try:
        project = Project(id="project-1", title="累计修复")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="正文。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        first = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "chapter_summary",
                **summary_payload(
                    scene_count=6,
                    characters=["沈青冥"],
                ),
            },
            0,
        )
        second = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "chapter_summary",
                **summary_payload(
                    scene_count=5,
                    characters=["剑灵"],
                ),
            },
            1,
        )

        assert first.get("candidate") is not None
        assert second.get("updated") is True
        rows = (
            db.query(CatalogingCandidate)
            .filter(CatalogingCandidate.chapter_run_id == run.id)
            .all()
        )
        assert len(rows) == 1
        payload = json.loads(rows[0].raw_payload)
        manifest = payload["coverage_manifest"]
        assert manifest["scene_count"] == 6
        assert manifest["characters"] == ["沈青冥", "剑灵"]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_incremental_section_retry_updates_same_scene_number_instead_of_duplicating():
    engine, db = database()
    try:
        project = Project(id="project-scene-retry", title="场景候选去重")
        chapter = Chapter(
            id="chapter-scene-retry",
            project_id=project.id,
            title="第一章",
            content="正文。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]

        first = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "outline_create",
                "node_type": "section",
                "scene_number": 4,
                "title": "第一章 / 播发与说明",
                "summary": "首次场景摘要。",
                "purpose": "推进播发流程",
                "entry_state": "稿件待发",
                "exit_state": "稿件播发",
            },
            1,
        )
        second = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "outline_create",
                "node_type": "section",
                "scene_number": 4,
                "title": "第一章 / 播发与补发函",
                "summary": "重试后的完整场景摘要。",
                "purpose": "落实播发与补发函",
                "entry_state": "稿件待发",
                "exit_state": "加注与补发函落地",
            },
            2,
        )

        assert first.get("candidate") is not None
        assert second.get("updated") is True
        rows = db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).all()
        assert len(rows) == 1
        payload = json.loads(rows[0].raw_payload)
        assert payload["scene_number"] == 4
        assert payload["title"] == "第一章 / 播发与补发函"
        assert payload["summary"] == "重试后的完整场景摘要。"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_section_apply_reuses_cataloged_scene_number_when_retry_changes_title():
    engine, db = database()
    try:
        project = Project(id="project-scene-apply", title="场景投影去重")
        volume = OutlineNode(
            id="volume-1",
            project_id=project.id,
            node_type="volume",
            title="第一卷",
        )
        chapter_outline = OutlineNode(
            id="outline-chapter-1",
            project_id=project.id,
            parent_id=volume.id,
            node_type="chapter",
            title="第一章",
        )
        chapter = Chapter(
            id="chapter-scene-apply",
            project_id=project.id,
            outline_node_id=chapter_outline.id,
            title="第一章",
            content="正文。",
        )
        existing = OutlineNode(
            id="section-scene-4",
            project_id=project.id,
            parent_id=chapter_outline.id,
            node_type="section",
            title="第一章 / 播发与说明",
            source_chapter_id=chapter.id,
            cataloging_status="cataloged",
            metadata_json={"scene_number": 4},
        )
        db.add_all([project, volume, chapter_outline, chapter, existing])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        row = candidate(
            db,
            job,
            run,
            chapter,
            "outline_create",
            {
                "node_type": "section",
                "scene_number": 4,
                "title": "第一章 / 播发与补发函",
                "summary": "更新后的场景摘要。",
                "purpose": "落实播发与补发函",
                "entry_state": "稿件待发",
                "exit_state": "补发函落地",
            },
            1,
        )

        result = apply_outline(
            db,
            row,
            chapter,
            json.loads(row.raw_payload),
            True,
        )
        db.flush()

        assert result["target_id"] == existing.id
        assert db.query(OutlineNode).filter_by(
            project_id=project.id,
            source_chapter_id=chapter.id,
            node_type="section",
        ).count() == 1
        assert existing.title == "第一章 / 播发与补发函"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_incremental_worldbuilding_alias_batches_collapse_to_exact_id_update():
    engine, db = database()
    try:
        project = Project(id="project-world-alias", title="世界观候选归并")
        chapter = Chapter(
            id="chapter-world-alias",
            project_id=project.id,
            title="第一章",
            content="主角先到临汐水文站，后文简称水文站。",
        )
        entry = WorldbuildingEntry(
            id="world-station",
            project_id=project.id,
            title="临汐水文站",
            dimension="geography",
            content="作者确认的站点基线。",
            status="active",
        )
        db.add_all([project, chapter, entry])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]

        first = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "worldbuilding_update",
                "id": entry.id,
                "title": "临汐水文站",
                "dimension": "geography",
                "content": "本章在临汐水文站提交材料。",
            },
            1,
        )
        second = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "worldbuilding_update",
                "id": entry.id,
                "title": "水文站",
                "dimension": "geography",
                "content": "本章在水文站取得收件编号。",
            },
            2,
        )
        third = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "worldbuilding_create",
                "title": "水文站",
                "dimension": "geography",
                "content": "水文站是本章的补正受理地点。",
            },
            3,
        )

        assert first.get("candidate") is not None
        assert second.get("updated") is True
        assert third.get("updated") is True
        rows = db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).all()
        assert len(rows) == 1
        assert rows[0].item_type == "worldbuilding_update"
        assert rows[0].target_id == entry.id
        payload = json.loads(rows[0].raw_payload)
        assert payload["id"] == entry.id

        apply_candidates_for_run(db, job, run)
        db.commit()
        db.refresh(entry)
        assert db.query(WorldbuildingEntry).filter_by(project_id=project.id).count() == 1
        assert entry.title == "临汐水文站"
        assert "补正受理地点" in entry.content
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_worldbuilding_update_rejects_foreign_or_missing_exact_id_before_staging():
    engine, db = database()
    try:
        project = Project(id="project-world-id", title="世界观 ID 校验")
        chapter = Chapter(
            id="chapter-world-id",
            project_id=project.id,
            title="第一章",
            content="正文。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]

        result = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "worldbuilding_update",
                "id": "missing-world-id",
                "title": "不存在的设定",
                "content": "不得写入。",
            },
            1,
        )

        assert result.get("bad_line")
        assert "目标 ID 不存在或不属于当前作品" in result["error"]
        assert db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_worldbuilding_update_and_timeline_reject_inactive_exact_ids_before_staging():
    engine, db = database()
    try:
        project = Project(id="project-world-inactive", title="停用世界观 ID 校验")
        chapter = Chapter(
            id="chapter-world-inactive",
            project_id=project.id,
            title="第一章",
            content="正文仍提到旧称，但该词条已由作者停用。",
        )
        entry = WorldbuildingEntry(
            id="retired-world-id",
            project_id=project.id,
            title="旧版错误地点",
            dimension="geography",
            content="已经停用的错误条目。",
            status="superseded",
        )
        db.add_all([project, chapter, entry])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]

        raws = [
            {
                "type": "worldbuilding_update",
                "id": entry.id,
                "title": entry.title,
                "content": "不得重新写入。",
            },
            {
                "type": "worldbuilding_timeline",
                "id": entry.id,
                "title": entry.title,
                "event_description": "不得挂接时间线。",
            },
        ]
        for sort_order, raw in enumerate(raws, 1):
            result = create_candidate_from_raw(db, job, run, raw, sort_order)
            assert result.get("bad_line")
            assert "目标 ID 已停用" in result["error"]

        assert db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_apply_boundary_never_reactivates_inactive_worldbuilding_ids():
    engine, db = database()
    try:
        project = Project(id="project-world-apply-inactive", title="应用时停用边界")
        chapter = Chapter(
            id="chapter-world-apply-inactive",
            project_id=project.id,
            title="第一章",
            content="绕过候选创建层的防御测试。",
        )
        entry = WorldbuildingEntry(
            id="retired-world-apply-id",
            project_id=project.id,
            title="旧版错误规则",
            dimension="culture",
            content="作者已经停用。",
            status="superseded",
        )
        db.add_all([project, chapter, entry])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        candidates = [
            candidate(
                db,
                job,
                run,
                chapter,
                "worldbuilding_update",
                {
                    "id": entry.id,
                    "title": entry.title,
                    "content": "绕过 staging 也不得恢复。",
                },
                1,
            ),
            candidate(
                db,
                job,
                run,
                chapter,
                "worldbuilding_timeline",
                {
                    "id": entry.id,
                    "title": entry.title,
                    "event_description": "绕过 staging 也不得挂接。",
                },
                2,
            ),
        ]

        events = apply_candidates_for_run(db, job, run)
        db.commit()
        db.refresh(entry)
        for row in candidates:
            db.refresh(row)
            assert row.status == "apply_failed"
            assert "不能重新激活" in (row.error or "")
        assert [event["type"] for event in events] == [
            "candidate_apply_failed",
            "candidate_apply_failed",
        ]
        assert entry.status == "superseded"
        assert entry.content == "作者已经停用。"
        assert db.query(WorldbuildingTimeline).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_incremental_worldbuilding_creates_with_exact_body_and_alias_titles_collapse():
    engine, db = database()
    try:
        project = Project(id="project-world-body", title="世界观正文去重")
        chapter = Chapter(
            id="chapter-world-body",
            project_id=project.id,
            title="空出的排期",
            content="周芷查到一条旧节目索引。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        content = (
            "临汐港口电台2015年4月12日播出的节目索引，来源单位列市档案馆、"
            "港务办公室，未解释18:50。"
        )

        first = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "worldbuilding_create",
                "title": "《临汐港务档案正式移交市档案馆》节目",
                "dimension": "history",
                "content": content,
                "status": "active",
            },
            1,
        )
        second = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "worldbuilding_create",
                "title": "《临汐港务档案正式移交市档案馆》",
                "dimension": "history",
                "content": content,
                "status": "active",
            },
            2,
        )

        assert first.get("candidate") is not None
        assert second.get("duplicate") is True
        rows = db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).all()
        assert len(rows) == 1
        payload = json.loads(rows[0].raw_payload)
        assert payload["title"] == "《临汐港务档案正式移交市档案馆》节目"
        assert rows[0].target_name == "《临汐港务档案正式移交市档案馆》节目"

        apply_candidates_for_run(db, job, run)
        db.commit()
        entries = db.query(WorldbuildingEntry).filter_by(project_id=project.id).all()
        assert len(entries) == 1
        assert entries[0].title == "《临汐港务档案正式移交市档案馆》节目"
        assert entries[0].content == content
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_scene_gap_reports_the_exact_scene_number_for_incremental_repair():
    engine, db = database()
    try:
        project = Project(id="project-1", title="场景补缺")
        chapter = Chapter(
            id="chapter-1",
            project_id=project.id,
            title="第一章",
            content="正文包含六个连续场景。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        base_rows(db, job, run, chapter, scene_count=6)
        for scene_number in range(1, 6):
            candidate(
                db,
                job,
                run,
                chapter,
                "outline_create",
                {
                    "node_type": "section",
                    "title": f"第一章 / 场景{scene_number}",
                    "summary": f"场景 {scene_number}",
                    "scene_number": scene_number,
                    "purpose": "推进情节",
                    "location": "山门",
                    "entry_state": "进入场景",
                    "exit_state": "离开场景",
                },
                scene_number + 1,
            )

        message = _candidate_coverage_error(db, run)

        assert "section outlines for declared scenes (5/6)" in message
        assert "缺少 section 场景编号：6" in message
        assert "缺少场景状态字段的 scene_number：6" in message
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_missing_section_numbers_are_normalized_before_identity_and_apply():
    engine, db = database()
    try:
        project = Project(id="project-scene-number", title="场景编号归一化")
        chapter = Chapter(
            id="chapter-scene-number",
            project_id=project.id,
            title="第三十八章·七分钟的位置",
            content="三个场景依次完成校准。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        raw_sections = [
            {
                "type": "outline_create",
                "node_type": "section",
                "title": f"第三十八章·七分钟的位置 / 场景{number}",
                "summary": f"第{number}个校准场景。",
                "purpose": "推进校准",
                "entry_state": "开始核对",
                "exit_state": "完成核对",
            }
            for number in range(1, 4)
        ]

        created = [
            create_candidate_from_raw(db, job, run, raw, index)
            for index, raw in enumerate(raw_sections, start=1)
        ]
        repeated = create_candidate_from_raw(db, job, run, raw_sections[0], 4)

        assert all(result.get("candidate") is not None for result in created)
        assert repeated.get("duplicate") is True
        rows = (
            db.query(CatalogingCandidate)
            .filter_by(chapter_run_id=run.id)
            .order_by(CatalogingCandidate.sort_order.asc())
            .all()
        )
        assert len(rows) == 3
        assert [json.loads(row.raw_payload)["scene_number"] for row in rows] == [1, 2, 3]

        apply_candidates_for_run(db, job, run)
        db.commit()
        sections = (
            db.query(OutlineNode)
            .filter_by(
                project_id=project.id,
                node_type="section",
                source_chapter_id=chapter.id,
            )
            .all()
        )
        assert sorted(row.metadata_json["scene_number"] for row in sections) == [1, 2, 3]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_candidate_coverage_rejects_more_sections_than_declared_scenes():
    engine, db = database()
    try:
        project = Project(id="project-scene-overflow", title="场景数量上限")
        chapter = Chapter(
            id="chapter-scene-overflow",
            project_id=project.id,
            title="第一章",
            content="正文。",
        )
        db.add_all([project, chapter])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        rows = base_rows(db, job, run, chapter, scene_count=2)
        for scene_number in range(1, 4):
            rows.append(candidate(
                db,
                job,
                run,
                chapter,
                "outline_create",
                {
                    "node_type": "section",
                    "title": f"第一章 / 场景{scene_number}",
                    "summary": f"场景 {scene_number}",
                    "scene_number": scene_number,
                    "purpose": "推进情节",
                    "location": "会议室",
                    "entry_state": "进入场景",
                    "exit_state": "离开场景",
                },
                scene_number + 1,
            ))

        coverage = inspect_candidate_coverage(rows, db=db, project_id=project.id)

        assert coverage.is_complete is False
        assert (
            "section outline candidates require unique scene_number within 1..2: "
            "out_of_range=3"
        ) in coverage.cli_parity_missing
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_current_facts_prevent_unnamed_role_from_binding_to_old_character():
    engine, db = database()
    try:
        project = Project(id="project-source-binding", title="改稿后的身份边界")
        chapter = Chapter(
            id="chapter-source-binding",
            project_id=project.id,
            title="空出的排期",
            content=(
                "栏目负责人把稿子递给周芷。周芷拒绝署名，栏目负责人收回稿子。"
                "她随后去找方敏核对旧便笺。"
            ),
        )
        db.add_all([
            project,
            chapter,
            Character(id="character-zhou", project_id=project.id, name="周芷"),
            Character(id="character-gu", project_id=project.id, name="顾志平"),
            Character(id="character-fang", project_id=project.id, name="方敏"),
        ])
        db.commit()
        job = create_cataloging_job(
            db,
            project.id,
            "auto",
            "deepseek:test",
            [chapter.id],
        )
        run = job.chapter_runs[0]
        db.add_all([
            CatalogingFact(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                fact_type="chapter_overview",
                raw_payload=json.dumps({
                    "characters": ["周芷", "方敏", "栏目负责人（未具名）"],
                    "cataloging_characters": ["周芷", "方敏"],
                    "anonymous_participants": ["栏目负责人"],
                    "cataloging_worldbuilding_titles": [],
                    "incidental_worldbuilding_mentions": [],
                }, ensure_ascii=False),
                status="active",
            ),
            CatalogingFact(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                fact_type="character_fact",
                raw_payload=json.dumps({
                    "primary_name": "周芷",
                    "names": ["周芷"],
                    "archive_identity": "stable_character",
                    "stable_profile_change": False,
                    "actions": ["拒绝署名"],
                }, ensure_ascii=False),
                status="active",
            ),
            CatalogingFact(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                fact_type="character_fact",
                raw_payload=json.dumps({
                    "primary_name": "方敏",
                    "names": ["方敏"],
                    "archive_identity": "stable_character",
                    "stable_profile_change": False,
                    "actions": ["被周芷提及为核对对象"],
                }, ensure_ascii=False),
                status="active",
            ),
            CatalogingFact(
                job_id=job.id,
                chapter_run_id=run.id,
                project_id=project.id,
                chapter_id=chapter.id,
                fact_type="character_fact",
                raw_payload=json.dumps({
                    "primary_name": "栏目负责人",
                    "names": ["栏目负责人"],
                    "archive_identity": "anonymous_role",
                    "stable_profile_change": False,
                    "actions": ["递稿并收回"],
                }, ensure_ascii=False),
                status="active",
            ),
        ])
        db.flush()

        summary = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "chapter_summary",
                **summary_payload(characters=["周芷", "顾志平", "方敏"]),
            },
            0,
        )
        stale_state = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "character_state_update",
                "name": "顾志平",
                "current_location": "电台办公室",
            },
            1,
        )
        current_state = create_candidate_from_raw(
            db,
            job,
            run,
            {
                "type": "character_state_update",
                "id": "character-zhou",
                "name": "周芷",
                "current_location": "电台办公室",
            },
            2,
        )

        assert summary.get("bad_line")
        assert "顾志平" in summary["error"]
        assert "不得把未具名角色绑定到旧档案人物" in summary["error"]
        assert stale_state.get("bad_line")
        assert "顾志平" in stale_state["error"]
        assert current_state.get("candidate") is not None
        rows = db.query(CatalogingCandidate).filter_by(chapter_run_id=run.id).all()
        assert [row.item_type for row in rows] == ["character_state_update"]
        assert json.loads(rows[0].raw_payload)["name"] == "周芷"
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
