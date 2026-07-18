"""SQLAlchemy persistence models owned by the story module."""

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
)
from sqlalchemy.orm import relationship

from app.database.models_support import generate_uuid
from app.database.session import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(String(500), nullable=True)  # JSON array string
    narrative_perspective = Column(String(50), default="third_person")  # FR-007
    writing_style = Column(String(50), default="natural")  # FR-007
    forbidden_sentence_patterns = Column(Text, nullable=True)
    rhetoric_guidelines = Column(Text, nullable=True)
    short_sentences = Column(Boolean, default=False)
    custom_style_prompt = Column(Text, nullable=True)
    daily_word_goal = Column(Integer, default=6000)  # FR-011
    storage_mode = Column(String(30), default="db_mirror")
    folder_path = Column(Text, nullable=True)
    content_migrated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    worldbuilding_entries = relationship(
        "WorldbuildingEntry", back_populates="project", cascade="all, delete-orphan"
    )
    worldbuilding_relations = relationship(
        "WorldbuildingRelation", back_populates="project", cascade="all, delete-orphan"
    )
    characters = relationship("Character", back_populates="project", cascade="all, delete-orphan")
    character_relationships = relationship("CharacterRelationship", cascade="all, delete-orphan")
    outline_nodes = relationship(
        "OutlineNode", back_populates="project", cascade="all, delete-orphan"
    )
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")
    deconstruction_reports = relationship(
        "DeconstructionReport", back_populates="project", cascade="all, delete-orphan"
    )
    assistant_conversations = relationship(
        "AssistantConversation", back_populates="project", cascade="all, delete-orphan"
    )
    assistant_runs = relationship(
        "AssistantRun", back_populates="project", cascade="all, delete-orphan"
    )
    cataloging_jobs = relationship(
        "CatalogingJob", back_populates="project", cascade="all, delete-orphan"
    )
    agent_plans = relationship("AgentPlan", back_populates="project", cascade="all, delete-orphan")
    rag_documents = relationship("RagDocument", cascade="all, delete-orphan")
    rag_chunks = relationship("RagChunk", cascade="all, delete-orphan")
    skills = relationship("Skill", back_populates="project", cascade="all, delete-orphan")
    scheduled_tasks = relationship(
        "ScheduledTask", back_populates="project", cascade="all, delete-orphan"
    )
    mcp_server_configs = relationship(
        "McpServerConfig", back_populates="project", cascade="all, delete-orphan"
    )
    agent_runs = relationship("AgentRun", back_populates="project", cascade="all, delete-orphan")
    external_agent_settings = relationship(
        "ExternalAgentSettings",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
    )
    context_manifests = relationship("ContextManifest", cascade="all, delete-orphan")


class WorldbuildingEntry(Base):
    __tablename__ = "worldbuilding_entries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    dimension = Column(
        String(50), nullable=False
    )  # geography/history/factions/power_system/races/culture
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    first_seen_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    last_updated_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    status = Column(String(30), default="active")
    confidence = Column(Float, nullable=True)
    content_file_path = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="worldbuilding_entries")
    versions = relationship(
        "WorldbuildingVersion", back_populates="entry", cascade="all, delete-orphan"
    )
    timeline_events = relationship(
        "WorldbuildingTimeline", back_populates="entry", cascade="all, delete-orphan"
    )
    chapter_links = relationship(
        "ChapterWorldbuilding", back_populates="worldbuilding_entry", cascade="all, delete-orphan"
    )
    outgoing_relations = relationship(
        "WorldbuildingRelation",
        foreign_keys="WorldbuildingRelation.source_entry_id",
        back_populates="source_entry",
        cascade="all, delete-orphan",
    )
    incoming_relations = relationship(
        "WorldbuildingRelation",
        foreign_keys="WorldbuildingRelation.target_entry_id",
        back_populates="target_entry",
        cascade="all, delete-orphan",
    )


