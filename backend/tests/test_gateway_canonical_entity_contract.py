from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ValidationError
from app.core.utils import count_words
from app.database import models as _models  # noqa: F401
from app.database.session import Base
from app.modules.gateway.application.contracts import SyncMutation
from app.modules.gateway.infrastructure.models import SyncEntityState
from app.modules.gateway.infrastructure.mutation_service import GatewayMutationApplier
from app.modules.story.infrastructure.entities import (
    Chapter,
    Character,
    CharacterAlias,
    OutlineNode,
    Project,
)
from app.services.gateway_legacy_replication import (
    apply_domain_mutation,
    domain_snapshot_for_entity,
    serialize_record,
)


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'canonical-sync.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_character_sync_snapshot_uses_same_public_shape_as_pc_character_api(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="契约测试")
            db.add(project)
            db.flush()
            character = Character(
                project_id=project.id,
                name="陆糖",
                abilities=json.dumps(["阵法", "推演"], ensure_ascii=False),
                profile_json={"core_motivation": "保护家人", "voice": "克制直接"},
            )
            db.add(character)
            db.flush()
            db.add_all([
                CharacterAlias(character_id=character.id, project_id=project.id, alias="糖糖"),
                CharacterAlias(character_id=character.id, project_id=project.id, alias="特昂糖"),
            ])
            db.commit()

            payload = serialize_record(character)

            assert payload["_record_type"] == "character"
            assert payload["name"] == "陆糖"
            assert payload["abilities"] == ["阵法", "推演"]
            assert payload["aliases"] == ["糖糖", "特昂糖"]
            assert payload["profile"] == {"core_motivation": "保护家人", "voice": "克制直接"}
            assert "profile_json" not in payload
    finally:
        engine.dispose()


def test_character_mobile_mutation_round_trips_public_arrays_and_profile(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="离线回传测试")
            db.add(project)
            db.commit()

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="character",
                entity_id="character-mobile",
                operation="upsert",
                payload={
                    "_record_type": "character",
                    "name": "陆景珩",
                    "role_type": "supporting",
                    "abilities": ["剑术", "新吐纳法"],
                    "aliases": ["景珩", "兄长"],
                    "profile": {"core_belief": "家人优先", "action_habit": "先观察再拔剑"},
                    # These are present in CharacterResponse but are not writable
                    # in the PC CharacterCreate/CharacterUpdate contract.
                    "current_version": 999,
                    "created_at": "2000-01-01T00:00:00Z",
                    "updated_at": "2000-01-01T00:00:00Z",
                },
            )
            db.commit()

            character = db.get(Character, "character-mobile")
            assert character is not None
            assert json.loads(character.abilities or "[]") == ["剑术", "新吐纳法"]
            assert character.profile_json == {
                "core_belief": "家人优先",
                "action_habit": "先观察再拔剑",
            }
            assert [item.alias for item in character.aliases] == ["景珩", "兄长"]
            assert character.current_version == 1
            assert character.created_at.year != 2000
            assert character.updated_at.year != 2000
    finally:
        engine.dispose()


def test_project_mobile_mutation_accepts_pc_tags_but_ignores_local_and_response_fields(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project_id = "project-mobile"
            apply_domain_mutation(
                db,
                project_id=project_id,
                entity_type="project",
                entity_id=project_id,
                operation="upsert",
                payload={
                    "_record_type": "project",
                    "title": "归墟测试",
                    "tags": ["东方玄幻", "轻科幻"],
                    "daily_word_goal": 8000,
                    "folder_path": "D:/should-not-be-written",
                    "storage_mode": "malicious",
                    "created_at": "2000-01-01T00:00:00Z",
                },
            )
            db.commit()

            project = db.get(Project, project_id)
            assert project is not None
            assert json.loads(project.tags or "[]") == ["东方玄幻", "轻科幻"]
            assert project.daily_word_goal == 8000
            assert project.folder_path != "D:/should-not-be-written"
            assert project.storage_mode != "malicious"
            assert project.created_at.year != 2000
    finally:
        engine.dispose()


def test_chapter_mobile_mutation_recomputes_pc_derived_fields_and_keeps_server_order(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="章节契约测试")
            db.add(project)
            db.commit()

            content = "陆糖推开石门。阵纹重新亮起。"
            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="chapter",
                entity_id="chapter-mobile",
                operation="upsert",
                payload={
                    "_record_type": "chapter",
                    "title": "第一章",
                    "content": content,
                    "word_count": 999,
                    "current_version": 88,
                    "sort_order": 777,
                    "quality_score": 100,
                    "created_at": "2000-01-01T00:00:00Z",
                },
            )
            db.commit()

            chapter = db.get(Chapter, "chapter-mobile")
            assert chapter is not None
            assert chapter.word_count == count_words(content)
            assert chapter.current_version == 1
            assert chapter.sort_order != 777
            assert chapter.created_at.year != 2000
            snapshot = domain_snapshot_for_entity(
                db,
                project_id=project.id,
                entity_type="chapter",
                entity_id=chapter.id,
            )
            assert snapshot is not None
            assert snapshot["word_count"] == count_words(content)
            assert snapshot["current_version"] == 1
            assert snapshot["content"] == content
    finally:
        engine.dispose()


