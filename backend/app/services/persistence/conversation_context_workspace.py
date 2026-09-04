"""Query helpers shared by the SQLAlchemy conversation-context adapter."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.database.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
)
from app.modules.assistant.infrastructure.models import (
    ConversationContextCheckpoint,
    ConversationContextState,
    SystemAssistantMessage,
)

_SOURCE_KINDS = {"message", "run_step", "prior_segment"}
_CREATION_SOURCE_RUN_PREFIX = "creation-turn:"


def _creation_source_message_id(source_run_id: str) -> str | None:
    """Resolve a canonical Creation ledger run ID to its durable message."""

    if not source_run_id.startswith(_CREATION_SOURCE_RUN_PREFIX):
        return None
    message_id = source_run_id.removeprefix(_CREATION_SOURCE_RUN_PREFIX).strip()
    if not message_id or len(message_id) > 36:
        return None
    if source_run_id != f"{_CREATION_SOURCE_RUN_PREFIX}{message_id}":
        return None
    return message_id


def checkpoint_identity_filters(
    conversation_kind: str,
    conversation_id: str,
) -> tuple[Any, ...]:
    if conversation_kind == "workspace":
        return (
            ConversationContextCheckpoint.conversation_kind == "workspace",
            ConversationContextCheckpoint.assistant_conversation_id == conversation_id,
            ConversationContextCheckpoint.system_conversation_id.is_(None),
        )
    if conversation_kind == "creation":
        return (
            ConversationContextCheckpoint.conversation_kind == "creation",
            ConversationContextCheckpoint.assistant_conversation_id.is_(None),
            ConversationContextCheckpoint.system_conversation_id == conversation_id,
        )
    return (False,)


def state_identity_filters(
    conversation_kind: str,
    conversation_id: str,
) -> tuple[Any, ...]:
    if conversation_kind == "workspace":
        return (
            ConversationContextState.conversation_kind == "workspace",
            ConversationContextState.assistant_conversation_id == conversation_id,
            ConversationContextState.system_conversation_id.is_(None),
        )
    if conversation_kind == "creation":
        return (
            ConversationContextState.conversation_kind == "creation",
            ConversationContextState.assistant_conversation_id.is_(None),
            ConversationContextState.system_conversation_id == conversation_id,
        )
    return (False,)


def checkpoint_source_belongs(
    db: Session,
    conversation_kind: str,
    conversation_id: str,
    source: dict[str, Any],
) -> bool:
    source_kind = str(source.get("source_kind") or "")
    source_id = str(source.get("source_id") or "")
    source_sequence = source.get("source_sequence")
    if source_kind not in _SOURCE_KINDS or not source_id:
        return False
    if source_kind == "message":
        model = AssistantMessage if conversation_kind == "workspace" else SystemAssistantMessage
        message = (
            db.query(model)
            .filter(
                model.id == source_id,
                model.conversation_id == conversation_id,
            )
            .first()
        )
        return bool(
            message and source_sequence is not None and int(source_sequence) == message.sequence_no
        )
    if source_kind == "prior_segment":
        return bool(
            db.query(ConversationContextCheckpoint.id)
            .filter(
                ConversationContextCheckpoint.id == source_id,
                *checkpoint_identity_filters(conversation_kind, conversation_id),
            )
            .first()
        )
    if conversation_kind == "workspace":
        return bool(
            db.query(AssistantRunStep.id)
            .join(AssistantRun, AssistantRun.id == AssistantRunStep.run_id)
            .join(
                AssistantConversation,
                AssistantConversation.id == AssistantRun.conversation_id,
            )
            .filter(
                AssistantRunStep.id == source_id,
                AssistantRun.conversation_id == conversation_id,
                AssistantRun.project_id == AssistantConversation.project_id,
                AssistantRunStep.project_id == AssistantRun.project_id,
            )
            .first()
        )
    # Creation turns keep a canonical, namespaced source-run identity even
    # though their durable ownership anchor is the sealed assistant message.
    # Never compare that identity with the optional business/stage ``run_id``.
    source_message_id = _creation_source_message_id(source_id)
    if source_message_id is None:
        return False
    return bool(
        db.query(SystemAssistantMessage.id)
        .filter(
            SystemAssistantMessage.id == source_message_id,
            SystemAssistantMessage.conversation_id == conversation_id,
            SystemAssistantMessage.role == "assistant",
        )
        .first()
    )
