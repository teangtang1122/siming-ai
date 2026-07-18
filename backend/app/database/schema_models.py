"""Infrastructure-owned schema metadata.

These tables describe the database itself rather than a novel-writing domain.
They live outside the legacy monolithic model module so new infrastructure
models do not make that module larger.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from .session import Base


class SchemaMetadata(Base):
    """Human-readable schema epoch recorded alongside Alembic's revision."""

    __tablename__ = "siming_schema_metadata"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
