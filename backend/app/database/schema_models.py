"""Infrastructure-owned schema metadata.

These tables describe the database itself rather than a novel-writing domain.
They live outside the legacy monolithic model module so new infrastructure
models do not make that module larger.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from .session import Base


class SchemaMetadata(Base):
    """Human-readable schema epoch recorded alongside Alembic's revision."""

    __tablename__ = "siming_schema_metadata"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class DataIntegrityQuarantine(Base):
    """Immutable copy of a row removed by a fail-closed integrity repair."""

    __tablename__ = "data_integrity_quarantine"

    id = Column(String(64), primary_key=True)
    migration_revision = Column(String(64), nullable=False, index=True)
    source_table = Column(String(100), nullable=False, index=True)
    source_id = Column(String(128), nullable=False)
    reason = Column(String(500), nullable=False)
    payload_json = Column(Text, nullable=False)
    quarantined_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "migration_revision",
            "source_table",
            "source_id",
            name="uq_data_integrity_quarantine_source",
        ),
    )


class DataIntegrityQuarantineBatch(Base):
    """Durable summary of one integrity-repair migration."""

    __tablename__ = "data_integrity_quarantine_batches"

    migration_revision = Column(String(64), primary_key=True)
    quarantined_receipt_count = Column(Integer, nullable=False, default=0)
    quarantined_replica_count = Column(Integer, nullable=False, default=0)
    completed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
