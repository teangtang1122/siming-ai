"""Database models — all 17 tables for the novel writing AI agent."""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Integer, DateTime, Boolean, ForeignKey, Float,
    Index, JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from .session import Base


def generate_uuid():
    """Generate a new UUID string."""
    return str(uuid.uuid4())


def default_external_agent_enabled_packs():
    return [
        "readonly_collaboration",
        "project_writing",
        "project_management",
        "trusted_local_maintenance",
    ]


def default_trusted_local_clients():
    return ["claude-code", "codex", "opencode", "mimocode", "cursor", "trae"]


# ---------------------------------------------------------------------------
# 1. projects — 作品表
# ---------------------------------------------------------------------------
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
    worldbuilding_entries = relationship("WorldbuildingEntry", back_populates="project", cascade="all, delete-orphan")
    worldbuilding_relations = relationship("WorldbuildingRelation", back_populates="project", cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="project", cascade="all, delete-orphan")
    character_relationships = relationship("CharacterRelationship", cascade="all, delete-orphan")
    outline_nodes = relationship("OutlineNode", back_populates="project", cascade="all, delete-orphan")
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")
    deconstruction_reports = relationship("DeconstructionReport", back_populates="project", cascade="all, delete-orphan")
    assistant_conversations = relationship("AssistantConversation", back_populates="project", cascade="all, delete-orphan")
    assistant_runs = relationship("AssistantRun", back_populates="project", cascade="all, delete-orphan")
    cataloging_jobs = relationship("CatalogingJob", back_populates="project", cascade="all, delete-orphan")
    agent_plans = relationship("AgentPlan", back_populates="project", cascade="all, delete-orphan")
    rag_documents = relationship("RagDocument", cascade="all, delete-orphan")
    rag_chunks = relationship("RagChunk", cascade="all, delete-orphan")
    skills = relationship("Skill", back_populates="project", cascade="all, delete-orphan")
    scheduled_tasks = relationship("ScheduledTask", back_populates="project", cascade="all, delete-orphan")
    mcp_server_configs = relationship("McpServerConfig", back_populates="project", cascade="all, delete-orphan")
    agent_runs = relationship("AgentRun", back_populates="project", cascade="all, delete-orphan")
    external_agent_settings = relationship("ExternalAgentSettings", back_populates="project", uselist=False, cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 2. worldbuilding_entries — 世界观条目表
# ---------------------------------------------------------------------------
class WorldbuildingEntry(Base):
    __tablename__ = "worldbuilding_entries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    dimension = Column(String(50), nullable=False)  # geography/history/factions/power_system/races/culture
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    first_seen_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    last_updated_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(30), default="active")
    confidence = Column(Float, nullable=True)
    content_file_path = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="worldbuilding_entries")
    versions = relationship("WorldbuildingVersion", back_populates="entry", cascade="all, delete-orphan")
    timeline_events = relationship("WorldbuildingTimeline", back_populates="entry", cascade="all, delete-orphan")
    chapter_links = relationship("ChapterWorldbuilding", back_populates="worldbuilding_entry", cascade="all, delete-orphan")
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


# ---------------------------------------------------------------------------
# 2b. worldbuilding_relations - stable links between places and factions
# ---------------------------------------------------------------------------
class WorldbuildingRelation(Base):
    __tablename__ = "worldbuilding_relations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_entry_id = Column(String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False)
    target_entry_id = Column(String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(100), nullable=False, default="related")
    description = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="worldbuilding_relations")
    source_entry = relationship("WorldbuildingEntry", foreign_keys=[source_entry_id], back_populates="outgoing_relations")
    target_entry = relationship("WorldbuildingEntry", foreign_keys=[target_entry_id], back_populates="incoming_relations")

    __table_args__ = (
        Index("ix_worldbuilding_relations_project", "project_id"),
        Index("ix_worldbuilding_relations_pair", "source_entry_id", "target_entry_id"),
    )


