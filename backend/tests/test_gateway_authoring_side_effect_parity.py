from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.utils import count_words
from app.database import models as _models  # noqa: F401
from app.database.session import Base
from app.modules.continuity.infrastructure.models import (
    CatalogingChapterRun,
    CatalogingJob,
    NarrativeCheckpoint,
)
from app.modules.gateway.application.contracts import SyncMutation
from app.modules.gateway.infrastructure.mutation_service import GatewayMutationApplier
from app.modules.gateway.infrastructure.service import GatewayService
from app.modules.story.infrastructure.chapters import SqlAlchemyChapterWorkspace
from app.modules.story.infrastructure.entities import (
    Chapter,
    ChapterSnapshot,
    Character,
    CharacterVersion,
    Project,
)
from app.services.cataloging.launcher import (
    AUTO_CHAPTER_WRITE_SOURCE,
    create_and_queue_cataloging_job,
)
from app.services.gateway_legacy_replication import apply_domain_mutation


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'authoring-side-effects.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_offline_existing_chapter_save_reuses_pc_snapshot_and_checkpoint_semantics(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="章节副作用契约")
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                title="第一章",
                content="旧正文",
                word_count=count_words("旧正文"),
                current_version=1,
                sort_order=1000,
            )
            db.add(chapter)
            db.commit()

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="chapter",
                entity_id=chapter.id,
                operation="upsert",
                payload={
                    "_record_type": "chapter",
                    "id": chapter.id,
                    "project_id": project.id,
                    "title": "第一章",
                    "outline_node_id": None,
                    "content": "新正文，来自手机离线编辑。",
                },
            )
            db.commit()

            db.refresh(chapter)
            assert chapter.current_version == 2
            assert chapter.word_count == count_words("新正文，来自手机离线编辑。")
            snapshots = (
                db.query(ChapterSnapshot)
                .filter(ChapterSnapshot.chapter_id == chapter.id)
                .order_by(ChapterSnapshot.version_number.asc())
                .all()
            )
            assert [item.version_number for item in snapshots] == [1, 2]
            assert snapshots[0].content == "旧正文"
            assert snapshots[1].content == "新正文，来自手机离线编辑。"

            checkpoints = (
                db.query(NarrativeCheckpoint)
                .filter(
                    NarrativeCheckpoint.project_id == project.id,
                    NarrativeCheckpoint.chapter_id == chapter.id,
                )
                .all()
            )
            assert len(checkpoints) == 1
            assert checkpoints[0].trigger_type == "manual_save"
            assert "v2" in checkpoints[0].label
    finally:
        engine.dispose()


def test_offline_existing_character_save_reuses_pc_character_version_semantics(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="角色副作用契约")
            db.add(project)
            db.flush()
            character = Character(
                project_id=project.id,
                name="陆糖",
                abilities=json.dumps(["推演"], ensure_ascii=False),
                current_version=1,
            )
            db.add(character)
            db.commit()

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="character",
                entity_id=character.id,
                operation="upsert",
                payload={
                    "_record_type": "character",
                    "id": character.id,
                    "project_id": project.id,
                    "name": "陆糖",
                    "abilities": ["推演", "阵法"],
                    "aliases": ["糖糖"],
                    "profile": {"core_motivation": "保护家人"},
                    "is_evolution_tracked": True,
                    "change_summary": "手机离线补充阵法能力",
                },
            )
            db.commit()

            db.refresh(character)
            assert character.current_version == 2
            versions = (
                db.query(CharacterVersion)
                .filter(CharacterVersion.character_id == character.id)
                .order_by(CharacterVersion.version_number.asc())
                .all()
            )
            assert len(versions) == 1
            assert versions[0].version_number == 2
            assert versions[0].change_summary == "手机离线补充阵法能力"
            snapshot = json.loads(versions[0].snapshot_data)
            assert snapshot["abilities"] == ["推演", "阵法"]
            assert snapshot["aliases"] == ["糖糖"]
            assert snapshot["profile"] == {"core_motivation": "保护家人"}
    finally:
        engine.dispose()


