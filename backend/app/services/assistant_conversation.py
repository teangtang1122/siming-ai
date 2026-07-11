"""Assistant conversation helpers — CRUD, history, context merging, serialization."""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from ..core.exceptions import NotFoundError
from ..database.models import AssistantConversation, AssistantMemory, AssistantMessage
from ..prompts.workspace_assistant import format_memory_context, format_previous_search_context


def _assistant_history_text(history: list[dict], limit: int = 8) -> str:
    lines = []
    for item in (history or [])[-limit:]:
        if not isinstance(item, dict):
            continue
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        max_len = 4000 if item.get("role") == "user" else 600
        lines.append(f"{role}：{content[:max_len]}")
    return "\n\n".join(lines) or "暂无对话历史。"


def _assistant_conversation_to_dict(conversation: AssistantConversation, message_count: Optional[int] = None) -> dict:
    return {
        "id": conversation.id,
        "project_id": conversation.project_id,
        "title": conversation.title,
        "scope": conversation.scope,
        "current_chapter_id": conversation.current_chapter_id,
        "current_outline_node_id": conversation.current_outline_node_id,
        "model": conversation.model,
        "message_count": message_count,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def _assistant_message_to_dict(message: AssistantMessage) -> dict:
    payload = None
    if message.payload_json:
        try:
            payload = json.loads(message.payload_json)
        except Exception:
            payload = None
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "payload": payload,
        "status": message.status,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "updated_at": message.updated_at.isoformat() if message.updated_at else None,
    }


def _get_assistant_conversation_or_404(
    db: Session,
    project_id: str,
    conversation_id: str,
) -> AssistantConversation:
    conversation = (
        db.query(AssistantConversation)
        .filter(
            AssistantConversation.id == conversation_id,
            AssistantConversation.project_id == project_id,
        )
        .first()
    )
    if not conversation:
        raise NotFoundError("助手对话不存在")
    return conversation


def _assistant_history_from_messages(
    db: Session,
    conversation_id: str,
    before_message_id: Optional[str] = None,
    limit: int = 8,
) -> str:
    messages = (
        db.query(AssistantMessage)
        .filter(AssistantMessage.conversation_id == conversation_id)
        .order_by(
            AssistantMessage.created_at.asc(),
            AssistantMessage.role.desc(),
            AssistantMessage.updated_at.asc(),
            AssistantMessage.id.asc(),
        )
        .all()
    )
    history: list[dict] = []
    for message in messages:
        if before_message_id and message.id == before_message_id:
            break
        if message.status not in {"completed", "running"}:
            continue
        history.append({"role": message.role, "content": message.content})
    return _assistant_history_text(history, limit=limit)


def _previous_search_context_from_messages(
    db: Session,
    conversation_id: str,
    before_message_id: Optional[str] = None,
) -> str:
    messages = (
        db.query(AssistantMessage)
        .filter(
            AssistantMessage.conversation_id == conversation_id,
            AssistantMessage.role == "assistant",
            AssistantMessage.status.in_({"completed", "running"}),
        )
        .order_by(AssistantMessage.created_at.desc())
        .all()
    )
    merged: dict[str, dict] = {}
    seen_ids: dict[str, set] = {}
    for message in messages:
        if before_message_id and message.id == before_message_id:
            continue
        if not message.payload_json:
            continue
        try:
            payload = json.loads(message.payload_json)
        except Exception:
            continue
        ctx = payload.get("searched_context")
        if not isinstance(ctx, list):
            continue
        for group in ctx:
            if not isinstance(group, dict):
                continue
            tool = str(group.get("tool") or "?")
            data = group.get("data")
            if not isinstance(data, list):
                continue
            if tool not in merged:
                merged[tool] = {"tool": tool, "detail": str(group.get("detail") or ""), "data": []}
                seen_ids[tool] = set()
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                eid = entry.get("id", "")
                if eid and eid in seen_ids[tool]:
                    continue
                if eid:
                    seen_ids[tool].add(eid)
                merged[tool]["data"].append(entry)
    all_search_results = list(merged.values())
    return format_previous_search_context(all_search_results)


def _assistant_title_from_message(message: str) -> str:
    title = " ".join((message or "").strip().split())
    if not title:
        return "新对话"
    return title[:36] + ("..." if len(title) > 36 else "")


def _memory_context_for_project(db: Session, project_id: str, limit: int = 15) -> str:
    """Query recent high-importance memories for injection into the system prompt."""
    memories = (
        db.query(AssistantMemory)
        .filter(AssistantMemory.project_id == project_id)
        .order_by(AssistantMemory.importance.desc(), AssistantMemory.updated_at.desc())
        .limit(limit)
        .all()
    )
    return format_memory_context([
        {
            "category": m.category,
            "key": m.key,
            "value": m.value,
        }
        for m in memories
    ])
