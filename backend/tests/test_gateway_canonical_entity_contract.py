from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import models as _models  # noqa: F401
from app.database.session import Base
from app.modules.story.infrastructure.entities import Character, CharacterAlias, Project
from app.services.gateway_legacy_replication import apply_domain_mutation, serialize_record


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