class WorldbuildingRelation(Base):
    __tablename__ = "worldbuilding_relations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_entry_id = Column(
        String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False
    )
    target_entry_id = Column(
        String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False
    )
    relation_type = Column(String(100), nullable=False, default="related")
    description = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="worldbuilding_relations")
    source_entry = relationship(
        "WorldbuildingEntry", foreign_keys=[source_entry_id], back_populates="outgoing_relations"
    )
    target_entry = relationship(
        "WorldbuildingEntry", foreign_keys=[target_entry_id], back_populates="incoming_relations"
    )

    __table_args__ = (
        Index("ix_worldbuilding_relations_project", "project_id"),
        Index("ix_worldbuilding_relations_pair", "source_entry_id", "target_entry_id"),
    )


class Character(Base):
    __tablename__ = "characters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    appearance = Column(Text, nullable=True)
    personality = Column(Text, nullable=True)
    background = Column(Text, nullable=True)
    abilities = Column(Text, nullable=True)  # JSON string — low-frequency query
    role_type = Column(String(50), nullable=True)  # protagonist/supporting/antagonist/etc.
    age = Column(String(50), nullable=True)  # e.g. "3岁", "约70岁", "成年"
    current_version = Column(Integer, default=1)
    is_evolution_tracked = Column(Boolean, default=True)  # FR-018
    life_status = Column(String(50), nullable=True)
    current_location = Column(String(200), nullable=True)
    realm_or_level = Column(String(200), nullable=True)
    physical_state = Column(Text, nullable=True)
    mental_state = Column(Text, nullable=True)
    current_goal = Column(Text, nullable=True)
    active_conflict = Column(Text, nullable=True)
    abilities_state = Column(Text, nullable=True)
    items_or_assets = Column(Text, nullable=True)
    profile_json = Column(JSON, nullable=True)
    content_file_path = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    last_seen_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    last_updated_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="characters")
    versions = relationship(
        "CharacterVersion", back_populates="character", cascade="all, delete-orphan"
    )
    ai_config = relationship(
        "CharacterAIConfig", back_populates="character", uselist=False, cascade="all, delete-orphan"
    )
    aliases = relationship(
        "CharacterAlias",
        back_populates="character",
        cascade="all, delete-orphan",
        foreign_keys="CharacterAlias.character_id",
    )
    timeline_events = relationship(
        "CharacterTimeline", back_populates="character", cascade="all, delete-orphan"
    )
    change_logs = relationship(
        "CharacterChangeLog", back_populates="character", cascade="all, delete-orphan"
    )
    chapter_appearances = relationship(
        "ChapterCharacter", back_populates="character", cascade="all, delete-orphan"
    )


class CharacterVersion(Base):
    __tablename__ = "character_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    version_number = Column(Integer, nullable=False)
    snapshot_data = Column(Text, nullable=False)  # Full character data JSON snapshot
    change_summary = Column(Text, nullable=True)
    source_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    character = relationship("Character", back_populates="versions")


class CharacterAIConfig(Base):
    __tablename__ = "character_ai_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    tone_style = Column(String(100), default="neutral")
    catchphrases = Column(Text, nullable=True)  # JSON array
    verbosity = Column(String(50), default="moderate")  # brief/moderate/verbose
    emotion_tendency = Column(String(100), default="neutral")
    model_override = Column(String(200), nullable=True)  # per-character model override
    custom_system_prompt = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    character = relationship("Character", back_populates="ai_config")


class CharacterAlias(Base):
    __tablename__ = "character_aliases"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    alias = Column(String(200), nullable=False)
    alias_type = Column(String(50), nullable=False, default="alias")
    description = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    source_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    merged_character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    character = relationship("Character", back_populates="aliases", foreign_keys=[character_id])
    merged_character = relationship("Character", foreign_keys=[merged_character_id])


