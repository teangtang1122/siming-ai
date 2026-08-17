from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import models as _models  # noqa: F401
from app.database.session import Base
from app.modules.continuity.infrastructure.models import Foreshadowing, NarrativeGovernanceEvent
from app.modules.story.infrastructure.entities import Chapter, Project
from app.services.gateway_legacy_replication import apply_domain_mutation


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'governance-sync.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_offline_governance_replay_uses_same_review_then_close_lifecycle_as_pc(tmp_path):
    engine, Session = _session(tmp_path)
    try:
        with Session() as db:
            project = Project(title="治理同步契约")
            db.add(project)
            db.flush()
            chapter = Chapter(project_id=project.id, title="第一百五十章", content="伏笔在这里兑现。")
            db.add(chapter)
            db.commit()

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="foreshadowing",
                entity_id="foreshadow-mobile",
                operation="upsert",
                payload={
                    "_record_type": "foreshadowing",
                    "id": "foreshadow-mobile",
                    "project_id": project.id,
                    "title": "传道石伏笔",
                    "description": "后续兑现",
                    "importance": "high",
                    "status": "open",
                    "dedupe_key": "mobile-foreshadow-mobile",
                    "source": "manual",
                },
            )
            db.commit()

            row = db.get(Foreshadowing, "foreshadow-mobile")
            assert row is not None
            assert row.status == "open"

            # The PC lifecycle never allows open -> fulfilled directly. Offline
            # replay must reject the same shortcut instead of writing status
            # directly into the SQLAlchemy row.
            with pytest.raises(ValueError, match="必须先提交复检"):
                apply_domain_mutation(
                    db,
                    project_id=project.id,
                    entity_type="foreshadowing",
                    entity_id=row.id,
                    operation="upsert",
                    payload={
                        "_record_type": "foreshadowing",
                        "id": row.id,
                        "project_id": project.id,
                        "title": row.title,
                        "status": "fulfilled",
                        "resolved_chapter_id": chapter.id,
                        "resolution_note": "伏笔已经正式兑现",
                        "verification_note": "人工复检确认兑现",
                    },
                )
            db.rollback()

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="foreshadowing",
                entity_id=row.id,
                operation="upsert",
                payload={
                    "_record_type": "foreshadowing",
                    "id": row.id,
                    "project_id": project.id,
                    "title": row.title,
                    "status": "pending_review",
                    "resolved_chapter_id": chapter.id,
                    "resolution_note": "伏笔已经正式兑现",
                },
            )
            db.commit()

            row = db.get(Foreshadowing, row.id)
            assert row.status == "pending_review"
            assert row.resolved_chapter_id == chapter.id
            assert row.resolved_chapter_version == 1

            apply_domain_mutation(
                db,
                project_id=project.id,
                entity_type="foreshadowing",
                entity_id=row.id,
                operation="upsert",
                payload={
                    "_record_type": "foreshadowing",
                    "id": row.id,
                    "project_id": project.id,
                    "title": row.title,
                    "status": "fulfilled",
                    "resolved_chapter_id": chapter.id,
                    "resolution_note": "伏笔已经正式兑现",
                    "verification_note": "人工复检确认兑现",
                    "closed_by": "user",
                },
            )
            db.commit()

            row = db.get(Foreshadowing, row.id)
            assert row.status == "fulfilled"
            assert row.verification_note == "人工复检确认兑现"
            assert row.verified_at is not None
            assert row.closed_by == "user"
            transitions = (
                db.query(NarrativeGovernanceEvent)
                .filter(
                    NarrativeGovernanceEvent.project_id == project.id,
                    NarrativeGovernanceEvent.item_id == row.id,
                )
                .order_by(NarrativeGovernanceEvent.created_at.asc())
                .all()
            )
            assert [(item.from_status, item.to_status) for item in transitions][-2:] == [
                ("open", "pending_review"),
                ("pending_review", "fulfilled"),
            ]
    finally:
        engine.dispose()
