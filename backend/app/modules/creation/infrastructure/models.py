"""SQLAlchemy persistence models owned by the creation module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.models_support import generate_uuid
from app.database.session import Base


class NovelCreationSession(Base):
    __tablename__ = "novel_creation_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_project_id = Column(String(36), nullable=True)  # project that initiated (may be null)
    created_project_id = Column(String(36), nullable=True)  # project created by this session
    status = Column(
        String(30), nullable=False, default="drafting"
    )  # drafting|reviewing|applying|completed|failed
    mode = Column(String(20), nullable=False, default="internal_llm")  # internal_llm|external_agent
    user_brief = Column(Text, nullable=True)
    target_audience = Column(String(100), nullable=True)
    genre = Column(String(100), nullable=True)
    platform = Column(String(100), nullable=True)
    schema_version = Column(Integer, nullable=False, default=1)
    current_stage = Column(String(50), nullable=True)
    revision = Column(Integer, nullable=False, default=0)
    blueprint_json = Column(JSON, nullable=True)
    review_json = Column(JSON, nullable=True)
    draft_json = Column(JSON, nullable=True)
    checkpoints_json = Column(JSON, nullable=True)
    last_error_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    stage_runs = relationship(
        "NovelCreationStageRun",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="NovelCreationStageRun.created_at",
    )

    __table_args__ = (Index("ix_novel_creation_sessions_status", "status"),)


class NovelCreationStageRun(Base):
    __tablename__ = "novel_creation_stage_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(
        String(36), ForeignKey("novel_creation_sessions.id", ondelete="CASCADE"), nullable=False
    )
    stage = Column(String(50), nullable=False)
    operation = Column(String(30), nullable=False, default="generate")
    status = Column(String(30), nullable=False, default="queued")
    model_source = Column(String(100), nullable=True)
    tool_mode = Column(String(50), nullable=True)
    failure_class = Column(String(50), nullable=True)
    storage_target = Column(String(50), nullable=False, default="session_draft")
    context_manifest_id = Column(
        String(36), ForeignKey("context_manifests.id", ondelete="SET NULL"), nullable=True
    )
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )
    input_revision = Column(Integer, nullable=True)
    input_snapshot_hash = Column(String(64), nullable=True)
    next_action = Column(Text, nullable=True)
    request_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    current_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    session = relationship("NovelCreationSession", back_populates="stage_runs")
    events = relationship(
        "NovelCreationStageEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="NovelCreationStageEvent.sequence",
    )

    __table_args__ = (
        Index("ix_novel_creation_stage_runs_session", "session_id"),
        Index("ix_novel_creation_stage_runs_status", "status"),
    )


class NovelCreationStageEvent(Base):
    __tablename__ = "novel_creation_stage_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(
        String(36), ForeignKey("novel_creation_stage_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(50), nullable=False)
    status = Column(String(30), nullable=False, default="running")
    message = Column(Text, nullable=True)
    payload_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    run = relationship("NovelCreationStageRun", back_populates="events")

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_novel_creation_stage_event_sequence"),
        Index("ix_novel_creation_stage_events_run", "run_id"),
    )


__all__ = [
    "NovelCreationSession",
    "NovelCreationStageRun",
    "NovelCreationStageEvent",
]
