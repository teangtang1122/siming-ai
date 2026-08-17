from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import models as _models  # noqa: F401
from app.database.session import Base
from app.modules.story.infrastructure.entities import Character, Project
from app.services.gateway_legacy_replication import (
    apply_domain_mutation,
    domain_snapshot_for_entity,
)


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'character-role-sync.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_offline_character_role_uses_same_normalization_and_identity_preservation_as_pc(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="角色定位契约")
            db.add(project)
            db.commit()

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="character",
                entity_id="character-role-mobile",
                operation="upsert",
                payload={
                    "_record_type": "character",
                    "id": "character-role-mobile",
                    "project_id": project.id,
                    "name": "陆糖",
                    "role_type": "主角，穿越者，陆家三岁孙女",
                    "background": "华清实验室天才少女转生。",
                    "abilities": [],
                    "aliases": [],
                    "profile": {},
                    "is_evolution_tracked": True,
                },
            )
            db.commit()

            character = db.get(Character, "character-role-mobile")
            assert character is not None
            assert character.role_type == "protagonist"
            assert "华清实验室天才少女转生。" in (character.background or "")
            assert "身份补充：穿越者、陆家三岁孙女" in (character.background or "")

            snapshot = domain_snapshot_for_entity(
                db,
                project_id=project.id,
                entity_type="character",
                entity_id=character.id,
            )
            assert snapshot is not None
            assert snapshot["role_type"] == "protagonist"
            assert snapshot["background"] == character.background
            assert "身份补充：穿越者、陆家三岁孙女" in (snapshot["background"] or "")
    finally:
        engine.dispose()


def test_mobile_created_character_without_role_defaults_to_pc_other_role(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="角色默认定位契约")
            db.add(project)
            db.commit()

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="character",
                entity_id="character-without-role",
                operation="upsert",
                payload={
                    "_record_type": "character",
                    "id": "character-without-role",
                    "project_id": project.id,
                    "name": "小七",
                    "abilities": [],
                    "aliases": [],
                    "profile": {},
                    "is_evolution_tracked": True,
                },
            )
            db.commit()

            character = db.get(Character, "character-without-role")
            assert character is not None
            assert character.role_type == "other"
            snapshot = domain_snapshot_for_entity(
                db,
                project_id=project.id,
                entity_type="character",
                entity_id=character.id,
            )
            assert snapshot is not None
            assert snapshot["role_type"] == "other"
    finally:
        engine.dispose()