def test_mobile_replay_matches_pc_chapter_lifecycle_and_is_idempotent(tmp_path):
    """Compare canonical PC create with the Android outbox replay path.

    Identifiers differ by design, so the assertion compares the final domain
    state: chapter, snapshot, checkpoint, and durable cataloging job.  Replaying
    the same mutation ID must not create another version or job.
    """

    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            pc_project = Project(title="PC 写章路径")
            mobile_project = Project(title="Android 回放路径")
            db.add_all([pc_project, mobile_project])
            db.commit()

            title = "断线重连"
            content = "陆糖切断病毒网络，归墟阵纹随即亮起。"

            pc_result = SqlAlchemyChapterWorkspace(db).create(
                pc_project.id,
                {
                    "title": title,
                    "content": content,
                    "outline_node_id": None,
                },
            )
            db.commit()
            pc_chapter_id = pc_result.data["id"]
            create_and_queue_cataloging_job(
                db,
                pc_project.id,
                [pc_chapter_id],
                execution_mode="auto",
                backend_override="external_agent",
                provider_override="parity_test",
                trigger_source=AUTO_CHAPTER_WRITE_SOURCE,
                run_now=False,
            )

            mutation = SyncMutation(
                mutation_id="mobile-write-run-1",
                project_id=mobile_project.id,
                entity_type="chapter",
                entity_id="mobile-deterministic-chapter",
                operation="upsert",
                base_revision=0,
                payload={
                    "_record_type": "chapter",
                    "id": "mobile-deterministic-chapter",
                    "project_id": mobile_project.id,
                    "title": title,
                    "content": content,
                },
            )
            applier = GatewayMutationApplier(
                db,
                tombstone_retention_days=90,
                refresh_project_manifest=lambda _project_id: None,
            )
            applied = applier.apply(mutation, device_id="android-test")
            db.commit()
            assert applied.status == "applied"

            deferred = GatewayService(db).take_deferred_chapter_cataloging()
            assert deferred == [
                (mutation.mutation_id, mobile_project.id, mutation.entity_id),
            ]
            for _mutation_id, project_id, chapter_id in deferred:
                create_and_queue_cataloging_job(
                    db,
                    project_id,
                    [chapter_id],
                    execution_mode="auto",
                    backend_override="external_agent",
                    provider_override="parity_test",
                    trigger_source=AUTO_CHAPTER_WRITE_SOURCE,
                    run_now=False,
                )

            def final_state(project_id: str, chapter_id: str) -> dict:
                chapter = db.get(Chapter, chapter_id)
                assert chapter is not None
                snapshots = (
                    db.query(ChapterSnapshot)
                    .filter(ChapterSnapshot.chapter_id == chapter_id)
                    .order_by(ChapterSnapshot.version_number.asc())
                    .all()
                )
                checkpoints = (
                    db.query(NarrativeCheckpoint)
                    .filter(
                        NarrativeCheckpoint.project_id == project_id,
                        NarrativeCheckpoint.chapter_id == chapter_id,
                    )
                    .order_by(NarrativeCheckpoint.sequence.asc())
                    .all()
                )
                jobs = (
                    db.query(CatalogingJob)
                    .join(CatalogingChapterRun, CatalogingChapterRun.job_id == CatalogingJob.id)
                    .filter(
                        CatalogingJob.project_id == project_id,
                        CatalogingChapterRun.chapter_id == chapter_id,
                    )
                    .all()
                )
                return {
                    "chapter": {
                        "title": chapter.title,
                        "content": chapter.content,
                        "word_count": chapter.word_count,
                        "current_version": chapter.current_version,
                        "sort_order": chapter.sort_order,
                    },
                    "snapshots": [
                        (item.version_number, item.content, item.word_count, item.trigger_type)
                        for item in snapshots
                    ],
                    "checkpoints": [
                        (item.trigger_type, item.label.endswith(" 创建"))
                        for item in checkpoints
                    ],
                    "cataloging": [
                        (
                            item.status,
                            item.execution_mode,
                            item.execution_backend,
                            str(item.model_source or "").split(":", 1)[0],
                            item.total_chapters,
                        )
                        for item in jobs
                    ],
                }

            assert final_state(mobile_project.id, mutation.entity_id) == final_state(
                pc_project.id,
                pc_chapter_id,
            )

            before = final_state(mobile_project.id, mutation.entity_id)
            duplicate = applier.apply(mutation, device_id="android-test")
            db.commit()
            assert duplicate.status == "duplicate"
            assert GatewayService(db).take_deferred_chapter_cataloging() == []
            assert final_state(mobile_project.id, mutation.entity_id) == before
    finally:
        engine.dispose()
