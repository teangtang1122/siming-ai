"""Owner-scoped durable context views for Creation Agent conversations."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.exceptions import NotFoundError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.assistant.application.system_conversations import SystemConversationStore
from ..modules.assistant.interfaces.system_conversation_dependencies import (
    get_system_conversation_store,
)
from ..modules.creation.interfaces.session_dependencies import novel_creation_session_store
from ..schemas.conversation_context import (
    ConversationCheckpointDetailResponse,
    ConversationCheckpointListResponse,
    ConversationContextStateResponse,
)
from ..services.conversation_context.errors import ConversationContextError
from ..services.conversation_context.public_api import (
    public_checkpoint_detail_payload,
    public_checkpoint_list_payload,
    public_context_state_payload,
)
from ..services.creation_agent_turn_runtime import (
    CreationTurnScopeError,
    creation_agent_conversation,
)
from ..services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace

router = APIRouter(tags=["novel-creation"])
DbSession = Annotated[Session, Depends(get_db)]
ConversationStore = Annotated[
    SystemConversationStore,
    Depends(get_system_conversation_store),
]
_BASE = (
    "/novel-creation/sessions/{session_id}/conversations/{conversation_id}"
)
_CONVERSATION_KIND = "creation"


def _creation_context_store(
    db: Session,
    conversations: SystemConversationStore,
    *,
    session_id: str,
    conversation_id: str,
) -> SqlAlchemyAssistantWorkspace:
    if novel_creation_session_store(db).session(session_id) is None:
        raise NotFoundError("立项草稿不存在")
    try:
        detail = creation_agent_conversation(
            conversations,
            session_id=session_id,
            conversation_id=conversation_id,
        )
    except (CreationTurnScopeError, NotFoundError) as exc:
        # Missing, foreign-session and guessed conversations are deliberately
        # indistinguishable at this owner-scoped boundary.
        raise NotFoundError("立项助手对话不存在") from exc
    canonical_id = str(
        ((detail or {}).get("conversation") or {}).get("id") or ""
    )
    if canonical_id != conversation_id:
        raise NotFoundError("立项助手对话不存在")
    return SqlAlchemyAssistantWorkspace(db)


def _checkpoint_detail(
    store: SqlAlchemyAssistantWorkspace,
    *,
    session_id: str,
    conversation_id: str,
    checkpoint_id: str,
) -> dict[str, Any]:
    if store.context_checkpoint(
        _CONVERSATION_KIND,
        conversation_id,
        checkpoint_id,
        owner_id=session_id,
    ) is None:
        raise NotFoundError("会话 checkpoint 不存在")
    try:
        return public_checkpoint_detail_payload(
            store=store,
            conversation_kind=_CONVERSATION_KIND,
            conversation_id=conversation_id,
            owner_id=session_id,
            checkpoint_id=checkpoint_id,
        )
    except ConversationContextError as exc:
        raise NotFoundError("会话 checkpoint 不存在") from exc


@router.get(
    f"{_BASE}/context-state",
    response_model=ApiResponse[ConversationContextStateResponse],
)
async def get_creation_conversation_context_state(
    session_id: str,
    conversation_id: str,
    db: DbSession,
    conversations: ConversationStore,
):
    store = _creation_context_store(
        db,
        conversations,
        session_id=session_id,
        conversation_id=conversation_id,
    )
    return ApiResponse.success(
        data=public_context_state_payload(
            store=store,
            conversation_kind=_CONVERSATION_KIND,
            conversation_id=conversation_id,
            owner_id=session_id,
        )
    )


@router.get(
    f"{_BASE}/checkpoints",
    response_model=ApiResponse[ConversationCheckpointListResponse],
)
async def list_creation_conversation_checkpoints(
    session_id: str,
    conversation_id: str,
    db: DbSession,
    conversations: ConversationStore,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    store = _creation_context_store(
        db,
        conversations,
        session_id=session_id,
        conversation_id=conversation_id,
    )
    return ApiResponse.success(
        data=public_checkpoint_list_payload(
            store=store,
            conversation_kind=_CONVERSATION_KIND,
            conversation_id=conversation_id,
            owner_id=session_id,
            offset=offset,
            limit=limit,
        )
    )


@router.get(
    f"{_BASE}/checkpoints/{{checkpoint_id}}",
    response_model=ApiResponse[ConversationCheckpointDetailResponse],
)
async def get_creation_conversation_checkpoint(
    session_id: str,
    conversation_id: str,
    checkpoint_id: str,
    db: DbSession,
    conversations: ConversationStore,
):
    store = _creation_context_store(
        db,
        conversations,
        session_id=session_id,
        conversation_id=conversation_id,
    )
    return ApiResponse.success(
        data=_checkpoint_detail(
            store,
            session_id=session_id,
            conversation_id=conversation_id,
            checkpoint_id=checkpoint_id,
        )
    )


__all__ = ["router"]
