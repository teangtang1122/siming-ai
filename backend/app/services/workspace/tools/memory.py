"""Memory tools — persistent remember / recall / forget for the workspace assistant."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import AssistantMemory


async def remember(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Save a memory entry. Returns the created memory ID."""
    key = str(args.get("key") or "").strip()
    value = str(args.get("value") or "").strip()
    if not key or not value:
        return {"tool": "remember", "status": "error", "detail": "key 和 value 不能为空"}
    if len(value) > 4000:
        value = value[:4000]

    category = str(args.get("category") or "note").strip()
    if category not in {"preference", "search_result", "note", "fact"}:
        category = "note"

    importance = max(0, min(10, int(args.get("importance") or 5)))

    # Upsert: if a memory with the same project_id + key exists, update it
    existing = (
        db.query(AssistantMemory)
        .filter(
            AssistantMemory.project_id == project_id,
            AssistantMemory.key == key,
        )
        .first()
    )
    if existing:
        existing.value = value
        existing.category = category
        existing.importance = importance
        existing.source = str(args.get("source") or "assistant")[:50]
        db.commit()
        return {
            "tool": "remember",
            "status": "ok",
            "detail": f"已更新记忆「{key}」",
            "data": [{"id": existing.id, "key": key, "updated": True}],
        }

    memory = AssistantMemory(
        project_id=project_id,
        category=category,
        key=key,
        value=value,
        source=str(args.get("source") or "assistant")[:50],
        importance=importance,
    )
    db.add(memory)
    db.commit()
    return {
        "tool": "remember",
        "status": "ok",
        "detail": f"已保存记忆「{key}」",
        "data": [{"id": memory.id, "key": key, "updated": False}],
    }


async def recall(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Retrieve memories by keyword search across key and value."""
    query = str(args.get("query") or "").strip()
    category = str(args.get("category") or "").strip()
    limit = max(1, min(int(args.get("limit") or 10), 20))

    base = db.query(AssistantMemory).filter(
        AssistantMemory.project_id == project_id,
    )
    if query:
        base = base.filter(
            AssistantMemory.key.ilike(f"%{query}%")
            | AssistantMemory.value.ilike(f"%{query}%")
        )
    if category and category in {"preference", "search_result", "note", "fact"}:
        base = base.filter(AssistantMemory.category == category)

    memories = (
        base.order_by(AssistantMemory.importance.desc(), AssistantMemory.updated_at.desc())
        .limit(limit)
        .all()
    )
    data = [
        {
            "id": m.id,
            "category": m.category,
            "key": m.key,
            "value": m.value,
            "source": m.source,
            "importance": m.importance,
        }
        for m in memories
    ]
    return {
        "tool": "recall",
        "status": "ok",
        "detail": f"找到 {len(data)} 条记忆" if data else "没有匹配的记忆",
        "data": data,
    }


async def forget(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Delete memories by id or key match."""
    memory_id = str(args.get("id") or "").strip()
    key = str(args.get("key") or "").strip()

    if memory_id:
        memory = db.query(AssistantMemory).filter(
            AssistantMemory.id == memory_id,
            AssistantMemory.project_id == project_id,
        ).first()
        if not memory:
            return {"tool": "forget", "status": "error", "detail": "未找到该记忆"}
        db.delete(memory)
        db.commit()
        return {"tool": "forget", "status": "ok", "detail": f"已删除记忆「{memory.key}」"}

    if key:
        deleted = (
            db.query(AssistantMemory)
            .filter(
                AssistantMemory.project_id == project_id,
                AssistantMemory.key == key,
            )
            .delete()
        )
        db.commit()
        if deleted:
            return {"tool": "forget", "status": "ok", "detail": f"已删除 {deleted} 条匹配「{key}」的记忆"}
        return {"tool": "forget", "status": "ok", "detail": f"没有匹配「{key}」的记忆"}

    return {"tool": "forget", "status": "error", "detail": "需要提供 id 或 key"}