def test_outline_mobile_mutation_maps_pc_metadata_and_character_links(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="大纲契约测试")
            db.add(project)
            db.flush()
            character = Character(project_id=project.id, name="陆糖")
            db.add(character)
            db.flush()

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="outline",
                entity_id="outline-mobile",
                operation="upsert",
                payload={
                    "_record_type": "outline_node",
                    "title": "归墟待敌",
                    "node_type": "chapter",
                    "status": "pending",
                    "sort_order": 2000,
                    "metadata": {"hook": "裂隙亮起"},
                    "characters": [
                        {"character_id": character.id, "role_in_scene": "protagonist"},
                    ],
                    "created_at": "2000-01-01T00:00:00Z",
                },
            )
            db.commit()

            node = db.get(OutlineNode, "outline-mobile")
            assert node is not None
            assert node.metadata_json == {"hook": "裂隙亮起"}
            snapshot = domain_snapshot_for_entity(
                db,
                project_id=project.id,
                entity_type="outline",
                entity_id=node.id,
            )
            assert snapshot is not None
            assert snapshot["metadata"] == {"hook": "裂隙亮起"}
            assert "metadata_json" not in snapshot
            assert snapshot["linked_characters"] == [
                {
                    "id": character.id,
                    "name": "陆糖",
                    "role_type": None,
                    "role_in_scene": "protagonist",
                }
            ]
    finally:
        engine.dispose()


def test_mobile_cannot_write_server_managed_history_rows(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="只读记录测试")
            db.add(project)
            db.flush()
            character = Character(project_id=project.id, name="陆糖")
            db.add(character)
            db.commit()

            with pytest.raises(ValidationError, match="移动端只读"):
                apply_domain_mutation(
                    db,
                    project_id=project.id,
                    entity_type="character",
                    entity_id="character-version-mobile",
                    operation="upsert",
                    payload={
                        "_record_type": "character_version",
                        "character_id": character.id,
                        "version_number": 2,
                        "snapshot_data": "{}",
                    },
                )
    finally:
        engine.dispose()


def test_gateway_state_persists_canonical_pc_snapshot_not_client_request(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="同步状态契约测试")
            db.add(project)
            db.commit()

            content = "这是从手机离线队列回传的正文。"
            result = GatewayMutationApplier(
                db,
                tombstone_retention_days=90,
                refresh_project_manifest=lambda _project_id: None,
            ).apply(
                SyncMutation(
                    mutation_id="mobile-chapter-1",
                    project_id=project.id,
                    entity_type="chapter",
                    entity_id="chapter-state",
                    operation="upsert",
                    base_revision=0,
                    payload={
                        "_record_type": "chapter",
                        "id": "chapter-state",
                        "project_id": project.id,
                        "title": "离线章节",
                        "content": content,
                    },
                ),
                device_id=None,
            )
            db.commit()

            assert result.status == "applied"
            state = (
                db.query(SyncEntityState)
                .filter(
                    SyncEntityState.project_id == project.id,
                    SyncEntityState.entity_type == "chapter",
                    SyncEntityState.entity_id == "chapter-state",
                )
                .one()
            )
            assert state.payload_json["_record_type"] == "chapter"
            assert state.payload_json["title"] == "离线章节"
            assert state.payload_json["content"] == content
            assert state.payload_json["word_count"] == count_words(content)
            assert state.payload_json["current_version"] == 1
            assert "snapshot_count" in state.payload_json
    finally:
        engine.dispose()
