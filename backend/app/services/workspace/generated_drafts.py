"""Short-lived generated draft cache for workspace tools.

The assistant model should not have to copy a full chapter body back into a
tool-call argument. Tool-call arguments are a common place for long text to get
truncated, so writers store the full text here and write tools can resolve it by
draft id or by matching a provided prefix.

Drafts are persisted to SQLite (chapter_drafts table) so they survive server
restarts. The in-memory OrderedDict acts as an L1 cache for fast lookups.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any
from uuid import uuid4

MAX_CHAPTER_DRAFTS = 64

_CHAPTER_DRAFTS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def store_chapter_draft(
    *,
    project_id: str,
    content: str,
    title: str = "",
    outline_node_id: str | None = None,
    db: Any = None,
) -> str:
    draft_id = str(uuid4())
    _CHAPTER_DRAFTS[draft_id] = {
        "project_id": project_id,
        "title": title,
        "outline_node_id": outline_node_id or "",
        "content": content,
        "created_at": datetime.utcnow(),
    }
    _CHAPTER_DRAFTS.move_to_end(draft_id)
    while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
        _CHAPTER_DRAFTS.popitem(last=False)

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            row = ChapterDraft(
                id=draft_id,
                project_id=project_id,
                title=title or "",
                outline_node_id=outline_node_id or None,
                content=content,
            )
            db.add(row)
            db.commit()
        except Exception:
            db.rollback()

    return draft_id


def get_chapter_draft(project_id: str, draft_id: str | None, *, db: Any = None) -> str | None:
    if not draft_id:
        return None
    entry = _CHAPTER_DRAFTS.get(str(draft_id))
    if entry and entry.get("project_id") == project_id:
        _CHAPTER_DRAFTS.move_to_end(str(draft_id))
        return str(entry.get("content") or "")

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            row = (
                db.query(ChapterDraft)
                .filter(ChapterDraft.id == str(draft_id), ChapterDraft.project_id == project_id)
                .first()
            )
            if row:
                content = str(row.content or "")
                _CHAPTER_DRAFTS[str(draft_id)] = {
                    "project_id": project_id,
                    "title": row.title or "",
                    "outline_node_id": row.outline_node_id or "",
                    "content": content,
                    "created_at": row.created_at,
                }
                _CHAPTER_DRAFTS.move_to_end(str(draft_id))
                while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
                    _CHAPTER_DRAFTS.popitem(last=False)
                return content
        except Exception:
            pass

    return None


def get_chapter_draft_meta(project_id: str, draft_id: str | None, *, db: Any = None) -> dict[str, Any] | None:
    if not draft_id:
        return None
    entry = _CHAPTER_DRAFTS.get(str(draft_id))
    if entry and entry.get("project_id") == project_id:
        return {
            "title": str(entry.get("title") or ""),
            "outline_node_id": str(entry.get("outline_node_id") or ""),
            "content": str(entry.get("content") or ""),
        }

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            row = (
                db.query(ChapterDraft)
                .filter(ChapterDraft.id == str(draft_id), ChapterDraft.project_id == project_id)
                .first()
            )
            if row:
                return {
                    "title": row.title or "",
                    "outline_node_id": row.outline_node_id or "",
                    "content": row.content or "",
                }
        except Exception:
            pass
    return None


def _looks_like_prefix(prefix: str, full: str) -> bool:
    prefix = prefix.strip()
    full = full.strip()
    if not prefix:
        return True
    if len(full) <= len(prefix):
        return False
    head = full[: max(200, min(len(prefix), 1200))]
    return head.startswith(prefix[: len(head)]) or prefix[:200] in full[:1200]


def resolve_chapter_draft_content(
    *,
    project_id: str,
    provided_content: str = "",
    draft_id: str | None = None,
    outline_node_id: str | None = None,
    db: Any = None,
) -> str:
    """Return the best full chapter content for a write/evaluation action."""
    provided = provided_content or ""
    direct = get_chapter_draft(project_id, draft_id, db=db)
    if direct and len(direct.strip()) > len(provided.strip()):
        return direct

    outline_id = str(outline_node_id or "").strip()
    for _id, entry in reversed(_CHAPTER_DRAFTS.items()):
        if entry.get("project_id") != project_id:
            continue
        if outline_id and str(entry.get("outline_node_id") or "") != outline_id:
            continue
        content = str(entry.get("content") or "")
        if content and _looks_like_prefix(provided, content):
            return content

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            query = db.query(ChapterDraft).filter(ChapterDraft.project_id == project_id)
            if outline_id:
                query = query.filter(ChapterDraft.outline_node_id == outline_id)
            rows = query.order_by(ChapterDraft.created_at.desc()).limit(10).all()
            for row in rows:
                content = str(row.content or "")
                if content and _looks_like_prefix(provided, content):
                    _CHAPTER_DRAFTS[str(row.id)] = {
                        "project_id": project_id,
                        "title": row.title or "",
                        "outline_node_id": row.outline_node_id or "",
                        "content": content,
                        "created_at": row.created_at,
                    }
                    _CHAPTER_DRAFTS.move_to_end(str(row.id))
                    while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
                        _CHAPTER_DRAFTS.popitem(last=False)
                    return content
        except Exception:
            pass

    return provided
