from __future__ import annotations

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.models import Base, Character, CharacterRelationship, Project
from app.services.workspace.tools.relationships import create_relationship


def test_create_relationship_is_pair_upsert_instead_of_duplicate_insert():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        project = Project(id="project-1", title="关系工具")
        source = Character(id="character-a", project_id=project.id, name="甲")
        target = Character(id="character-b", project_id=project.id, name="乙")
        db.add_all([project, source, target])
        db.commit()

        first = asyncio.run(create_relationship(db, project.id, {
            "source": "甲",
            "target": "乙",
            "relationship_type": "协作",
            "description": "临时协作。",
        }))
        second = asyncio.run(create_relationship(db, project.id, {
            "source": "甲",
            "target": "乙",
            "relationship_type": "调查搭档",
            "description": "共同核验证据。",
        }))
        db.commit()

        rows = db.query(CharacterRelationship).all()
        assert len(rows) == 1
        assert rows[0].relationship_type == "调查搭档"
        assert rows[0].description == "共同核验证据。"
        assert first["data"]["created"] is True
        assert second["data"]["created"] is False
        assert first["data"]["relationship_id"] == second["data"]["relationship_id"]
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
