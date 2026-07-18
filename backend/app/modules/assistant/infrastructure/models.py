"""SQLAlchemy persistence models owned by the assistant module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.models_support import generate_uuid
from app.database.session import Base


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False, default="新对话")
    scope = Column(String(50), nullable=False, default="writer")
    current_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    current_outline_node_id = Column(
        String(36), ForeignKey("outline_nodes.id", ondelete="SET NULL"), nullable=True
    )
    model = Column(String(200), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="assistant_conversations")
    messages = relationship(
        "AssistantMessage", back_populates="conversation", cascade="all, delete-orphan"
    )


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(
        String(36), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(String(20), nullable=False)  # user/assistant
    content = Column(Text, nullable=False, default="")
    payload_json = Column(Text, nullable=True)
    status = Column(
        String(20), nullable=False, default="completed"
    )  # running/completed/error/aborted
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation = relationship("AssistantConversation", back_populates="messages")


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


class AssistantRun(Base):
    __tablename__ = "assistant_runs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(
        String(36), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=True
    )
    user_message_id = Column(
        String(36), ForeignKey("assistant_messages.id", ondelete="SET NULL"), nullable=True
    )
    assistant_message_id = Column(
        String(36), ForeignKey("assistant_messages.id", ondelete="SET NULL"), nullable=True
    )
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )
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
    retry_of_step_id = Column(
        String(36), ForeignKey("assistant_run_steps.id", ondelete="SET NULL"), nullable=True
    )
    resolved_step_id = Column(
        String(36), ForeignKey("assistant_run_steps.id", ondelete="SET NULL"), nullable=True
    )
    attempt_no = Column(Integer, default=1, nullable=False)
    depends_on_step_ids = Column(Text, nullable=True)  # JSON array of step IDs
    output_refs = Column(Text, nullable=True)  # JSON object: {resource_type: resource_id}
    planned_next_steps = Column(Text, nullable=True)  # JSON array of tool names
    idempotency_key = Column(String(200), nullable=True)

    run = relationship("AssistantRun", back_populates="steps")
    retry_of = relationship(
        "AssistantRunStep", remote_side="AssistantRunStep.id", foreign_keys=[retry_of_step_id]
    )
    resolved_by = relationship(
        "AssistantRunStep", remote_side="AssistantRunStep.id", foreign_keys=[resolved_step_id]
    )

    __table_args__ = (
        Index("ix_run_steps_idempotency_key", "idempotency_key"),
        Index("ix_run_steps_run_iteration", "run_id", "iteration"),
    )


class AssistantMemory(Base):
    __tablename__ = "assistant_memories"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    category = Column(
        String(30), nullable=False, default="user_preference"
    )  # user_preference/project_fact/writing_style/research_note/workflow_preference
    key = Column(String(200), nullable=False)
    value = Column(Text, nullable=False)
    source = Column(String(50), nullable=True)  # e.g., "web_search", "user", "assistant"
    importance = Column(Integer, nullable=False, default=5)  # 0-10
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChapterDraft(Base):
    __tablename__ = "chapter_drafts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False, default="")
    outline_node_id = Column(String(36), nullable=True)
    context_manifest_id = Column(
        String(36), ForeignKey("context_manifests.id", ondelete="SET NULL"), nullable=True
    )
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class RagDocument(Base):
    __tablename__ = "rag_documents"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_type = Column(
        String(30), nullable=False
    )  # chapter/chapter_summary/outline/character/character_timeline/worldbuilding/assistant_memory
    source_id = Column(String(36), nullable=False)
    content_hash = Column(String(64), nullable=False)
    chunk_count = Column(Integer, nullable=False, default=0)
    indexed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("ix_rag_doc_project_source", "project_id", "source_type", "source_id"),)


class RagChunk(Base):
    __tablename__ = "rag_chunks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    document_id = Column(
        String(36), ForeignKey("rag_documents.id", ondelete="CASCADE"), nullable=False
    )
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


class RagLink(Base):
    __tablename__ = "rag_links"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_chunk_id = Column(String(36), nullable=False)
    target_chunk_id = Column(String(36), nullable=False)
    link_type = Column(
        String(30), nullable=False, default="references"
    )  # references/contradicts/depends_on
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class RagChunkEmbedding(Base):
    __tablename__ = "rag_chunk_embeddings"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    chunk_id = Column(String(36), ForeignKey("rag_chunks.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    embedding_model = Column(String(200), nullable=False)
    index_version = Column(Integer, nullable=False, default=1)
    vector_dim = Column(Integer, nullable=False)
    vector_blob = Column(LargeBinary, nullable=False)
    source_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "chunk_id", "embedding_model", "index_version", name="uq_rag_chunk_embeddings_version"
        ),
        Index("ix_rag_chunk_embeddings_project_model", "project_id", "embedding_model"),
    )


__all__ = [
    "AssistantConversation",
    "AssistantMessage",
    "SystemAssistantConversation",
    "SystemAssistantMessage",
    "AssistantRun",
    "AssistantRunStep",
    "AssistantMemory",
    "ChapterDraft",
    "RagDocument",
    "RagChunk",
    "RagLink",
    "RagChunkEmbedding",
]
