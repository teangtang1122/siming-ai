from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.database.bootstrap import alembic_config


def test_migration_keeps_latest_directed_pair_and_quarantines_older_rows(tmp_path):
    database_path = tmp_path / "relationships.db"
    url = f"sqlite:///{database_path.as_posix()}"
    config = alembic_config(url)
    # Fresh databases are created from current metadata before Alembic stamps
    # the baseline, so rehearse a real legacy shape by upgrading once and then
    # downgrading this constraint-only revision.
    command.upgrade(config, "300a35_relationship_integrity")
    command.downgrade(config, "300a34_canonical_model_identity")
    engine = create_engine(url)

    with engine.begin() as connection:
        connection.execute(
            text('INSERT INTO projects (id, title, created_at, updated_at) VALUES (:id, :title, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)'),
            {"id": "project-1", "title": "关系迁移"},
        )
        for character_id, name in (("character-a", "甲"), ("character-b", "乙")):
            connection.execute(
                text(
                    'INSERT INTO characters (id, project_id, name, current_version, created_at, updated_at) '
                    'VALUES (:id, :project_id, :name, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)'
                ),
                {"id": character_id, "project_id": "project-1", "name": name},
            )
        connection.execute(
            text(
                'INSERT INTO character_relationships '
                '(id, project_id, character_a_id, character_b_id, relationship_type, description, created_at) '
                'VALUES (:id, :project_id, :a, :b, :kind, :description, :created_at)'
            ),
            [
                {
                    "id": "older",
                    "project_id": "project-1",
                    "a": "character-a",
                    "b": "character-b",
                    "kind": "协作",
                    "description": "旧描述",
                    "created_at": "2026-01-01 00:00:00",
                },
                {
                    "id": "newer",
                    "project_id": "project-1",
                    "a": "character-a",
                    "b": "character-b",
                    "kind": "调查搭档",
                    "description": "新描述",
                    "created_at": "2026-01-02 00:00:00",
                },
            ],
        )

    command.upgrade(config, "300a35_relationship_integrity")

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, relationship_type, description FROM character_relationships "
                "ORDER BY id"
            )
        ).mappings().all()
        assert [dict(row) for row in rows] == [
            {"id": "newer", "relationship_type": "调查搭档", "description": "新描述"}
        ]
        quarantined = connection.execute(
            text(
                "SELECT source_id, reason, payload_json FROM data_integrity_quarantine "
                "WHERE migration_revision = '300a35_relationship_integrity'"
            )
        ).mappings().one()
        assert quarantined["source_id"] == "older"
        assert "survivor=newer" in quarantined["reason"]
        assert json.loads(quarantined["payload_json"])["relationship_type"] == "协作"

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    'INSERT INTO character_relationships '
                    '(id, project_id, character_a_id, character_b_id, relationship_type, created_at) '
                    "VALUES ('third', 'project-1', 'character-a', 'character-b', '合作', CURRENT_TIMESTAMP)"
                )
            )
    engine.dispose()