# ---------------------------------------------------------------------------
# 3. characters — 角色表
# ---------------------------------------------------------------------------
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
    last_seen_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    last_updated_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="characters")
    versions = relationship("CharacterVersion", back_populates="character", cascade="all, delete-orphan")
    ai_config = relationship("CharacterAIConfig", back_populates="character", uselist=False, cascade="all, delete-orphan")
    aliases = relationship(
        "CharacterAlias",
        back_populates="character",
        cascade="all, delete-orphan",
        foreign_keys="CharacterAlias.character_id",
    )
    timeline_events = relationship("CharacterTimeline", back_populates="character", cascade="all, delete-orphan")
    change_logs = relationship("CharacterChangeLog", back_populates="character", cascade="all, delete-orphan")
    chapter_appearances = relationship("ChapterCharacter", back_populates="character", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 4. character_versions — 角色版本快照表
# ---------------------------------------------------------------------------
class CharacterVersion(Base):
    __tablename__ = "character_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    character_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    snapshot_data = Column(Text, nullable=False)  # Full character data JSON snapshot
    change_summary = Column(Text, nullable=True)
    source_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    character = relationship("Character", back_populates="versions")


# ---------------------------------------------------------------------------
# 5. character_ai_configs — 角色AI配置表
# ---------------------------------------------------------------------------
class CharacterAIConfig(Base):
    __tablename__ = "character_ai_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    character_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, unique=True)
    tone_style = Column(String(100), default="neutral")
    catchphrases = Column(Text, nullable=True)  # JSON array
    verbosity = Column(String(50), default="moderate")  # brief/moderate/verbose
    emotion_tendency = Column(String(100), default="neutral")
    model_override = Column(String(200), nullable=True)  # per-character model override
    custom_system_prompt = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    character = relationship("Character", back_populates="ai_config")


# ---------------------------------------------------------------------------
# 6. character_relationships — 角色关系表
# ---------------------------------------------------------------------------
class CharacterAlias(Base):
    __tablename__ = "character_aliases"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    alias = Column(String(200), nullable=False)
    alias_type = Column(String(50), nullable=False, default="alias")
    description = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    source_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    merged_character_id = Column(String(36), ForeignKey("characters.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    character = relationship("Character", back_populates="aliases", foreign_keys=[character_id])
    merged_character = relationship("Character", foreign_keys=[merged_character_id])


class CharacterRelationship(Base):
    __tablename__ = "character_relationships"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    character_a_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    character_b_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    relationship_type = Column(String(100), nullable=False)  # master/apprentice/enemy/lover/etc.
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# 7. outline_nodes — 大纲节点表
# ---------------------------------------------------------------------------
class OutlineNode(Base):
    __tablename__ = "outline_nodes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    parent_id = Column(String(36), ForeignKey("outline_nodes.id", ondelete="CASCADE"), nullable=True)
    node_type = Column(String(20), nullable=False)  # volume/chapter/section
    title = Column(String(200), nullable=False)
    summary = Column(Text, nullable=True)
    status = Column(String(20), default="pending")  # pending/in_progress/completed
    source_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
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
    children = relationship("OutlineNode", back_populates="parent",
                            cascade="all, delete-orphan",
                            single_parent=True)
    parent = relationship("OutlineNode", back_populates="children",
                          remote_side=[id])
    linked_characters = relationship("OutlineNodeCharacter", back_populates="outline_node", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 8. outline_node_characters — 大纲节点关联角色表 (replaces JSON field)
# ---------------------------------------------------------------------------
class OutlineNodeCharacter(Base):
    __tablename__ = "outline_node_characters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    outline_node_id = Column(String(36), ForeignKey("outline_nodes.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    role_in_scene = Column(String(50), nullable=True)  # protagonist/antagonist/supporting/observer
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    outline_node = relationship("OutlineNode", back_populates="linked_characters")
    character = relationship("Character")


# ---------------------------------------------------------------------------
# 9. chapters — 章节表
# ---------------------------------------------------------------------------
class Chapter(Base):
    __tablename__ = "chapters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    outline_node_id = Column(String(36), ForeignKey("outline_nodes.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False, default="")
    content_file_path = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    word_count = Column(Integer, default=0)
    current_version = Column(Integer, default=1)
    quality_score = Column(Integer, nullable=True)
    quality_detail = Column(Text, nullable=True)
    quality_evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="chapters")
    snapshots = relationship("ChapterSnapshot", back_populates="chapter", cascade="all, delete-orphan")
    summary = relationship("ChapterSummary", back_populates="chapter", uselist=False, cascade="all, delete-orphan")
    character_appearances = relationship("ChapterCharacter", back_populates="chapter", cascade="all, delete-orphan")
    worldbuilding_links = relationship("ChapterWorldbuilding", back_populates="chapter", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 10. chapter_characters — 章节角色出场表
# ---------------------------------------------------------------------------
class ChapterCharacter(Base):
    __tablename__ = "chapter_characters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    character_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    appearance_type = Column(String(50), nullable=False, default="出场")  # 出场/提及/回忆
    description = Column(Text, nullable=True)  # AI-extracted context
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="character_appearances")
    character = relationship("Character", back_populates="chapter_appearances")


# ---------------------------------------------------------------------------
# 10b. chapter_worldbuilding - 章节世界观关联表
# ---------------------------------------------------------------------------
class ChapterWorldbuilding(Base):
    __tablename__ = "chapter_worldbuilding"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    worldbuilding_entry_id = Column(String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="worldbuilding_links")
    worldbuilding_entry = relationship("WorldbuildingEntry", back_populates="chapter_links")


# ---------------------------------------------------------------------------
# 11. chapter_snapshots — 章节版本快照表
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 12. chapter_summaries — 章节摘要表
# ---------------------------------------------------------------------------
class ChapterSummary(Base):
    __tablename__ = "chapter_summaries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, unique=True)
    summary_text = Column(Text, nullable=False)
    key_events = Column(Text, nullable=True)  # JSON array
    token_count = Column(Integer, default=0)
    ai_model = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    chapter = relationship("Chapter", back_populates="summary")


# ---------------------------------------------------------------------------
# 13. api_configs — API配置表
# ---------------------------------------------------------------------------
class APIConfig(Base):
    __tablename__ = "api_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    provider = Column(String(50), nullable=False, unique=True)  # openai/anthropic/deepseek/qwen/gemini
    api_key_encrypted = Column(Text, nullable=False)
    default_model = Column(String(100), nullable=False)
    is_global_default = Column(Boolean, default=False)
    base_url_override = Column(String(500), nullable=True)
    provider_type = Column(String(20), nullable=False, default="api")  # api/local_cli/local_runtime
    cli_command = Column(String(500), nullable=True)
    cli_args = Column(Text, nullable=True)  # JSON array or shell-like argument string
    max_output_tokens = Column(Integer, nullable=True)
    deconstruct_input_char_limit = Column(Integer, nullable=True)
    deconstruct_item_char_limit = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# 15. deconstruction_reports — 拆书报告表
# ---------------------------------------------------------------------------
class DeconstructionReport(Base):
    __tablename__ = "deconstruction_reports"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_filename = Column(String(500), nullable=False)
    report_data = Column(Text, nullable=False)  # JSON
    status = Column(String(20), default="processing")  # processing/completed/failed
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    project = relationship("Project", back_populates="deconstruction_reports")


# ---------------------------------------------------------------------------
# 16. character_change_logs — 角色变更日志表
# ---------------------------------------------------------------------------
class CharacterChangeLog(Base):
    __tablename__ = "character_change_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    character_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    change_type = Column(String(50), nullable=False)  # skill/experience/relationship/personality
    field_name = Column(String(100), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    confirmed = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    character = relationship("Character", back_populates="change_logs")


# ---------------------------------------------------------------------------
# 17. character_timeline — 角色事件时间线表
# ---------------------------------------------------------------------------
class CharacterTimeline(Base):
    __tablename__ = "character_timeline"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    character_id = Column(String(36), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    event_description = Column(Text, nullable=False)
    event_type = Column(String(50), nullable=False)  # skill_gain/relationship_change/key_decision/emotional_turning_point/injury/death
    emotional_state_change = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    character = relationship("Character", back_populates="timeline_events")


# ---------------------------------------------------------------------------
# 17b. worldbuilding_versions - 世界观版本快照表
# ---------------------------------------------------------------------------
class WorldbuildingVersion(Base):
    __tablename__ = "worldbuilding_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    entry_id = Column(String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False)
    version_number = Column(Integer, nullable=False)
    snapshot_data = Column(Text, nullable=False)
    change_summary = Column(Text, nullable=True)
    source_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    entry = relationship("WorldbuildingEntry", back_populates="versions")


# ---------------------------------------------------------------------------
# 17c. worldbuilding_timeline - 世界观时间线表
# ---------------------------------------------------------------------------
class WorldbuildingTimeline(Base):
    __tablename__ = "worldbuilding_timeline"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    entry_id = Column(String(36), ForeignKey("worldbuilding_entries.id", ondelete="CASCADE"), nullable=False)
    chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    event_description = Column(Text, nullable=False)
    event_type = Column(String(50), nullable=False, default="fact_change")
    evidence = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    entry = relationship("WorldbuildingEntry", back_populates="timeline_events")


# ---------------------------------------------------------------------------
# 18. assistant_conversations — 写作助手对话会话表
# ---------------------------------------------------------------------------
class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False, default="新对话")
    scope = Column(String(50), nullable=False, default="writer")
    current_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    current_outline_node_id = Column(String(36), ForeignKey("outline_nodes.id", ondelete="SET NULL"), nullable=True)
    model = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="assistant_conversations")
    messages = relationship("AssistantMessage", back_populates="conversation", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# 19. assistant_messages — 写作助手消息表
# ---------------------------------------------------------------------------
class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(String(36), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False)  # user/assistant
    content = Column(Text, nullable=False, default="")
    payload_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="completed")  # running/completed/error/aborted
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation = relationship("AssistantConversation", back_populates="messages")


# ---------------------------------------------------------------------------
# 19a. system_assistant_conversations — 系统级助手对话会话表
# ---------------------------------------------------------------------------
class SystemAssistantConversation(Base):
    __tablename__ = "system_assistant_conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    title = Column(String(200), nullable=False, default="新对话")
    creation_session_id = Column(String(36), nullable=True)
    user_brief = Column(Text, nullable=True)
    blueprint_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    messages = relationship(
        "SystemAssistantMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# Local model runtime, downloads, adapters, and training jobs
# ---------------------------------------------------------------------------
class LocalModel(Base):
    __tablename__ = "local_models"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    model_key = Column(String(120), nullable=False, unique=True)
    display_name = Column(String(200), nullable=False)
    family = Column(String(100), nullable=False, default="qwen3")
    parameter_size = Column(String(30), nullable=True)
    quantization = Column(String(50), nullable=True)
    context_length = Column(Integer, nullable=False, default=8192)
    file_path = Column(Text, nullable=True)
    file_size = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    license_name = Column(String(100), nullable=True)
    source = Column(String(50), nullable=True)
    source_urls = Column(JSON, nullable=True)
    min_ram_gb = Column(Integer, nullable=True)
    recommended_vram_gb = Column(Integer, nullable=True)
    status = Column(String(30), nullable=False, default="available")
    installed_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_local_models_status", "status"),
    )


class LocalRuntimeInstallation(Base):
    __tablename__ = "local_runtime_installations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    runtime_key = Column(String(80), nullable=False, unique=True, default="llama_cpp")
    version = Column(String(80), nullable=True)
    backend = Column(String(30), nullable=False, default="cpu")
    executable_path = Column(Text, nullable=True)
    install_path = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="not_installed")
    port = Column(Integer, nullable=True)
    pid = Column(Integer, nullable=True)
    active_model_id = Column(String(36), ForeignKey("local_models.id", ondelete="SET NULL"), nullable=True)
    last_error = Column(Text, nullable=True)
    last_health_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ModelDownloadTask(Base):
    __tablename__ = "model_download_tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    kind = Column(String(30), nullable=False, default="model")
    target_key = Column(String(120), nullable=False)
    source_url = Column(Text, nullable=True)
    destination_path = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    downloaded_bytes = Column(Integer, nullable=False, default=0)
    total_bytes = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_model_download_tasks_status", "status"),
    )