class CharacterRelationship(Base):
    __tablename__ = "character_relationships"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    character_a_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    character_b_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type = Column(String(100), nullable=False)  # master/apprentice/enemy/lover/etc.
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class OutlineNode(Base):
    __tablename__ = "outline_nodes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    parent_id = Column(
        String(36), ForeignKey("outline_nodes.id", ondelete="CASCADE"), nullable=True
    )
    node_type = Column(String(20), nullable=False)  # volume/chapter/section
    title = Column(String(200), nullable=False)
    summary = Column(Text, nullable=True)
    status = Column(String(20), default="pending")  # pending/in_progress/completed
    source_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    actual_summary = Column(Text, nullable=True)
    planned_summary = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    cataloging_status = Column(String(30), nullable=True)
    content_file_path = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="outline_nodes")
    children = relationship(
        "OutlineNode", back_populates="parent", cascade="all, delete-orphan", single_parent=True
    )
    parent = relationship("OutlineNode", back_populates="children", remote_side=[id])
    linked_characters = relationship(
        "OutlineNodeCharacter", back_populates="outline_node", cascade="all, delete-orphan"
    )


class OutlineNodeCharacter(Base):
    __tablename__ = "outline_node_characters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    outline_node_id = Column(
        String(36), ForeignKey("outline_nodes.id", ondelete="CASCADE"), nullable=False
    )
    character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    role_in_scene = Column(String(50), nullable=True)  # protagonist/antagonist/supporting/observer
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    outline_node = relationship("OutlineNode", back_populates="linked_characters")
    character = relationship("Character")


class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    outline_node_id = Column(
        String(36), ForeignKey("outline_nodes.id", ondelete="SET NULL"), nullable=True
    )
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False, default="")
    content_file_path = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    word_count = Column(Integer, default=0)
    current_version = Column(Integer, default=1)
    quality_score = Column(Integer, nullable=True)
    quality_detail = Column(Text, nullable=True)
    quality_evaluated_at = Column(DateTime, nullable=True)
    # The immutable task context that produced this chapter, when it came from
    # an AI or external Agent flow. Manual editing may deliberately leave it
    # empty for backwards compatibility.
    context_manifest_id = Column(
        String(36), ForeignKey("context_manifests.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="chapters")
    snapshots = relationship(
        "ChapterSnapshot", back_populates="chapter", cascade="all, delete-orphan"
    )
    summary = relationship(
        "ChapterSummary", back_populates="chapter", uselist=False, cascade="all, delete-orphan"
    )
    character_appearances = relationship(
        "ChapterCharacter", back_populates="chapter", cascade="all, delete-orphan"
    )
    worldbuilding_links = relationship(
        "ChapterWorldbuilding", back_populates="chapter", cascade="all, delete-orphan"
    )


class ChapterCharacter(Base):
    __tablename__ = "chapter_characters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(
        String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    appearance_type = Column(String(50), nullable=False, default="出场")  # 出场/提及/回忆
    description = Column(Text, nullable=True)  # AI-extracted context
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="character_appearances")
    character = relationship("Character", back_populates="chapter_appearances")


class ChapterWorldbuilding(Base):
    __tablename__ = "chapter_worldbuilding"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    worldbuilding_entry_id = Column(
        String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False
    )
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="worldbuilding_links")
    worldbuilding_entry = relationship("WorldbuildingEntry", back_populates="chapter_links")


class ChapterSnapshot(Base):
    __tablename__ = "chapter_snapshots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    word_count = Column(Integer, default=0)
    trigger_type = Column(String(50), nullable=False)  # manual_save/ai_insert/restore
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="snapshots")


class DeconstructionReport(Base):
    __tablename__ = "deconstruction_reports"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_filename = Column(String(500), nullable=False)
    report_data = Column(Text, nullable=False)  # JSON
    status = Column(String(20), default="processing")  # processing/completed/failed
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    project = relationship("Project", back_populates="deconstruction_reports")


__all__ = [
    "Project",
    "WorldbuildingEntry",
    "WorldbuildingRelation",
    "Character",
    "CharacterVersion",
    "CharacterAIConfig",
    "CharacterAlias",
    "CharacterRelationship",
    "OutlineNode",
    "OutlineNodeCharacter",
    "Chapter",
    "ChapterCharacter",
    "ChapterWorldbuilding",
    "ChapterSnapshot",
    "DeconstructionReport",
]
