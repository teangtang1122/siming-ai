from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.utils import count_words
from app.database import models as _models  # noqa: F401
from app.database.session import Base
from app.modules.continuity.infrastructure.models import NarrativeCheckpoint
from app.modules.story.infrastructure.entities import (
    Chapter,
    ChapterSnapshot,
    Character,
    CharacterVersion,
    Project,
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
