from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import ValidationError
from app.database import models as _models  # noqa: F401
from app.database.session import Base
from app.modules.story.infrastructure.entities import (
    Character,
    CharacterAIConfig,
    CharacterRelationship,
    Project,
    WorldbuildingEntry,
    WorldbuildingRelation,
)
from app.services.gateway_legacy_replication import (
    apply_domain_mutation,
    domain_snapshot_for_entity,
    project_snapshots,
)


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'gateway-auxiliary-contract.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_auxiliary_relationship_and_ai_config_contracts_round_trip(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="辅助结构同步")
            db.add(project)
            db.flush()
            a = Character(project_id=project.id, name="陆糖", role_type="protagonist")
            b = Character(project_id=project.id, name="陆承宇", role_type="supporting")
            w1 = WorldbuildingEntry(
                project_id=project.id,
                dimension="power_system",
                title="归墟",
                content="锁时空并切断网络",
            )
            w2 = WorldbuildingEntry(
                project_id=project.id,
                dimension="factions",
                title="青云宗",
                content="修仙宗门",
            )
            db.add_all([a, b, w1, w2])
            db.flush()
            config = CharacterAIConfig(
                character_id=a.id,
                tone_style="冷静",
                catchphrases='["先验证", "数据说话"]',
                verbosity="brief",
                emotion_tendency="克制",
            )
            db.add(config)
            db.commit()

            snapshots = [payload for _spec, _row, payload in project_snapshots(db, project.id)]
            ai_snapshot = next(
                row for row in snapshots if row.get("_record_type") == "character_ai_config"
            )
            assert ai_snapshot["character_id"] == a.id
            assert ai_snapshot["catchphrases"] == ["先验证", "数据说话"]

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="character_relation",
                entity_id="relation-mobile",
                operation="upsert",
                payload={
                    "_record_type": "character_relationship",
                    "id": "relation-mobile",
                    "project_id": project.id,
                    "from": a.id,
                    "to": b.id,
                    "relationship_type": "父女",
                    "description": "互相信任",
                },
            )
            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="character_ai_config",
                entity_id=config.id,
                operation="upsert",
                payload={
                    "_record_type": "character_ai_config",
                    "id": config.id,
                    "project_id": project.id,
                    "character_id": a.id,
                    "tone_style": "理工式冷静",
                    "catchphrases": ["先验证", "别猜"],
                    "verbosity": "moderate",
                    "emotion_tendency": "克制",
                    "model_override": None,
                    "custom_system_prompt": "避免无依据判断",
                },
            )
            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="world_relation",
                entity_id="world-relation-mobile",
                operation="upsert",
                payload={
                    "_record_type": "world_relationship",
                    "id": "world-relation-mobile",
                    "project_id": project.id,
                    "source_entry_id": w1.id,
                    "target_entry_id": w2.id,
                    "relation_type": "constrained_by",
                    "description": "宗门负责维持阵法",
                    "metadata_json": {"strength": "high"},
                },
            )
            db.commit()

            relation = db.get(CharacterRelationship, "relation-mobile")
            assert relation is not None
            assert relation.character_a_id == a.id
            assert relation.character_b_id == b.id
            assert relation.relationship_type == "父女"

            db.refresh(config)
            assert config.tone_style == "理工式冷静"
            assert config.catchphrases == '["先验证", "别猜"]'
            ai_snapshot = domain_snapshot_for_entity(
                db,
                project_id=project.id,
                entity_type="character_ai_config",
                entity_id=config.id,
            )
            assert ai_snapshot is not None
            assert ai_snapshot["catchphrases"] == ["先验证", "别猜"]

            world_relation = db.get(WorldbuildingRelation, "world-relation-mobile")
            assert world_relation is not None
            assert world_relation.metadata_json == {"strength": "high"}
    finally:
        engine.dispose()


def test_character_relation_rejects_self_link(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="关系校验")
            db.add(project)
            db.flush()
            character = Character(project_id=project.id, name="陆糖")
            db.add(character)
            db.commit()

            with pytest.raises(ValidationError, match="自身"):
                apply_domain_mutation(
                    db,
                    project_id=project.id,
                    entity_type="character_relation",
                    entity_id="self-link",
                    operation="upsert",
                    payload={
                        "_record_type": "character_relationship",
                        "id": "self-link",
                        "project_id": project.id,
                        "from": character.id,
                        "to": character.id,
                        "relationship_type": "self",
                    },
                )
    finally:
        engine.dispose()


def test_character_ai_config_cannot_move_to_another_character(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="AI 配置归属校验")
            db.add(project)
            db.flush()
            original = Character(project_id=project.id, name="陆糖")
            other = Character(project_id=project.id, name="小七")
            db.add_all([original, other])
            db.flush()
            config = CharacterAIConfig(character_id=original.id, tone_style="冷静")
            db.add(config)
            db.commit()

            with pytest.raises(ValidationError, match="不能移动"):
                apply_domain_mutation(
                    db,
                    project_id=project.id,
                    entity_type="character_ai_config",
                    entity_id=config.id,
                    operation="upsert",
                    payload={
                        "_record_type": "character_ai_config",
                        "id": config.id,
                        "project_id": project.id,
                        "character_id": other.id,
                        "tone_style": "活泼",
                    },
                )

            db.rollback()
            db.refresh(config)
            assert config.character_id == original.id
            assert config.tone_style == "冷静"
    finally:
        engine.dispose()
