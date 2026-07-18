"""Persisted system-level assistant conversations."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..core.response import ApiResponse
from ..modules.assistant.application.system_conversations import SystemConversationStore
from ..modules.assistant.interfaces.system_conversation_dependencies import (
    get_system_conversation_store,
)

router = APIRouter(tags=["system-assistant"])


class SystemConversationCreate(BaseModel):
    title: str = ""


class SystemTurnCreate(BaseModel):
    user_content: str = Field(min_length=1)
    assistant_content: str = ""
    status: str = "completed"
    payload: dict[str, Any] | None = None
    creation_session_id: str | None = None
    user_brief: str | None = None
    blueprints: list[dict[str, Any]] | None = None


@router.get("/ai/system-assistant/conversations")
async def list_system_conversations(
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    return ApiResponse.success(data=conversations.list())


@router.post("/ai/system-assistant/conversations")
async def create_system_conversation(
    payload: SystemConversationCreate,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    return ApiResponse.success(data=conversations.create(payload.title))


@router.get("/ai/system-assistant/conversations/{conversation_id}")
async def get_system_conversation(
    conversation_id: str,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    return ApiResponse.success(data=conversations.get(conversation_id))


@router.post("/ai/system-assistant/conversations/{conversation_id}/turns")
async def append_system_turn(
    conversation_id: str,
    payload: SystemTurnCreate,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    return ApiResponse.success(
        data=conversations.append_turn(conversation_id, payload.model_dump())
    )


@router.delete("/ai/system-assistant/conversations/{conversation_id}")
async def delete_system_conversation(
    conversation_id: str,
    conversations: Annotated[
        SystemConversationStore,
        Depends(get_system_conversation_store),
    ],
):
    return ApiResponse.success(data=conversations.delete(conversation_id))


__all__ = [
    "SystemConversationCreate",
    "SystemTurnCreate",
    "append_system_turn",
    "create_system_conversation",
    "delete_system_conversation",
    "get_system_conversation",
    "list_system_conversations",
    "router",
]
