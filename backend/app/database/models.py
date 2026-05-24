"""Database models — all 17 tables for the novel writing AI agent."""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Integer, DateTime, Boolean, ForeignKey, Date,
)
from sqlalchemy.orm import relationship
from .session import Base


def generate_uuid():
    """Generate a new UUID string."""
    return str(uuid.uuid4())


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
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    worldbuilding_entries = relationship("WorldbuildingEntry", back_populates="project", cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="project", cascade="all, delete-orphan")
    character_relationships = relationship("CharacterRelationship", cascade="all, delete-orphan")
    outline_nodes = relationship("OutlineNode", back_populates="project", cascade="all, delete-orphan")
    chapters = relationship("Chapter", back_populates="project", cascade="all, delete-orphan")
    writing_logs = relationship("WritingLog", back_populates="project", cascade="all, delete-orphan")
    deconstruction_reports = relationship("DeconstructionReport", back_populates="project", cascade="all, delete-orphan")
    assistant_conversations = relationship("AssistantConversation", back_populates="project", cascade="all, delete-orphan")


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
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="worldbuilding_entries")


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
    current_version = Column(Integer, default=1)
    is_evolution_tracked = Column(Boolean, default=True)  # FR-018
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="characters")
    versions = relationship("CharacterVersion", back_populates="character", cascade="all, delete-orphan")
    ai_config = relationship("CharacterAIConfig", back_populates="character", uselist=False, cascade="all, delete-orphan")
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
# 13. writing_logs — 写作日志表
# ---------------------------------------------------------------------------
class WritingLog(Base):
    __tablename__ = "writing_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    total_words = Column(Integer, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    project = relationship("Project", back_populates="writing_logs")


# ---------------------------------------------------------------------------
# 14. api_configs — API配置表
# ---------------------------------------------------------------------------
class APIConfig(Base):
    __tablename__ = "api_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    provider = Column(String(50), nullable=False, unique=True)  # openai/anthropic/deepseek/qwen
    api_key_encrypted = Column(Text, nullable=False)
    default_model = Column(String(100), nullable=False)
    is_global_default = Column(Boolean, default=False)
    base_url_override = Column(String(500), nullable=True)
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
# 20. assistant_memories — 智能体持久记忆表
# ---------------------------------------------------------------------------
class AssistantMemory(Base):
    __tablename__ = "assistant_memories"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    category = Column(String(30), nullable=False, default="note")  # preference/search_result/note/fact
    key = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    source = Column(String(50), nullable=True)  # e.g., "web_search", "user", "assistant"
    importance = Column(Integer, nullable=False, default=5)  # 0-10
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
