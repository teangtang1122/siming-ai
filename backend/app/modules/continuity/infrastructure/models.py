"""SQLAlchemy persistence models owned by the continuity module."""

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

from app.database.models_support import generate_uuid
from app.database.session import Base


class ChapterSummary(Base):
    __tablename__ = "chapter_summaries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    summary_text = Column(Text, nullable=False)
    key_events = Column(Text, nullable=True)  # JSON array
    token_count = Column(Integer, default=0)
    ai_model = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="summary")


class CharacterChangeLog(Base):
    __tablename__ = "character_change_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    change_type = Column(String(50), nullable=False)  # skill/experience/relationship/personality
    field_name = Column(String(100), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    character = relationship("Character", back_populates="change_logs")


class CharacterTimeline(Base):
    __tablename__ = "character_timeline"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    event_description = Column(Text, nullable=False)
    event_type = Column(
        String(50), nullable=False
    )  # skill_gain/relationship_change/key_decision/emotional_turning_point/injury/death
    emotional_state_change = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    character = relationship("Character", back_populates="timeline_events")


class WorldbuildingVersion(Base):
    __tablename__ = "worldbuilding_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    entry_id = Column(
        String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False
    )
    version_number = Column(Integer, nullable=False)
    snapshot_data = Column(Text, nullable=False)
    change_summary = Column(Text, nullable=True)
    source_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    entry = relationship("WorldbuildingEntry", back_populates="versions")


class WorldbuildingTimeline(Base):
    __tablename__ = "worldbuilding_timeline"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    entry_id = Column(
        String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    event_description = Column(Text, nullable=False)
    event_type = Column(String(50), nullable=False, default="fact_change")
    evidence = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    entry = relationship("WorldbuildingEntry", back_populates="timeline_events")


class CatalogingJob(Base):
    __tablename__ = "cataloging_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    execution_mode = Column(String(20), nullable=False, default="auto")
    execution_backend = Column(String(30), nullable=False, default="internal_llm")
    agent_run_id = Column(String(36), nullable=True)
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )
    current_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    last_completed_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    blocked_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    context_integrity = Column(String(30), nullable=False, default="clean")
    total_chapters = Column(Integer, default=0)
    completed_chapters = Column(Integer, default=0)
    failed_chapters = Column(Integer, default=0)
    model = Column(String(200), nullable=True)
    model_source = Column(String(50), nullable=True)
    provider = Column(String(80), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="cataloging_jobs")
    chapter_runs = relationship(
        "CatalogingChapterRun", back_populates="job", cascade="all, delete-orphan"
    )
    candidates = relationship(
        "CatalogingCandidate", back_populates="job", cascade="all, delete-orphan"
    )


class CatalogingChapterRun(Base):
    __tablename__ = "cataloging_chapter_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(
        String(36), ForeignKey("cataloging_jobs.id", ondelete="CASCADE"), nullable=False
    )
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    chapter_order = Column(Integer, default=0)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    raw_output = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    job = relationship("CatalogingJob", back_populates="chapter_runs")
    chapter = relationship("Chapter")
    candidates = relationship(
        "CatalogingCandidate", back_populates="chapter_run", cascade="all, delete-orphan"
    )
    facts = relationship(
        "CatalogingFact", back_populates="chapter_run", cascade="all, delete-orphan"
    )


class CatalogingFact(Base):
    __tablename__ = "cataloging_facts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(
        String(36), ForeignKey("cataloging_jobs.id", ondelete="CASCADE"), nullable=False
    )
    chapter_run_id = Column(
        String(36), ForeignKey("cataloging_chapter_runs.id", ondelete="CASCADE"), nullable=False
    )
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    fact_type = Column(String(50), nullable=False)
    raw_payload = Column(Text, nullable=False)
    confidence = Column(Float, nullable=True)
    evidence = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    status = Column(String(30), nullable=False, default="active")
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    chapter_run = relationship("CatalogingChapterRun", back_populates="facts")
    chapter = relationship("Chapter")


class CatalogingCandidate(Base):
    __tablename__ = "cataloging_candidates"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(
        String(36), ForeignKey("cataloging_jobs.id", ondelete="CASCADE"), nullable=False
    )
    chapter_run_id = Column(
        String(36), ForeignKey("cataloging_chapter_runs.id", ondelete="CASCADE"), nullable=False
    )
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    item_type = Column(String(50), nullable=False)
    operation = Column(String(30), nullable=False, default="upsert")
    target_type = Column(String(50), nullable=True)
    target_id = Column(String(36), nullable=True)
    target_name = Column(String(200), nullable=True)
    raw_payload = Column(Text, nullable=False)
    edited_payload = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="pending")
    confidence = Column(Float, nullable=True)
    evidence = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    source_task = Column(String(50), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    job = relationship("CatalogingJob", back_populates="candidates")
    chapter_run = relationship("CatalogingChapterRun", back_populates="candidates")
    chapter = relationship("Chapter")


class CatalogingApplyLog(Base):
    __tablename__ = "cataloging_apply_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(
        String(36), ForeignKey("cataloging_jobs.id", ondelete="CASCADE"), nullable=False
    )
    chapter_run_id = Column(
        String(36), ForeignKey("cataloging_chapter_runs.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id = Column(
        String(36), ForeignKey("cataloging_candidates.id", ondelete="CASCADE"), nullable=False
    )
    target_type = Column(String(50), nullable=True)
    target_id = Column(String(36), nullable=True)
    operation = Column(String(30), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    applied_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Foreshadowing(Base):
    __tablename__ = "foreshadowings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="open")  # open|fulfilled|deferred|abandoned
    importance = Column(String(20), nullable=False, default="medium")
    source_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    target_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    target_chapter_number = Column(Integer, nullable=True)
    resolved_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    evidence = Column(Text, nullable=True)
    storyline = Column(String(200), nullable=True)
    dedupe_key = Column(String(200), nullable=False)
    source = Column(String(50), nullable=False, default="manual")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "dedupe_key", name="uq_foreshadowing_project_key"),
        Index("ix_foreshadowings_project_status", "project_id", "status"),
    )


