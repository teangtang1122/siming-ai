"""SQLAlchemy persistence models owned by the assistant module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
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
    func,
    select,
    update,
)
from sqlalchemy.orm import Session, relationship

from app.database.models_support import generate_uuid
from app.database.session import Base


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False, default="新对话")
    scope = Column(String(50), nullable=False, default="writer")
    canonical_conversation_id = Column(String(36), nullable=True, unique=True, index=True)
    model = Column(String(512), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="assistant_conversations")
    messages = relationship(
        "AssistantMessage", back_populates="conversation", cascade="all, delete-orphan"
    )
    context_checkpoints = relationship(
        "ConversationContextCheckpoint",
        foreign_keys="ConversationContextCheckpoint.assistant_conversation_id",
        cascade="all, delete-orphan",
    )
    context_state = relationship(
        "ConversationContextState",
        foreign_keys="ConversationContextState.assistant_conversation_id",
        cascade="all, delete-orphan",
        uselist=False,
    )


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(
        String(36), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role = Column(String(20), nullable=False)  # user/assistant
    sequence_no = Column(Integer, nullable=False)
    content = Column(Text, nullable=False, default="")
    payload_json = Column(Text, nullable=True)
    status = Column(
        String(20), nullable=False, default="completed"
    )  # running/completed/error/aborted
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation = relationship("AssistantConversation", back_populates="messages")

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_assistant_messages_conversation_sequence",
        ),
        CheckConstraint("sequence_no > 0", name="ck_assistant_messages_sequence_positive"),
    )


class AssistantConversationReplica(Base):
    """Device-scoped identity for a transcript imported into the workspace.

    ``AssistantConversation.canonical_conversation_id`` already identifies the
    canonical System Assistant conversation used by the PC UI. Mobile-local
    IDs therefore live in this separate mapping and cannot impersonate that
    existing namespace.
    """

    __tablename__ = "assistant_conversation_replicas"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    assistant_conversation_id = Column(
        String(36),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_scope = Column(String(128), nullable=False)
    client_conversation_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "assistant_conversation_id",
            name="uq_assistant_conversation_replica_server",
        ),
        UniqueConstraint(
            "device_scope",
            "client_conversation_id",
            name="uq_assistant_conversation_replica_client",
        ),
        Index(
            "ix_assistant_conversation_replica_owner",
            "project_id",
            "device_scope",
        ),
    )


class AssistantTranscriptImportReceipt(Base):
    """Durable result of one explicit, device-scoped transcript import."""

    __tablename__ = "assistant_transcript_import_receipts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    assistant_conversation_id = Column(
        String(36),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    replica_id = Column(
        String(36),
        ForeignKey("assistant_conversation_replicas.id", ondelete="CASCADE"),
        nullable=False,
    )
    device_scope = Column(String(128), nullable=False)
    idempotency_key = Column(String(200), nullable=False)
    request_hash = Column(String(64), nullable=False)
    source_transcript_revision = Column(Integer, nullable=False)
    source_first_sequence = Column(Integer, nullable=False)
    source_last_sequence = Column(Integer, nullable=False)
    source_message_count = Column(Integer, nullable=False)
    imported_message_count = Column(Integer, nullable=False)
    result_transcript_revision = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "device_scope",
            "idempotency_key",
            name="uq_assistant_transcript_import_device_key",
        ),
        CheckConstraint(
            "source_transcript_revision > 0 "
            "AND source_first_sequence > 0 "
            "AND source_last_sequence >= source_first_sequence "
            "AND source_message_count > 0 "
            "AND imported_message_count >= 0 "
            "AND result_transcript_revision >= source_last_sequence",
            name="ck_assistant_transcript_import_ranges",
        ),
        Index(
            "ix_assistant_transcript_import_conversation",
            "assistant_conversation_id",
            "created_at",
        ),
    )


class SystemAssistantConversation(Base):
    __tablename__ = "system_assistant_conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    title = Column(String(200), nullable=False, default="新对话")
    scope_type = Column(String(30), nullable=False)
    scope_id = Column(String(36), nullable=True)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    creation_session_id = Column(String(36), nullable=True)
    user_brief = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    messages = relationship(
        "SystemAssistantMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    context_checkpoints = relationship(
        "ConversationContextCheckpoint",
        foreign_keys="ConversationContextCheckpoint.system_conversation_id",
        cascade="all, delete-orphan",
    )
    context_state = relationship(
        "ConversationContextState",
        foreign_keys="ConversationContextState.system_conversation_id",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        Index("ix_system_assistant_conversation_scope", "scope_type", "scope_id", "updated_at"),
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
    sequence_no = Column(Integer, nullable=False)
    content = Column(Text, nullable=False, default="")
    run_id = Column(String(36), nullable=True)
    operation_id = Column(
        String(36), ForeignKey("operation_runs.id", ondelete="SET NULL"), nullable=True
    )

    message_type = Column(String(30), nullable=False, default="text")
    payload_json = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="completed")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation = relationship("SystemAssistantConversation", back_populates="messages")

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_system_assistant_messages_conversation_sequence",
        ),
        CheckConstraint(
            "sequence_no > 0",
            name="ck_system_assistant_messages_sequence_positive",
        ),
    )


class ConversationContextCheckpoint(Base):
    """A derived, auditable checkpoint for one canonical Agent conversation."""

    __tablename__ = "conversation_context_checkpoints"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_kind = Column(String(20), nullable=False)
    assistant_conversation_id = Column(
        String(36),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=True,
    )
    system_conversation_id = Column(
        String(36),
        ForeignKey("system_assistant_conversations.id", ondelete="CASCADE"),
        nullable=True,
    )
    parent_checkpoint_id = Column(
        String(36),
        ForeignKey("conversation_context_checkpoints.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_version = Column(Integer, nullable=False, default=1)
    schema_version = Column(String(50), nullable=False, default="conversation_checkpoint.v1")
    status = Column(String(20), nullable=False, default="pending")
    source_first_sequence = Column(Integer, nullable=False)
    source_last_sequence = Column(Integer, nullable=False)
    source_message_count = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    transcript_revision = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(String(200), nullable=False)
    model_binding_json = Column(JSON, nullable=False, default=dict)
    model_binding_fingerprint = Column(String(64), nullable=True)
    semantic_navigation_json = Column(JSON, nullable=False, default=dict)
    author_quotes_json = Column(JSON, nullable=False, default=list)
    execution_ledger_json = Column(JSON, nullable=False, default=list)
    project_refs_json = Column(JSON, nullable=False, default=list)
    validation_json = Column(JSON, nullable=False, default=dict)
    original_tokens = Column(Integer, nullable=True)
    checkpoint_tokens = Column(Integer, nullable=True)
    error_code = Column(String(100), nullable=True)
    error_detail = Column(Text, nullable=True)
    cancel_requested_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    sources = relationship(
        "ConversationContextCheckpointSource",
        back_populates="checkpoint",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "((conversation_kind = 'workspace' "
            "AND assistant_conversation_id IS NOT NULL "
            "AND system_conversation_id IS NULL) "
            "OR (conversation_kind = 'creation' "
            "AND assistant_conversation_id IS NULL "
            "AND system_conversation_id IS NOT NULL))",
            name="ck_context_checkpoint_conversation_owner",
        ),
        CheckConstraint(
            "status IN ('pending', 'compressing', 'ready', 'failed', 'cancelled', 'superseded')",
            name="ck_context_checkpoint_status",
        ),
        CheckConstraint(
            "source_first_sequence > 0 "
            "AND source_last_sequence >= source_first_sequence "
            "AND source_message_count > 0",
            name="ck_context_checkpoint_source_range",
        ),
        CheckConstraint(
            "policy_version > 0 AND transcript_revision >= 0",
            name="ck_context_checkpoint_versions",
        ),
        CheckConstraint(
            "(original_tokens IS NULL OR original_tokens >= 0) "
            "AND (checkpoint_tokens IS NULL OR checkpoint_tokens >= 0)",
            name="ck_context_checkpoint_tokens",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_context_checkpoint_idempotency",
        ),
        Index(
            "ix_context_checkpoint_assistant_status",
            "assistant_conversation_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_context_checkpoint_system_status",
            "system_conversation_id",
            "status",
            "created_at",
        ),
        Index("ix_context_checkpoint_parent", "parent_checkpoint_id"),
    )


class ConversationContextCheckpointSource(Base):
    """One immutable provenance reference used to build a checkpoint."""

    __tablename__ = "conversation_context_checkpoint_sources"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    checkpoint_id = Column(
        String(36),
        ForeignKey("conversation_context_checkpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_kind = Column(String(30), nullable=False)
    # Creation uses ``creation-turn:<assistant message UUID>`` as its canonical
    # source-run identity; workspace source identities remain UUID-sized.
    source_id = Column(String(128), nullable=False)
    source_sequence = Column(Integer, nullable=True)
    source_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    checkpoint = relationship("ConversationContextCheckpoint", back_populates="sources")

    __table_args__ = (
        UniqueConstraint(
            "checkpoint_id",
            "source_kind",
            "source_id",
            name="uq_context_checkpoint_source_identity",
        ),
        CheckConstraint(
            "source_kind IN ('message', 'run_step', 'prior_segment')",
            name="ck_context_checkpoint_source_kind",
        ),
        CheckConstraint(
            "source_sequence IS NULL OR source_sequence > 0",
            name="ck_context_checkpoint_source_sequence",
        ),
        Index(
            "ix_context_checkpoint_source_sequence",
            "checkpoint_id",
            "source_sequence",
        ),
    )


class ConversationContextState(Base):
    """CAS-protected active checkpoint pointer for one conversation."""

    __tablename__ = "conversation_context_states"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_kind = Column(String(20), nullable=False)
    assistant_conversation_id = Column(
        String(36),
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=True,
    )
    system_conversation_id = Column(
        String(36),
        ForeignKey("system_assistant_conversations.id", ondelete="CASCADE"),
        nullable=True,
    )
    revision = Column(Integer, nullable=False, default=0)
    active_checkpoint_id = Column(
        String(36),
        ForeignKey("conversation_context_checkpoints.id", ondelete="SET NULL"),
        nullable=True,
    )
    active_source_last_sequence = Column(Integer, nullable=False, default=0)
    last_budget_json = Column(JSON, nullable=False, default=dict)
    last_compacted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "assistant_conversation_id",
            name="uq_context_state_assistant_conversation",
        ),
        UniqueConstraint(
            "system_conversation_id",
            name="uq_context_state_system_conversation",
        ),
        CheckConstraint(
            "((conversation_kind = 'workspace' "
            "AND assistant_conversation_id IS NOT NULL "
            "AND system_conversation_id IS NULL) "
            "OR (conversation_kind = 'creation' "
            "AND assistant_conversation_id IS NULL "
            "AND system_conversation_id IS NOT NULL))",
            name="ck_context_state_conversation_owner",
        ),
        CheckConstraint(
            "revision >= 0 AND active_source_last_sequence >= 0",
            name="ck_context_state_versions",
        ),
        Index("ix_context_state_active_checkpoint", "active_checkpoint_id"),
    )


def reserve_message_sequence_range(
    session: Session,
    *,
    conversation_model: type,
    message_model: type,
    conversation_id: str,
    count: int,
) -> tuple[int, ...]:
    """Reserve a consecutive message sequence range inside the caller transaction.

    PostgreSQL locks the owning conversation row. SQLite obtains its database
    writer reservation with a harmless UPDATE, which also enters Siming's
    cross-process write coordinator before MAX(sequence_no) is read.
    """

    if count <= 0:
        raise ValueError("count must be positive")
    bind = session.get_bind()
    if bind.dialect.name == "sqlite":
        result = session.execute(
            update(conversation_model)
            .where(conversation_model.id == conversation_id)
            .values(updated_at=conversation_model.updated_at)
        )
        if result.rowcount != 1:
            raise ValueError("conversation does not exist")
    else:
        owner = session.execute(
            select(conversation_model.id)
            .where(conversation_model.id == conversation_id)
            .with_for_update()
        ).scalar_one_or_none()
        if owner is None:
            raise ValueError("conversation does not exist")

    last_sequence = session.execute(
        select(func.max(message_model.sequence_no)).where(
            message_model.conversation_id == conversation_id
        )
    ).scalar_one()
    first_sequence = int(last_sequence or 0) + 1
    return tuple(range(first_sequence, first_sequence + count))


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
    model = Column(String(512), nullable=True)
    current_iteration = Column(Integer, default=0)
    direct_mcp_lease_hash = Column(String(64), nullable=True)
    direct_mcp_lease_iteration = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    final_reply = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="assistant_runs")
    steps = relationship("AssistantRunStep", back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index(
            "uq_assistant_runs_direct_mcp_lease_hash",
            "direct_mcp_lease_hash",
            unique=True,
        ),
    )


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
    direct_mcp_call_key = Column(String(200), nullable=True)

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
        Index(
            "uq_run_steps_direct_mcp_call_key",
            "direct_mcp_call_key",
            unique=True,
        ),
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
    saved_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    # ``new`` drafts are promoted with POST. ``revision`` drafts are review
    # candidates for an existing chapter and can only be accepted with PUT.
    draft_kind = Column(String(20), nullable=False, default="new")
    target_chapter_id = Column(
        String(36), ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    base_chapter_version = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index(
            "uq_chapter_drafts_project_pending",
            "project_id",
            unique=True,
            sqlite_where=(status == "pending"),
            postgresql_where=(status == "pending"),
        ),
    )


class OutlineDraft(Base):
    """Author-visible, unsaved outline proposal produced by the Agent."""

    __tablename__ = "outline_drafts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    context_manifest_id = Column(
        String(36), ForeignKey("context_manifests.id", ondelete="SET NULL"), nullable=True
    )
    parent_id = Column(String(36), nullable=True)
    insert_after_id = Column(String(36), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    nodes_json = Column(JSON, nullable=False, default=list)
    design_notes = Column(Text, nullable=False, default="")
    context_selection_digest = Column(String(64), nullable=False)
    base_outline_hash = Column(String(64), nullable=False)
    saved_outline_node_ids = Column(JSON, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index(
            "uq_outline_drafts_project_pending",
            "project_id",
            unique=True,
            sqlite_where=(status == "pending"),
            postgresql_where=(status == "pending"),
        ),
    )


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
    "AssistantConversationReplica",
    "AssistantMessage",
    "AssistantTranscriptImportReceipt",
    "SystemAssistantConversation",
    "SystemAssistantMessage",
    "ConversationContextCheckpoint",
    "ConversationContextCheckpointSource",
    "ConversationContextState",
    "reserve_message_sequence_range",
    "AssistantRun",
    "AssistantRunStep",
    "AssistantMemory",
    "ChapterDraft",
    "OutlineDraft",
    "RagDocument",
    "RagChunk",
    "RagLink",
    "RagChunkEmbedding",
]
