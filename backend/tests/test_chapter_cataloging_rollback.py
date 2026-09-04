"""Regression coverage for chapter deletion and semantic-edit cataloging rollback."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import ValidationError
from app.database.models import (
    CatalogingApplyLog,
    CatalogingCandidate,
    CatalogingChapterRun,
    CatalogingJob,
    Chapter,
    ChapterCharacter,
    ChapterSummary,
    Character,
    CharacterVersion,
    NarrativeCheckpoint,
    Project,
    WorldbuildingEntry,
    WorldbuildingVersion,
)
from app.database.session import Base
from app.modules.story.infrastructure.chapters import SqlAlchemyChapterWorkspace
from app.services.cataloging.launcher import create_and_queue_cataloging_job
from app.services.cataloging.snapshots import character_snapshot, worldbuilding_snapshot


@pytest.fixture()
def db(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'rollback.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _chapter(project_id: str, chapter_id: str, order: int, title: str) -> Chapter:
    return Chapter(
        id=chapter_id,
        project_id=project_id,
        title=title,
        content=f"{title} 正文",
        word_count=10,
        current_version=1,
        cataloging_required=False,
        sort_order=order,
    )


def _run(
    db: Session,
    project_id: str,
    chapter: Chapter,
    index: int,
    *,
    item_type: str,
    target_type: str,
    target_id: str,
    old_value,
    new_value,
) -> CatalogingCandidate:
    job = CatalogingJob(
        id=f"job-{index}-{item_type}",
        project_id=project_id,
        status="completed",
        total_chapters=1,
        completed_chapters=1,
        context_integrity="clean",
        model_source=f"chapter_save:{chapter.id}",
    )
    db.add(job)
    db.flush()
    run = CatalogingChapterRun(
        id=f"run-{index}-{item_type}",
        job_id=job.id,
        project_id=project_id,
        chapter_id=chapter.id,
        status="completed",
        chapter_order=index,
        chapter_version=chapter.current_version,
    )
    db.add(run)
    db.flush()
    candidate = CatalogingCandidate(
        id=f"candidate-{index}-{item_type}",
        job_id=job.id,
        chapter_run_id=run.id,
        project_id=project_id,
        chapter_id=chapter.id,
        item_type=item_type,
        operation="upsert",
        target_type=target_type,
        target_id=target_id,
        raw_payload="{}",
        status="applied",
    )
    db.add(candidate)
    db.flush()
    db.add(
        CatalogingApplyLog(
            id=f"log-{index}-{item_type}",
            job_id=job.id,
            chapter_run_id=run.id,
            candidate_id=candidate.id,
            target_type=target_type,
            target_id=target_id,
            operation="upsert",
            old_value=(
                json.dumps(old_value, ensure_ascii=False)
                if old_value is not None
                else None
            ),
            new_value=(
                json.dumps(new_value, ensure_ascii=False)
                if new_value is not None
                else None
            ),
        )
    )
    db.flush()
    return candidate


def _seed_cataloged_suffix(db: Session):
    project = Project(id="p1", title="回退测试")
    first = _chapter("p1", "c1", 1000, "第一章")
    second = _chapter("p1", "c2", 2000, "第二章")
    third = _chapter("p1", "c3", 3000, "第三章")
    db.add_all([project, first, second, third])
    db.flush()

    existing = Character(
        id="existing-character",
        project_id="p1",
        name="旧角色",
        current_goal="出发前",
        current_version=1,
    )
    db.add(existing)
    db.flush()
    before_second = character_snapshot(existing)
    existing.current_goal = "第二章目标"
    after_second = character_snapshot(existing)
    _run(
        db,
        "p1",
        second,
        2,
        item_type="character_update",
        target_type="character",
        target_id=existing.id,
        old_value=before_second,
        new_value=after_second,
    )
    db.add(
        CharacterVersion(
            id="existing-v2",
            character_id=existing.id,
            version_number=2,
            snapshot_data=json.dumps(after_second, ensure_ascii=False),
            source_chapter_id=second.id,
        )
    )

    existing.current_goal = "第三章目标"
    after_third = character_snapshot(existing)
    _run(
        db,
        "p1",
        third,
        3,
        item_type="character_update",
        target_type="character",
        target_id=existing.id,
        old_value=after_second,
        new_value=after_third,
    )
    db.add(
        CharacterVersion(
            id="existing-v3",
            character_id=existing.id,
            version_number=3,
            snapshot_data=json.dumps(after_third, ensure_ascii=False),
            source_chapter_id=third.id,
        )
    )

    new_character = Character(
        id="new-character",
        project_id="p1",
        name="第二章新角色",
        current_goal="首次登场",
        current_version=1,
    )
    db.add(new_character)
    db.flush()
    new_character_snapshot = character_snapshot(new_character)
    _run(
        db,
        "p1",
        second,
        20,
        item_type="character_create",
        target_type="character",
        target_id=new_character.id,
        old_value=None,
        new_value=new_character_snapshot,
    )
    db.add(
        CharacterVersion(
            id="new-character-v1",
            character_id=new_character.id,
            version_number=1,
            snapshot_data=json.dumps(new_character_snapshot, ensure_ascii=False),
            source_chapter_id=second.id,
        )
    )

    new_world = WorldbuildingEntry(
        id="new-world",
        project_id="p1",
        dimension="location",
        title="第二章新地点",
        content="只在第二章出现",
        first_seen_chapter_id=second.id,
        last_updated_chapter_id=second.id,
    )
    db.add(new_world)
    db.flush()
    world_snapshot = worldbuilding_snapshot(new_world)
    _run(
        db,
        "p1",
        second,
        21,
        item_type="worldbuilding_create",
        target_type="worldbuilding",
        target_id=new_world.id,
        old_value=None,
        new_value=world_snapshot,
    )
    db.add(
        WorldbuildingVersion(
            id="new-world-v1",
            entry_id=new_world.id,
            version_number=1,
            snapshot_data=json.dumps(world_snapshot, ensure_ascii=False),
            source_chapter_id=second.id,
        )
    )

    db.add_all(
        [
            ChapterSummary(
                id="summary-2",
                chapter_id=second.id,
                summary_text="第二章摘要",
            ),
            ChapterSummary(
                id="summary-3",
                chapter_id=third.id,
                summary_text="第三章摘要",
            ),
            ChapterCharacter(
                id="appearance-2",
                chapter_id=second.id,
                character_id=new_character.id,
                appearance_type="出场",
            ),
            NarrativeCheckpoint(
                id="checkpoint-project",
                project_id="p1",
                chapter_id=None,
                chapter_snapshot_id=None,
                sequence=1,
                label="项目级检查点",
                state_json={},
            ),
            NarrativeCheckpoint(
                id="checkpoint-1",
                project_id="p1",
                chapter_id=first.id,
                chapter_snapshot_id=None,
                sequence=2,
                label="第一章",
                state_json={},
            ),
            NarrativeCheckpoint(
                id="checkpoint-2",
                project_id="p1",
                chapter_id=second.id,
                chapter_snapshot_id=None,
                sequence=3,
                label="第二章",
                state_json={},
            ),
            NarrativeCheckpoint(
                id="checkpoint-3",
                project_id="p1",
                chapter_id=third.id,
                chapter_snapshot_id=None,
                sequence=4,
                label="第三章",
                state_json={},
            ),
        ]
    )
    db.commit()
    return first, second, third, existing, new_character, new_world


def test_delete_middle_chapter_rolls_back_suffix_and_preserves_project_checkpoint(
    db: Session,
):
    _first, second, third, existing, new_character, new_world = (
        _seed_cataloged_suffix(db)
    )

    mutation = SqlAlchemyChapterWorkspace(db).delete("p1", second.id)
    db.commit()

    assert db.get(Chapter, second.id) is None
    assert db.get(Chapter, third.id) is not None
    assert db.get(Chapter, third.id).cataloging_required is True
    assert db.get(Character, existing.id).current_goal == "出发前"
    assert db.get(Character, new_character.id) is None
    assert db.get(WorldbuildingEntry, new_world.id) is None
    assert (
        db.query(ChapterSummary)
        .filter(ChapterSummary.chapter_id == third.id)
        .count()
        == 0
    )
    assert {
        row.id
        for row in db.query(NarrativeCheckpoint)
        .order_by(NarrativeCheckpoint.sequence)
        .all()
    } == {"checkpoint-project", "checkpoint-1"}
    assert mutation.data["recatalog_required_chapter_ids"] == [third.id]


def test_semantic_edit_keeps_ids_but_rolls_back_current_and_later_cataloging(
    db: Session,
):
    _first, second, third, existing, new_character, new_world = (
        _seed_cataloged_suffix(db)
    )

    mutation = SqlAlchemyChapterWorkspace(db).save(
        "p1",
        second.id,
        {
            "content": "第二章剧情发生变化",
            "expected_version": 1,
            "cataloging_impact": "semantic",
        },
    )
    db.commit()

    assert db.get(Chapter, second.id) is not None
    assert db.get(Chapter, third.id) is not None
    assert db.get(Chapter, second.id).cataloging_required is True
    assert db.get(Chapter, third.id).cataloging_required is True
    assert db.get(Character, existing.id).current_goal == "出发前"
    assert db.get(Character, new_character.id) is None
    assert db.get(WorldbuildingEntry, new_world.id) is None
    assert mutation.data["recatalog_required_chapter_ids"] == [second.id, third.id]
    checkpoints = db.query(NarrativeCheckpoint).all()
    assert any(
        row.chapter_id == second.id and row.trigger_type == "manual_save"
        for row in checkpoints
    )
    assert not any(row.chapter_id == third.id for row in checkpoints)


def test_semantic_rollback_never_reuses_invalidated_completed_runs(db: Session):
    _first, second, third, _existing, _new_character, _new_world = (
        _seed_cataloged_suffix(db)
    )

    SqlAlchemyChapterWorkspace(db).save(
        "p1",
        second.id,
        {
            "content": "第二章改写后需要重新建档",
            "expected_version": 1,
            "cataloging_impact": "semantic",
        },
    )
    db.commit()

    job, launch = create_and_queue_cataloging_job(
        db,
        "p1",
        [second.id, third.id],
        backend_override="external_agent",
        trigger_source="manual",
        run_now=False,
    )

    assert launch["idempotent_reuse"] is False
    assert launch["already_cataloged_chapter_ids"] == []
    assert launch["queued_chapter_ids"] == [second.id, third.id]
    assert {
        row.chapter_id
        for row in db.query(CatalogingChapterRun).filter_by(job_id=job.id).all()
    } == {second.id, third.id}


def test_style_only_edit_preserves_cataloging_projection(db: Session):
    _first, second, third, existing, new_character, new_world = (
        _seed_cataloged_suffix(db)
    )

    mutation = SqlAlchemyChapterWorkspace(db).save(
        "p1",
        second.id,
        {
            "content": "第二章仅调整句式与措辞",
            "expected_version": 1,
            "cataloging_impact": "style_only",
        },
    )
    db.commit()

    assert db.get(Chapter, second.id).cataloging_required is False
    assert db.get(Chapter, third.id).cataloging_required is False
    assert db.get(Character, existing.id).current_goal == "第三章目标"
    assert db.get(Character, new_character.id) is not None
    assert db.get(WorldbuildingEntry, new_world.id) is not None
    assert (
        db.query(ChapterSummary)
        .filter(ChapterSummary.chapter_id == third.id)
        .count()
        == 1
    )
    assert mutation.data["cataloging_impact"] == "style_only"
    assert mutation.data["cataloging_rollback"] is None


def test_style_only_cannot_change_outline_or_title(db: Session):
    _first, second, _third, _existing, _new_character, _new_world = (
        _seed_cataloged_suffix(db)
    )

    with pytest.raises(ValidationError, match="仅润色模式"):
        SqlAlchemyChapterWorkspace(db).save(
            "p1",
            second.id,
            {
                "title": "改名后的第二章",
                "cataloging_impact": "style_only",
            },
        )
