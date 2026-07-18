"""Memory tools — persistent remember / recall / forget / list_memories for the workspace assistant."""
from __future__ import annotations

from app.architecture.uow import commit_session

import re
from typing import Any

from sqlalchemy.orm import Session

from ....database.models import AssistantMemory
from ....services.rag.indexer import refresh_source_index, delete_source_index

VALID_CATEGORIES = {
    "user_preference", "project_fact", "writing_style", "research_note", "workflow_preference",
}

LEGACY_CATEGORY_MAP = {
    "preference": "user_preference",
    "fact": "project_fact",
    "search_result": "research_note",
    "note": "project_fact",
}

# Reverse map: for recall/list queries, include legacy values alongside new ones
_COMPAT_CATEGORIES = {
    "user_preference": ["user_preference", "preference"],
    "project_fact": ["project_fact", "fact", "note"],
    "writing_style": ["writing_style"],
    "research_note": ["research_note", "search_result"],
    "workflow_preference": ["workflow_preference"],
}


def normalize_category(cat: str) -> str:
    """Map legacy category names to the new canonical set."""
    cat = (cat or "").strip()
    if cat in VALID_CATEGORIES:
        return cat
    return LEGACY_CATEGORY_MAP.get(cat, "user_preference")


def _compatible_categories(category: str) -> list[str]:
    """Return all DB values that should match a given canonical category."""
    return _COMPAT_CATEGORIES.get(category, [category])


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

    category = normalize_category(args.get("category"))
    importance = max(0, min(10, int(args.get("importance") or 5)))
    source = str(args.get("source") or "assistant")[:50]

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
        existing.source = source
        refresh_source_index(db, project_id, "assistant_memory", existing.id)
        commit_session(db)
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
        source=source,
        importance=importance,
    )
    db.add(memory)
    db.flush()
    refresh_source_index(db, project_id, "assistant_memory", memory.id)
    commit_session(db)
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
    if category:
        compat = _compatible_categories(normalize_category(category))
        base = base.filter(AssistantMemory.category.in_(compat))

    memories = (
        base.order_by(AssistantMemory.importance.desc(), AssistantMemory.updated_at.desc())
        .limit(limit)
        .all()
    )
    data = [
        {
            "id": m.id,
            "category": normalize_category(m.category),
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
        delete_source_index(db, project_id, "assistant_memory", memory.id)
        key_deleted = memory.key
        db.delete(memory)
        commit_session(db)
        return {"tool": "forget", "status": "ok", "detail": f"已删除记忆「{key_deleted}」", "data": {"id": memory_id, "key": key_deleted}}

    if key:
        targets = (
            db.query(AssistantMemory)
            .filter(
                AssistantMemory.project_id == project_id,
                AssistantMemory.key == key,
            )
            .all()
        )
        for m in targets:
            delete_source_index(db, project_id, "assistant_memory", m.id)
            db.delete(m)
        commit_session(db)
        if targets:
            return {"tool": "forget", "status": "ok", "detail": f"已删除 {len(targets)} 条匹配「{key}」的记忆", "data": {"key": key, "deleted_count": len(targets)}}
        return {"tool": "forget", "status": "ok", "detail": f"没有匹配「{key}」的记忆", "data": {"key": key, "deleted_count": 0}}

    return {"tool": "forget", "status": "error", "detail": "需要提供 id 或 key"}


async def list_memories(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """List memories with optional category filter."""
    category = str(args.get("category") or "").strip()
    limit = max(1, min(int(args.get("limit") or 30), 100))

    base = db.query(AssistantMemory).filter(
        AssistantMemory.project_id == project_id,
    )
    if category:
        compat = _compatible_categories(normalize_category(category))
        base = base.filter(AssistantMemory.category.in_(compat))

    memories = (
        base.order_by(AssistantMemory.importance.desc(), AssistantMemory.updated_at.desc())
        .limit(limit)
        .all()
    )
    data = [
        {
            "id": m.id,
            "category": normalize_category(m.category),
            "key": m.key,
            "value": m.value,
            "source": m.source,
            "importance": m.importance,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        }
        for m in memories
    ]
    return {
        "tool": "list_memories",
        "status": "ok",
        "detail": f"共 {len(data)} 条记忆" if data else "暂无记忆",
        "data": data,
    }
