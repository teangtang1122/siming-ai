"""SQLAlchemy novel-creation session persistence adapter."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import NovelCreationSession, NovelCreationStageRun


class SqlAlchemyNovelCreationSessionStore:
    def __init__(self, session: Session) -> None:
        self.db = session

    def session(self, session_id: str):
        return self.db.query(NovelCreationSession).filter(
            NovelCreationSession.id == session_id
        ).first()

    def sessions(self, *, include_completed: bool, limit: int = 30):
        query = self.db.query(NovelCreationSession)
        if not include_completed:
            query = query.filter(
                NovelCreationSession.status.in_(["drafting", "reviewing", "failed"])
            )
        return query.order_by(
            NovelCreationSession.updated_at.desc(),
            NovelCreationSession.created_at.desc(),
        ).limit(limit).all()

    def delete(self, session: Any) -> None:
        self.db.delete(session)

    def run(self, run_id: str):
        return self.db.query(NovelCreationStageRun).filter(
            NovelCreationStageRun.id == run_id
        ).first()

    def running_stage(self, session_id: str, stage: str):
        return self.db.query(NovelCreationStageRun).filter(
            NovelCreationStageRun.session_id == session_id,
            NovelCreationStageRun.stage == stage,
            NovelCreationStageRun.status == "running",
        ).order_by(NovelCreationStageRun.created_at.desc()).first()

    def latest_stage_operation(self, session_id: str, stage: str):
        return self.db.query(NovelCreationStageRun).filter(
            NovelCreationStageRun.session_id == session_id,
            NovelCreationStageRun.stage.in_([stage, "all"]),
            NovelCreationStageRun.operation_id.isnot(None),
        ).order_by(
            NovelCreationStageRun.completed_at.desc(),
            NovelCreationStageRun.created_at.desc(),
        ).first()


__all__ = ["SqlAlchemyNovelCreationSessionStore"]