class ModelAdapter(Base):
    __tablename__ = "model_adapters"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    base_model_key = Column(String(120), nullable=False)
    name = Column(String(200), nullable=False)
    adapter_type = Column(String(30), nullable=False, default="lora")
    scope = Column(String(30), nullable=False, default="private")
    file_path = Column(Text, nullable=False)
    base_model_sha256 = Column(String(64), nullable=True)
    weight = Column(Float, nullable=False, default=1.0)
    enabled = Column(Boolean, nullable=False, default=True)
    is_default_for_writing = Column(Boolean, nullable=False, default=False)
    metrics_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_model_adapters_project", "project_id"),
        Index("ix_model_adapters_base_model", "base_model_key"),
    )


class LocalModelTaskSetting(Base):
    __tablename__ = "local_model_task_settings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_type = Column(String(30), nullable=False, unique=True)
    model_key = Column(String(120), nullable=False)
    adapter_ids = Column(JSON, nullable=True)
    context_length = Column(Integer, nullable=True)
    allow_api_fallback = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainingDataset(Base):
    __tablename__ = "training_datasets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    name = Column(String(200), nullable=False)
    source_config_json = Column(JSON, nullable=True)
    file_path = Column(Text, nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    train_count = Column(Integer, nullable=False, default=0)
    eval_count = Column(Integer, nullable=False, default=0)
    stats_json = Column(JSON, nullable=True)
    rights_confirmed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class TrainingJob(Base):
    __tablename__ = "training_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    dataset_id = Column(String(36), ForeignKey("training_datasets.id", ondelete="SET NULL"), nullable=True)
    base_model_key = Column(String(120), nullable=False)
    name = Column(String(200), nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    progress = Column(Float, nullable=False, default=0.0)
    current_step = Column(Integer, nullable=False, default=0)
    total_steps = Column(Integer, nullable=True)
    config_json = Column(JSON, nullable=True)
    metrics_json = Column(JSON, nullable=True)
    checkpoint_path = Column(Text, nullable=True)
    output_path = Column(Text, nullable=True)
    log_path = Column(Text, nullable=True)
    pid = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_training_jobs_status", "status"),
        Index("ix_training_jobs_project", "project_id"),
    )

class SystemAssistantMessage(Base):
    __tablename__ = "system_assistant_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(
        String(36),
        ForeignKey("system_assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False, default="")
    payload_json = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="completed")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation = relationship("SystemAssistantConversation", back_populates="messages")


# ---------------------------------------------------------------------------
# 20. assistant_runs — 写作助手执行任务表
# ---------------------------------------------------------------------------
class AssistantRun(Base):
    __tablename__ = "assistant_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=True)
    user_message_id = Column(String(36), ForeignKey("assistant_messages.id", ondelete="SET NULL"), nullable=True)
    assistant_message_id = Column(String(36), ForeignKey("assistant_messages.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(30), nullable=False, default="running")
    phase = Column(String(50), nullable=True)
    scope = Column(String(50), nullable=True)
    assistant_mode = Column(String(20), nullable=True)
    model = Column(String(200), nullable=True)
    current_iteration = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    final_reply = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="assistant_runs")
    steps = relationship("AssistantRunStep", back_populates="run", cascade="all, delete-orphan")


class AssistantRunStep(Base):
    __tablename__ = "assistant_run_steps"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), ForeignKey("assistant_runs.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    step_type = Column(String(50), nullable=False, default="tool")
    tool = Column(String(100), nullable=True)
    status = Column(String(30), nullable=False, default="running")
    iteration = Column(Integer, default=0)
    request_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    detail = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- Recovery / retry fields ---
    retry_of_step_id = Column(String(36), ForeignKey("assistant_run_steps.id", ondelete="SET NULL"), nullable=True)
    resolved_step_id = Column(String(36), ForeignKey("assistant_run_steps.id", ondelete="SET NULL"), nullable=True)
    attempt_no = Column(Integer, default=1, nullable=False)
    depends_on_step_ids = Column(Text, nullable=True)       # JSON array of step IDs
    output_refs = Column(Text, nullable=True)                # JSON object: {resource_type: resource_id}
    planned_next_steps = Column(Text, nullable=True)         # JSON array of tool names
    idempotency_key = Column(String(200), nullable=True)

    run = relationship("AssistantRun", back_populates="steps")
    retry_of = relationship("AssistantRunStep", remote_side="AssistantRunStep.id", foreign_keys=[retry_of_step_id])
    resolved_by = relationship("AssistantRunStep", remote_side="AssistantRunStep.id", foreign_keys=[resolved_step_id])

    __table_args__ = (
        Index("ix_run_steps_idempotency_key", "idempotency_key"),
        Index("ix_run_steps_run_iteration", "run_id", "iteration"),
    )


# ---------------------------------------------------------------------------
# 21. assistant_memories — 智能体持久记忆表
# ---------------------------------------------------------------------------
class CatalogingJob(Base):
    __tablename__ = "cataloging_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(30), nullable=False, default="queued")
    execution_mode = Column(String(20), nullable=False, default="auto")
    execution_backend = Column(String(30), nullable=False, default="internal_llm")
    agent_run_id = Column(String(36), nullable=True)
    current_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    last_completed_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    blocked_chapter_id = Column(String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
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
    chapter_runs = relationship("CatalogingChapterRun", back_populates="job", cascade="all, delete-orphan")
    candidates = relationship("CatalogingCandidate", back_populates="job", cascade="all, delete-orphan")


class CatalogingChapterRun(Base):
    __tablename__ = "cataloging_chapter_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(String(36), ForeignKey("cataloging_jobs.id", ondelete="CASCADE"), nullable=False)
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
    candidates = relationship("CatalogingCandidate", back_populates="chapter_run", cascade="all, delete-orphan")
    facts = relationship("CatalogingFact", back_populates="chapter_run", cascade="all, delete-orphan")


class CatalogingFact(Base):
    __tablename__ = "cataloging_facts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    job_id = Column(String(36), ForeignKey("cataloging_jobs.id", ondelete="CASCADE"), nullable=False)
    chapter_run_id = Column(String(36), ForeignKey("cataloging_chapter_runs.id", ondelete="CASCADE"), nullable=False)
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
    job_id = Column(String(36), ForeignKey("cataloging_jobs.id", ondelete="CASCADE"), nullable=False)
    chapter_run_id = Column(String(36), ForeignKey("cataloging_chapter_runs.id", ondelete="CASCADE"), nullable=False)
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
    job_id = Column(String(36), ForeignKey("cataloging_jobs.id", ondelete="CASCADE"), nullable=False)
    chapter_run_id = Column(String(36), ForeignKey("cataloging_chapter_runs.id", ondelete="CASCADE"), nullable=False)
    candidate_id = Column(String(36), ForeignKey("cataloging_candidates.id", ondelete="CASCADE"), nullable=False)
    target_type = Column(String(50), nullable=True)
    target_id = Column(String(36), nullable=True)
    operation = Column(String(30), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    applied_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AssistantMemory(Base):
    __tablename__ = "assistant_memories"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    category = Column(String(30), nullable=False, default="user_preference")  # user_preference/project_fact/writing_style/research_note/workflow_preference
    key = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    source = Column(String(50), nullable=True)  # e.g., "web_search", "user", "assistant"
    importance = Column(Integer, nullable=False, default=5)  # 0-10
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# 22. chapter_drafts — 章节草稿持久化表（支持重启后重试）
# ---------------------------------------------------------------------------
class ChapterDraft(Base):
    __tablename__ = "chapter_drafts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False, default="")
    outline_node_id = Column(String(36), nullable=True)
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# 23. rag_documents — RAG 索引文档跟踪表
# ---------------------------------------------------------------------------
class RagDocument(Base):
    __tablename__ = "rag_documents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_type = Column(String(30), nullable=False)  # chapter/chapter_summary/outline/character/character_timeline/worldbuilding/assistant_memory
    source_id = Column(String(36), nullable=False)
    content_hash = Column(String(64), nullable=False)
    chunk_count = Column(Integer, nullable=False, default=0)
    indexed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_rag_doc_project_source", "project_id", "source_type", "source_id"),
    )


# ---------------------------------------------------------------------------
# 24. rag_chunks — RAG 可搜索内容块
# ---------------------------------------------------------------------------
class RagChunk(Base):
    __tablename__ = "rag_chunks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    document_id = Column(String(36), ForeignKey("rag_documents.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_type = Column(String(30), nullable=False)
    source_id = Column(String(36), nullable=False)
    chunk_index = Column(Integer, nullable=False, default=0)
    title = Column(String(300), nullable=False, default="")
    content = Column(Text, nullable=False, default="")
    metadata_json = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_rag_chunk_project", "project_id"),
        Index("ix_rag_chunk_source", "project_id", "source_type", "source_id"),
    )


# ---------------------------------------------------------------------------
# 25. rag_links — RAG 块间可选关联（v1 仅存储，不参与排序）
# ---------------------------------------------------------------------------
class RagLink(Base):
    __tablename__ = "rag_links"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_chunk_id = Column(String(36), nullable=False)
    target_chunk_id = Column(String(36), nullable=False)
    link_type = Column(String(30), nullable=False, default="references")  # references/contradicts/depends_on
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# 26. agent_plans — 智能体执行计划表
# ---------------------------------------------------------------------------
class AgentPlan(Base):
    __tablename__ = "agent_plans"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("assistant_conversations.id", ondelete="SET NULL"), nullable=True)
    assistant_run_id = Column(String(36), ForeignKey("assistant_runs.id", ondelete="SET NULL"), nullable=True)
    assistant_message_id = Column(String(36), ForeignKey("assistant_messages.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(100), nullable=False)  # fast_chapter / quality_chapter / cataloging_init
    status = Column(String(30), nullable=False, default="pending")  # pending/running/completed/error/cancelled
    graph_json = Column(Text, nullable=False)  # serialized PlanGraph
    model = Column(String(200), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="agent_plans")
    steps = relationship("AgentPlanStep", back_populates="plan", cascade="all, delete-orphan")
    conversation = relationship("AssistantConversation", foreign_keys=[conversation_id])
    assistant_run = relationship("AssistantRun", foreign_keys=[assistant_run_id])


# ---------------------------------------------------------------------------
# 27. agent_plan_steps — 智能体执行计划步骤表
# ---------------------------------------------------------------------------
class AgentPlanStep(Base):
    __tablename__ = "agent_plan_steps"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    plan_id = Column(String(36), ForeignKey("agent_plans.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    step_key = Column(String(50), nullable=False)
    tool = Column(String(100), nullable=False)
    args_json = Column(Text, nullable=True)
    depends_on_json = Column(Text, nullable=True)  # JSON array of step_keys
    status = Column(String(30), nullable=False, default="pending")  # pending/blocked/running/ok/error/skipped
    retry_policy = Column(String(20), nullable=False, default="none")
    idempotency_key = Column(String(200), nullable=True)
    result_json = Column(Text, nullable=True)
    output_refs = Column(Text, nullable=True)  # JSON: {"draft_id": "...", "chapter_id": "..."}
    detail = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    attempt_no = Column(Integer, default=1, nullable=False)
    retry_of_step_id = Column(String(36), ForeignKey("agent_plan_steps.id", ondelete="SET NULL"), nullable=True)
    resolved_step_id = Column(String(36), ForeignKey("agent_plan_steps.id", ondelete="SET NULL"), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("AgentPlan", back_populates="steps")
    retry_of = relationship("AgentPlanStep", remote_side="AgentPlanStep.id", foreign_keys=[retry_of_step_id])
    resolved_by = relationship("AgentPlanStep", remote_side="AgentPlanStep.id", foreign_keys=[resolved_step_id])

    __table_args__ = (
        Index("ix_agent_plan_steps_plan_key", "plan_id", "step_key", unique=True),
        Index("ix_agent_plan_steps_idempotency", "idempotency_key"),
    )


# ---------------------------------------------------------------------------
# 38. skills — 技能表
# ---------------------------------------------------------------------------
class Skill(Base):
    __tablename__ = "skills"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    builtin_key = Column(String(50), nullable=True)  # e.g. "continue_writing"
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    trigger_examples = Column(Text, nullable=True)  # JSON array of keyword strings
    system_prompt = Column(Text, nullable=False)
    recommended_tools = Column(Text, nullable=True)  # JSON array of tool names
    forbidden_tools = Column(Text, nullable=True)  # JSON array of tool names
    scope = Column(String(30), default="global")  # global|project|writing|outline|characters|worldbuilding|cataloging|research
    priority = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="skills")

    __table_args__ = (
        Index("ix_skills_project_name", "project_id", "name", unique=True),
    )


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    skill_id = Column(String(36), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    change_summary = Column(Text, nullable=True)
    snapshot_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    skill = relationship("Skill")

    __table_args__ = (
        Index("ix_skill_versions_skill_created", "skill_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# 19. scheduled_tasks - 定时任务表
# ---------------------------------------------------------------------------
class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    prompt = Column(Text, nullable=False)
    cron_expr = Column(String(100), nullable=True)
    interval_minutes = Column(Integer, nullable=True)
    tool_policy = Column(JSON, nullable=True, default=list)
    status = Column(String(20), nullable=False, default="active")
    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String(20), nullable=True)
    last_run_output = Column(Text, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="scheduled_tasks")

    __table_args__ = (
        Index("ix_scheduled_tasks_project_status", "project_id", "status"),
        Index("ix_scheduled_tasks_next_run", "next_run_at"),
    )


# ---------------------------------------------------------------------------
# 20. mcp_server_configs — 外部 MCP 服务器配置表
# ---------------------------------------------------------------------------
class McpServerConfig(Base):
    __tablename__ = "mcp_server_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    transport = Column(String(20), nullable=False, default="stdio")  # stdio | http
    command = Column(Text, nullable=True)  # for stdio: command to run
    url = Column(String(500), nullable=True)  # for http: server URL
    enabled = Column(Boolean, default=True)
    status = Column(String(20), default="disconnected")  # disconnected | connecting | connected | error
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="mcp_server_configs")

    __table_args__ = (
        Index("ix_mcp_server_configs_project", "project_id"),
    )


# ---------------------------------------------------------------------------
# 21. agent_runs — 外部 Agent 运行记录表
# ---------------------------------------------------------------------------
class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(50), nullable=False, default="mcp")  # mcp | internal
    client_name = Column(String(100), nullable=True)  # claude-code, codex, etc.
    title = Column(String(200), nullable=True)
    status = Column(String(30), nullable=False, default="created")  # created|running|waiting_confirmation|completed|failed|cancelled
    current_step = Column(String(200), nullable=True)
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="agent_runs")
    events = relationship("AgentRunEvent", back_populates="run", cascade="all, delete-orphan",
                          order_by="AgentRunEvent.sequence")

    __table_args__ = (
        Index("ix_agent_runs_project_status", "project_id", "status"),
        Index("ix_agent_runs_created", "created_at"),
    )


# ---------------------------------------------------------------------------
# 22. agent_run_events — 外部 Agent 运行事件表
# ---------------------------------------------------------------------------
class AgentRunEvent(Base):
    __tablename__ = "agent_run_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    run_id = Column(String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(30), nullable=False)  # plan|progress|tool_start|tool_result|...
    status = Column(String(20), nullable=False, default="ok")  # ok|running|error|skipped
    message = Column(Text, nullable=True)
    payload_json = Column(Text, nullable=True)  # JSON string, max 10000 chars
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    run = relationship("AgentRun", back_populates="events")

    __table_args__ = (
        Index("ix_agent_run_events_run_seq", "run_id", "sequence", unique=True),
        Index("ix_agent_run_events_created", "created_at"),
    )


# ---------------------------------------------------------------------------
# 23. external_agent_settings — 外部 Agent 权限设置表
# ---------------------------------------------------------------------------
class ExternalAgentSettings(Base):
    __tablename__ = "external_agent_settings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True)
    enabled_packs = Column(JSON, nullable=False, default=default_external_agent_enabled_packs)
    trusted_local_enabled = Column(Boolean, default=True)
    trusted_local_clients = Column(JSON, nullable=False, default=default_trusted_local_clients)
    require_confirmation_for_writes = Column(Boolean, default=False)
    require_confirmation_for_destructive = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="external_agent_settings")

    __table_args__ = (
        Index("ix_external_agent_settings_project", "project_id", unique=True),
    )


# ---------------------------------------------------------------------------
# 24. public_prompt_packs — 公开提示词包表
# ---------------------------------------------------------------------------
class PublicPromptPack(Base):
    __tablename__ = "public_prompt_packs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)  # null = global builtin
    pack_id = Column(String(100), nullable=False)  # e.g. "chapter_writing_quality"
    version = Column(String(20), nullable=False, default="1.0.0")
    scope = Column(String(50), nullable=False)  # new_project|chapter_writing|chapter_review|...
    title = Column(String(200), nullable=False)
    summary = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=False)
    workflow_json = Column(JSON, nullable=True)  # list of workflow steps
    quality_rubric_json = Column(JSON, nullable=True)
    tool_playbook_json = Column(JSON, nullable=True)
    forbidden_patterns_json = Column(JSON, nullable=True)  # list of strings
    context_policy_json = Column(JSON, nullable=True)
    output_contract_json = Column(JSON, nullable=True)
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    tags_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_public_prompt_packs_project_pack", "project_id", "pack_id"),
        Index("ix_public_prompt_packs_scope", "scope"),
    )


# ---------------------------------------------------------------------------
# 25. method_cards — 方法卡片表
# ---------------------------------------------------------------------------
class MethodCard(Base):
    __tablename__ = "method_cards"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)  # null = global builtin
    card_id = Column(String(100), nullable=False)  # e.g. "chapter_writing_workflow"
    version = Column(String(20), nullable=False, default="1.0.0")
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    content_json = Column(JSON, nullable=False)  # structured method content
    card_type = Column(String(50), nullable=False)  # workflow|rubric|playbook|pattern
    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_method_cards_project_card", "project_id", "card_id"),
        Index("ix_method_cards_type", "card_type"),
    )


