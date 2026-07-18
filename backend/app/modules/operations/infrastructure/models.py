"""Persistence models for the unified operation runtime."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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

from ....database.models_support import generate_uuid
from ....database.session import Base


class OperationRun(Base):
    __tablename__ = "operation_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_kind = Column(String(50), nullable=False)
    source_id = Column(String(100), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(300), nullable=False)
    status = Column(String(30), nullable=False, default="running")
    health_status = Column(String(30), nullable=False, default="active")
    phase = Column(String(80), nullable=True)
    current_message = Column(Text, nullable=True)
    progress_mode = Column(String(20), nullable=False, default="indeterminate")
    progress_current = Column(Integer, nullable=True)
    progress_total = Column(Integer, nullable=True)
    model_source = Column(String(200), nullable=True)
    tool_mode = Column(String(80), nullable=True)
    failure_class = Column(String(80), nullable=True)
    next_action = Column(Text, nullable=True)
    resume_url = Column(Text, nullable=True)
    can_pause = Column(Boolean, nullable=False, default=False)
    can_cancel = Column(Boolean, nullable=False, default=True)
    can_retry = Column(Boolean, nullable=False, default=True)
    input_revision = Column(Integer, nullable=True)
    input_snapshot_hash = Column(String(64), nullable=True)
    process_metrics_json = Column(JSON, nullable=True)
    attention_json = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    last_activity_at = Column(DateTime, nullable=True)
    last_output_at = Column(DateTime, nullable=True)
    last_checkpoint_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    events = relationship(
        "OperationEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="OperationEvent.sequence",
    )

    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", name="uq_operation_runs_source"),
        Index("ix_operation_runs_status", "status"),
        Index("ix_operation_runs_updated", "updated_at"),
        Index("ix_operation_runs_project", "project_id"),
    )


class OperationEvent(Base):
    __tablename__ = "operation_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), ForeignKey("operation_runs.id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(50), nullable=False)
    status = Column(String(30), nullable=False, default="running")
    message = Column(Text, nullable=True)
    payload_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    run = relationship("OperationRun", back_populates="events")

    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_operation_event_sequence"),
        Index("ix_operation_events_run", "run_id"),
    )
