"""Persisted system-level assistant conversations."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..database.models import SystemAssistantConversation, SystemAssistantMessage
from ..database.session import get_db

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


def _title_from_message(message: str) -> str:
    title = " ".join((message or "").strip().split())
    if not title:
        return "新对话"
    return title[:36] + ("..." if len(title) > 36 else "")


def _conversation_dict(conversation: SystemAssistantConversation, message_count: int | None = None) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "scope": "system",
        "message_count": message_count,
        "creation_session_id": conversation.creation_session_id,
        "user_brief": conversation.user_brief,
        "blueprints": conversation.blueprint_json or [],
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def _message_dict(message: SystemAssistantMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "payload": message.payload_json,
        "status": message.status,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "updated_at": message.updated_at.isoformat() if message.updated_at else None,
    }


def _get_conversation(db: Session, conversation_id: str) -> SystemAssistantConversation:
    conversation = (
        db.query(SystemAssistantConversation)
        .filter(SystemAssistantConversation.id == conversation_id)
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="系统助手对话不存在")
    return conversation


@router.get("/ai/system-assistant/conversations")
async def list_system_conversations(db: Session = Depends(get_db)):
    conversations = (
        db.query(SystemAssistantConversation)
        .order_by(SystemAssistantConversation.updated_at.desc(), SystemAssistantConversation.created_at.desc())
        .all()
    )
    items = []
    for conversation in conversations:
        message_count = (
            db.query(SystemAssistantMessage)
            .filter(SystemAssistantMessage.conversation_id == conversation.id)
            .count()
        )
        items.append(_conversation_dict(conversation, message_count))
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.post("/ai/system-assistant/conversations")
async def create_system_conversation(payload: SystemConversationCreate, db: Session = Depends(get_db)):
    conversation = SystemAssistantConversation(title=payload.title.strip() or "新对话")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return ApiResponse.success(data={"conversation": _conversation_dict(conversation, 0)})


@router.get("/ai/system-assistant/conversations/{conversation_id}")
async def get_system_conversation(conversation_id: str, db: Session = Depends(get_db)):
    conversation = _get_conversation(db, conversation_id)
    messages = (
        db.query(SystemAssistantMessage)
        .filter(SystemAssistantMessage.conversation_id == conversation.id)
        .order_by(
            SystemAssistantMessage.created_at.asc(),
            SystemAssistantMessage.role.desc(),
            SystemAssistantMessage.updated_at.asc(),
            SystemAssistantMessage.id.asc(),
        )
        .all()
    )
    return ApiResponse.success(data={
        "conversation": _conversation_dict(conversation, len(messages)),
        "messages": [_message_dict(message) for message in messages],
    })


@router.post("/ai/system-assistant/conversations/{conversation_id}/turns")
async def append_system_turn(
    conversation_id: str,
    payload: SystemTurnCreate,
    db: Session = Depends(get_db),
):
    conversation = _get_conversation(db, conversation_id)
    if conversation.title == "新对话":
        conversation.title = _title_from_message(payload.user_content)
    if payload.creation_session_id is not None:
        conversation.creation_session_id = payload.creation_session_id or None
    if payload.user_brief is not None:
        conversation.user_brief = payload.user_brief or None
    if payload.blueprints is not None:
        conversation.blueprint_json = payload.blueprints

    user_message = SystemAssistantMessage(
        conversation_id=conversation.id,
        role="user",
        content=payload.user_content,
        status="completed",
    )
    assistant_message = SystemAssistantMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=payload.assistant_content,
        status=payload.status,
        payload_json=payload.payload,
    )
    db.add_all([user_message, assistant_message])
    db.commit()
    db.refresh(conversation)
    db.refresh(user_message)
    db.refresh(assistant_message)
    return ApiResponse.success(data={
        "conversation": _conversation_dict(conversation),
        "messages": [_message_dict(user_message), _message_dict(assistant_message)],
    })


@router.delete("/ai/system-assistant/conversations/{conversation_id}")
async def delete_system_conversation(conversation_id: str, db: Session = Depends(get_db)):
    conversation = _get_conversation(db, conversation_id)
    db.delete(conversation)
    db.commit()
    return ApiResponse.success(data={"id": conversation_id})