# ---------------------------------------------------------------------------
# 26. novel_creation_sessions — 新小说创建会话表
# ---------------------------------------------------------------------------
class NovelCreationSession(Base):
    __tablename__ = "novel_creation_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    source_project_id = Column(String(36), nullable=True)  # project that initiated (may be null)
    created_project_id = Column(String(36), nullable=True)  # project created by this session
    status = Column(String(30), nullable=False, default="drafting")  # drafting|reviewing|applying|completed|failed
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

    __table_args__ = (
        Index("ix_novel_creation_sessions_status", "status"),
    )


# ---------------------------------------------------------------------------
# 26b. novel_creation_stage_runs - resumable generation attempts
# ---------------------------------------------------------------------------
class NovelCreationStageRun(Base):
    __tablename__ = "novel_creation_stage_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey("novel_creation_sessions.id", ondelete="CASCADE"), nullable=False)
    stage = Column(String(50), nullable=False)
    operation = Column(String(30), nullable=False, default="generate")
    status = Column(String(30), nullable=False, default="queued")
    model_source = Column(String(100), nullable=True)
    tool_mode = Column(String(50), nullable=True)
    failure_class = Column(String(50), nullable=True)
    storage_target = Column(String(50), nullable=False, default="session_draft")
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
    run_id = Column(String(36), ForeignKey("novel_creation_stage_runs.id", ondelete="CASCADE"), nullable=False)
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


# ---------------------------------------------------------------------------
# 27. external_agent_global_settings — 全局外部 Agent 权限设置表
# ---------------------------------------------------------------------------
class ExternalAgentGlobalSettings(Base):
    __tablename__ = "external_agent_global_settings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    enabled_packs = Column(JSON, nullable=False, default=default_external_agent_enabled_packs)
    trusted_local_enabled = Column(Boolean, default=True)
    trusted_local_clients = Column(JSON, nullable=False, default=default_trusted_local_clients)
    require_confirmation_for_writes = Column(Boolean, default=False)
    require_confirmation_for_destructive = Column(Boolean, default=False)
    mcp_permission_source = Column(String(30), default="global_settings")  # global_settings | cli_override
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)
