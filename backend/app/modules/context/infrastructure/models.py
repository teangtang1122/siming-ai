"""Persistence models for governed context manifests and rebuild jobs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
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


class ModelContextProfile(Base):
    __tablename__ = "model_context_profiles"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    provider = Column(String(80), nullable=False)
    model_name = Column(String(200), nullable=False)
    context_window_tokens = Column(Integer, nullable=False, default=16384)
    max_output_tokens = Column(Integer, nullable=True)
    safety_margin_tokens = Column(Integer, nullable=False, default=512)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "model_name", name="uq_model_context_profiles_provider_model"),
        Index("ix_model_context_profiles_provider_model", "provider", "model_name"),
    )


class ContextManifest(Base):
    __tablename__ = "context_manifests"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    session_id = Column(String(36), nullable=True)
    task_type = Column(String(50), nullable=False)
    model = Column(String(200), nullable=True)
    provider = Column(String(80), nullable=True)
    execution_route = Column(String(50), nullable=False, default="internal_api")
    policy_version = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, default="ready")
    context_window_tokens = Column(Integer, nullable=False, default=16384)
    input_budget_tokens = Column(Integer, nullable=False, default=0)
    output_reserve_tokens = Column(Integer, nullable=False, default=0)
    safety_margin_tokens = Column(Integer, nullable=False, default=512)
    estimated_input_tokens = Column(Integer, nullable=False, default=0)
    estimated_input_chars = Column(Integer, nullable=False, default=0)
    coverage_json = Column(JSON, nullable=False, default=dict)
    warnings_json = Column(JSON, nullable=False, default=list)
    query_json = Column(JSON, nullable=False, default=dict)
    contract_json = Column(JSON, nullable=False, default=dict)
    rendered_context = Column(Text, nullable=False, default="")
    stale_reason = Column(Text, nullable=True)
    override_reason = Column(Text, nullable=True)
    override_actor = Column(String(100), nullable=True)
    overridden_at = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)
    last_validated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="context_manifests")
    items = relationship(
        "ContextManifestItem",
        back_populates="manifest",
        cascade="all, delete-orphan",
        order_by="ContextManifestItem.sort_order",
    )

    __table_args__ = (
        Index("ix_context_manifests_project_task_created", "project_id", "task_type", "created_at"),
        Index("ix_context_manifests_status", "status"),
    )


class ContextManifestItem(Base):
    __tablename__ = "context_manifest_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    manifest_id = Column(
        String(36), ForeignKey("context_manifests.id", ondelete="CASCADE"), nullable=False
    )
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    category = Column(String(50), nullable=False)
    source_type = Column(String(50), nullable=False)
    source_id = Column(String(36), nullable=True)
    chunk_id = Column(String(36), nullable=True)
    source_hash = Column(String(64), nullable=True)
    title = Column(String(300), nullable=False, default="")
    content_excerpt = Column(Text, nullable=False, default="")
    required = Column(Boolean, nullable=False, default=False)
    pinned = Column(Boolean, nullable=False, default=False)
    tier = Column(Integer, nullable=False, default=4)
    lexical_score = Column(Float, nullable=True)
    semantic_score = Column(Float, nullable=True)
    recency_score = Column(Float, nullable=True)
    structural_score = Column(Float, nullable=True)
    final_score = Column(Float, nullable=False, default=0.0)
    selection_reason = Column(Text, nullable=False, default="")
    estimated_tokens = Column(Integer, nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    evidence_submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    manifest = relationship("ContextManifest", back_populates="items")

    __table_args__ = (
        Index("ix_context_manifest_items_manifest", "manifest_id", "sort_order"),
        Index("ix_context_manifest_items_source", "project_id", "source_type", "source_id"),
    )


class ContextRebuildJob(Base):
    __tablename__ = "context_rebuild_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )
    policy_version = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, default="queued")
    requested_by = Column(String(100), nullable=True)
    total_projects = Column(Integer, nullable=False, default=0)
    completed_projects = Column(Integer, nullable=False, default=0)
    failed_projects = Column(Integer, nullable=False, default=0)
    semantic_available = Column(Boolean, nullable=False, default=False)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    projects = relationship(
        "ContextRebuildProject",
        back_populates="job",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_context_rebuild_jobs_status", "status"),)


class ContextRebuildProject(Base):
    __tablename__ = "context_rebuild_projects"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(
        String(36), ForeignKey("context_rebuild_jobs.id", ondelete="CASCADE"), nullable=False
    )
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    index_version = Column(Integer, nullable=False, default=1)
    current_source_type = Column(String(50), nullable=True)
    indexed_chunks = Column(Integer, nullable=False, default=0)
    semantic_chunks = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    job = relationship("ContextRebuildJob", back_populates="projects")
    project = relationship("Project")

    __table_args__ = (
        UniqueConstraint("job_id", "project_id", name="uq_context_rebuild_project"),
        Index("ix_context_rebuild_projects_project_status", "project_id", "status"),
    )