class CausalEdge(Base):
    __tablename__ = "causal_edges"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    cause = Column(Text, nullable=False)
    effect = Column(Text, nullable=False)
    causal_type = Column(String(30), nullable=False, default="causes")
    strength = Column(Float, nullable=False, default=0.5)
    status = Column(String(30), nullable=False, default="open")  # open|resolved|invalidated
    character_ids = Column(JSON, nullable=False, default=list)
    source_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    resolved_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    evidence = Column(Text, nullable=True)
    dedupe_key = Column(String(200), nullable=False)
    source = Column(String(50), nullable=False, default="manual")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "dedupe_key", name="uq_causal_edge_project_key"),
        Index("ix_causal_edges_project_status_strength", "project_id", "status", "strength"),
    )


class NarrativeDebt(Base):
    __tablename__ = "narrative_debts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    debt_type = Column(String(30), nullable=False, default="promise")
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="open")  # open|fulfilled|deferred|abandoned
    priority = Column(String(20), nullable=False, default="medium")
    source_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    target_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    target_chapter_number = Column(Integer, nullable=True)
    resolved_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    linked_foreshadowing_id = Column(
        String(36), ForeignKey("foreshadowings.id", ondelete="SET NULL"), nullable=True
    )
    linked_causal_edge_id = Column(
        String(36), ForeignKey("causal_edges.id", ondelete="SET NULL"), nullable=True
    )
    evidence = Column(Text, nullable=True)
    dedupe_key = Column(String(200), nullable=False)
    source = Column(String(50), nullable=False, default="manual")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "dedupe_key", name="uq_narrative_debt_project_key"),
        Index("ix_narrative_debts_project_status", "project_id", "status"),
    )


class CharacterNarrativeState(Base):
    __tablename__ = "character_narrative_states"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    current_goal = Column(Text, nullable=True)
    public_stance = Column(Text, nullable=True)
    hidden_intent = Column(Text, nullable=True)
    emotional_residue = Column(Text, nullable=True)
    relationship_tension = Column(Text, nullable=True)
    behavior_boundaries = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)
    source = Column(String(50), nullable=False, default="manual")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_character_narrative_state_character", "project_id", "character_id", "created_at"),
    )


class ChapterQualityMetric(Base):
    __tablename__ = "chapter_quality_metrics"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    plot_tension = Column(Float, nullable=True)
    emotional_tension = Column(Float, nullable=True)
    pacing_density = Column(Float, nullable=True)
    character_consistency = Column(Float, nullable=True)
    viewpoint_consistency = Column(Float, nullable=True)
    world_consistency = Column(Float, nullable=True)
    target_tension = Column(Float, nullable=True)
    strict_mode = Column(Boolean, nullable=False, default=False)
    passed = Column(Boolean, nullable=True)
    warnings = Column(JSON, nullable=False, default=list)
    evidence = Column(Text, nullable=True)
    source = Column(String(50), nullable=False, default="manual")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_chapter_quality_metrics_chapter", "project_id", "chapter_id", "created_at"),
    )


class NarrativeCheckpoint(Base):
    __tablename__ = "narrative_checkpoints"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    chapter_snapshot_id = Column(
        String(36), ForeignKey("chapter_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    sequence = Column(Integer, nullable=False)
    label = Column(String(300), nullable=False)
    trigger_type = Column(String(50), nullable=False, default="post_write")
    state_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "sequence", name="uq_narrative_checkpoint_sequence"),
        Index("ix_narrative_checkpoints_project", "project_id", "sequence"),
    )


__all__ = [
    "ChapterSummary",
    "CharacterChangeLog",
    "CharacterTimeline",
    "WorldbuildingVersion",
    "WorldbuildingTimeline",
    "CatalogingJob",
    "CatalogingChapterRun",
    "CatalogingFact",
    "CatalogingCandidate",
    "CatalogingApplyLog",
    "Foreshadowing",
    "CausalEdge",
    "NarrativeDebt",
    "CharacterNarrativeState",
    "ChapterQualityMetric",
    "NarrativeCheckpoint",
]
