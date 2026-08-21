"""Regression tests for cumulative cataloging completeness repair."""
from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Base,
    CatalogingCandidate,
    Chapter,
    Project,
    WorldbuildingEntry,
)
from app.services.cataloging.candidate_validation import (
    candidate_coverage_error_message,
    candidate_coverage_should_retry,
    inspect_candidate_coverage,
)
from app.services.cataloging.fact_store import clear_candidates_for_run
from app.services.cataloging.orchestrator import create_cataloging_job


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


def test_retry_clear_keeps_valid_staged_candidates():
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
        candidate(
            db,
            job,
            run,
            chapter,
            "chapter_summary",
            summary_payload(),
        )
        run.status = "extracting"
        db.flush()

        clear_candidates_for_run(db, run)
        db.flush()

        assert (
            db.query(CatalogingCandidate)
            .filter(CatalogingCandidate.chapter_run_id == run.id)
            .count()
            == 1
        )
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
