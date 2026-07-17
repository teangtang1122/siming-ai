"""Persistence models owned by the story module."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Index, Integer, String, Text

from ....database.models_support import generate_uuid
from ....database.session import Base


class ContentSyncJob(Base):
    """Transactional outbox row for a database-to-files mirror projection."""

    __tablename__ = "content_sync_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), nullable=False)
    target = Column(String(50), nullable=False)
    entity_id = Column(String(36), nullable=True)
    payload_json = Column(JSON, nullable=True)
    source = Column(String(80), nullable=False, default="story_command")
    dedupe_key = Column(String(500), nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index("ix_content_sync_jobs_status_next", "status", "next_attempt_at"),
        Index("ix_content_sync_jobs_project_created", "project_id", "created_at"),
        Index("ix_content_sync_jobs_dedupe", "dedupe_key", "status"),
    )


__all__ = ["ContentSyncJob"]
