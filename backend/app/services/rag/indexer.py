"""RAG indexer: chunking, FTS5 indexing, and dirty detection."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...database.models import (
    AssistantMemory,
    Chapter,
    ChapterSummary,
    Character,
    CharacterTimeline,
    OutlineNode,
    RagChunk,
    RagDocument,
    WorldbuildingEntry,
)


# ---------------------------------------------------------------------------
# FTS5 detection
# ---------------------------------------------------------------------------

_fts5_available: bool | None = None


def detect_fts5_available(db: Session) -> bool:
    """Check if the SQLite connection supports FTS5. Caches result per process."""
    global _fts5_available
    if _fts5_available is not None:
        return _fts5_available
    try:
        db.execute(text("CREATE VIRTUAL TABLE temp.__fts5_test USING fts5(content)"))
        db.execute(text("DROP TABLE temp.__fts5_test"))
        db.commit()
        _fts5_available = True
    except Exception:
        db.rollback()
        _fts5_available = False
    return _fts5_available


# ---------------------------------------------------------------------------
# Content hashing and chunking
# ---------------------------------------------------------------------------

def _content_hash(text_value: str) -> str:
    """SHA-256 hex digest of text content."""
    return hashlib.sha256((text_value or "").encode("utf-8")).hexdigest()


def _chunk_text(text_value: str, max_chunk_chars: int = 800, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks by paragraph boundaries.

    Paragraphs longer than max_chunk_chars are split at sentence boundaries.
    """
    if not text_value or not text_value.strip():
        return []

    paragraphs = [p.strip() for p in text_value.split("\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 1 <= max_chunk_chars:
            current = f"{current}\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) > max_chunk_chars:
                sub_chunks = _split_long_paragraph(para, max_chunk_chars, overlap)
                chunks.extend(sub_chunks[:-1])
                current = sub_chunks[-1] if sub_chunks else ""
            else:
                current = para

    if current:
        chunks.append(current)

    return chunks


def _split_long_paragraph(para: str, max_chars: int, overlap: int) -> list[str]:
    """Split a long paragraph at sentence-like boundaries."""
    import re
    sentences = re.split(r"(?<=[。！？；\.\!\?\;])\s*", para)
    chunks: list[str] = []
    current = ""
    for sent in sentences:
        if not sent.strip():
            continue
        if len(current) + len(sent) + 1 <= max_chars:
            current = f"{current}{sent}" if current else sent
        else:
            if current:
                chunks.append(current)
            if len(sent) > max_chars:
                for i in range(0, len(sent), max_chars - overlap):
                    chunks.append(sent[i:i + max_chars])
                current = ""
            else:
                current = sent
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# FTS5 sync helpers
# ---------------------------------------------------------------------------

def _fts_insert(
    db: Session,
    chunk_id: str,
    project_id: str,
    source_type: str,
    title: str,
    content: str,
    metadata_json: str | None,
) -> None:
    if not detect_fts5_available(db):
        return
    try:
        db.execute(
            text(
                "INSERT INTO rag_chunks_fts(chunk_id, project_id, source_type, title, content, metadata_json) "
                "VALUES(:cid, :pid, :st, :title, :content, :meta)"
            ),
            {"cid": chunk_id, "pid": project_id, "st": source_type, "title": title, "content": content, "meta": metadata_json or ""},
        )
    except Exception as exc:
        # A database can support FTS5 while its virtual table has not yet been
        # created (for example a partial upgrade or an isolated test DB). Keep
        # lexical LIKE indexing alive instead of failing the whole rebuild.
        if "rag_chunks_fts" not in str(exc):
            raise


def _fts_delete(db: Session, chunk_id: str) -> None:
    if not detect_fts5_available(db):
        return
    try:
        db.execute(text("DELETE FROM rag_chunks_fts WHERE chunk_id = :cid"), {"cid": chunk_id})
    except Exception as exc:
        if "rag_chunks_fts" not in str(exc):
            raise


def _fts_delete_by_source(db: Session, project_id: str, source_type: str, source_id: str) -> None:
    if not detect_fts5_available(db):
        return
    chunk_ids = [
        row[0]
        for row in db.execute(
            text("SELECT id FROM rag_chunks WHERE project_id = :pid AND source_type = :st AND source_id = :sid"),
            {"pid": project_id, "st": source_type, "sid": source_id},
        ).fetchall()
    ]
    for cid in chunk_ids:
        _fts_delete(db, cid)


# ---------------------------------------------------------------------------
# Low-level chunk CRUD
# ---------------------------------------------------------------------------

def _delete_chunks_for_source(db: Session, project_id: str, source_type: str, source_id: str) -> int:
    """Delete all chunks (ORM + FTS) for a source. Returns count deleted."""
    _fts_delete_by_source(db, project_id, source_type, source_id)
    count = (
        db.query(RagChunk)
        .filter(
            RagChunk.project_id == project_id,
            RagChunk.source_type == source_type,
            RagChunk.source_id == source_id,
        )
        .delete()
    )
    return count


def _insert_chunks(
    db: Session,
    project_id: str,
    document_id: str,
    source_type: str,
    source_id: str,
    title: str,
    chunks: list[str],
    metadata_list: list[dict[str, Any]] | None = None,
) -> int:
    """Insert chunks into rag_chunks + rag_chunks_fts. Returns count."""
    for i, chunk_content in enumerate(chunks):
        meta = (metadata_list[i] if metadata_list and i < len(metadata_list) else {}) or {}
        chunk_id = _generate_uuid()
        meta_json = json.dumps(meta, ensure_ascii=False) if meta else None

        rag_chunk = RagChunk(
            id=chunk_id,
            document_id=document_id,
            project_id=project_id,
            source_type=source_type,
            source_id=source_id,
            chunk_index=i,
            title=title[:300],
            content=chunk_content,
            metadata_json=meta_json,
        )
        db.add(rag_chunk)
        _fts_insert(db, chunk_id, project_id, source_type, title[:300], chunk_content, meta_json)

    return len(chunks)


def _generate_uuid() -> str:
    import uuid
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Document tracking
# ---------------------------------------------------------------------------

def _get_or_create_document(
    db: Session,
    project_id: str,
    source_type: str,
    source_id: str,
    content_hash: str,
) -> RagDocument:
    doc = (
        db.query(RagDocument)
        .filter(
            RagDocument.project_id == project_id,
            RagDocument.source_type == source_type,
            RagDocument.source_id == source_id,
        )
        .first()
    )
    if doc:
        doc.content_hash = content_hash
        doc.indexed_at = datetime.utcnow()
        return doc
    doc = RagDocument(
        id=_generate_uuid(),
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        content_hash=content_hash,
        chunk_count=0,
        indexed_at=datetime.utcnow(),
    )
    db.add(doc)
    return doc


# ---------------------------------------------------------------------------
# Type-specific indexers
# ---------------------------------------------------------------------------

def _index_chapter(db: Session, project_id: str, chapter_id: str) -> int:
    chapter = db.query(Chapter).filter(Chapter.id == chapter_id, Chapter.project_id == project_id).first()
    if not chapter:
        return 0
    content = chapter.content or ""
    if not content.strip():
        return 0
    chunks = _chunk_text(content, max_chunk_chars=800, overlap=100)
    if not chunks:
        return 0

    c_hash = _content_hash(content)
    doc = _get_or_create_document(db, project_id, "chapter", chapter_id, c_hash)
    _delete_chunks_for_source(db, project_id, "chapter", chapter_id)

    chapter_num = _extract_chapter_number(chapter.title)
    metadata_list = [{"chapter_number": chapter_num, "title": chapter.title} for _ in chunks]
    count = _insert_chunks(db, project_id, doc.id, "chapter", chapter_id, chapter.title, chunks, metadata_list)
    doc.chunk_count = count
    return count


def _index_chapter_summary(db: Session, project_id: str, chapter_id: str) -> int:
    summary = db.query(ChapterSummary).filter(ChapterSummary.chapter_id == chapter_id).first()
    if not summary:
        return 0
    content = summary.summary_text or ""
    if not content.strip():
        return 0

    c_hash = _content_hash(content)
    doc = _get_or_create_document(db, project_id, "chapter_summary", chapter_id, c_hash)
    _delete_chunks_for_source(db, project_id, "chapter_summary", chapter_id)

    title = summary.chapter.title if summary.chapter else "未知章节"
    key_events = summary.key_events or ""
    full_text = f"{content}\n关键事件：{key_events}" if key_events else content
    count = _insert_chunks(db, project_id, doc.id, "chapter_summary", chapter_id, title, [full_text])
    doc.chunk_count = count
    return count


def _index_outline(db: Session, project_id: str, outline_id: str) -> int:
    node = db.query(OutlineNode).filter(OutlineNode.id == outline_id, OutlineNode.project_id == project_id).first()
    if not node:
        return 0
    parts = [node.title or "", node.summary or "", node.actual_summary or "", node.planned_summary or ""]
    content = "\n".join(p for p in parts if p).strip()
    if not content:
        return 0

    c_hash = _content_hash(content)
    doc = _get_or_create_document(db, project_id, "outline", outline_id, c_hash)
    _delete_chunks_for_source(db, project_id, "outline", outline_id)

    meta = {"node_type": node.node_type, "status": node.status}
    count = _insert_chunks(
        db, project_id, doc.id, "outline", outline_id, node.title or "大纲节点", [content], [meta],
    )
    doc.chunk_count = count
    return count


def _index_character(db: Session, project_id: str, character_id: str) -> int:
    char = db.query(Character).filter(Character.id == character_id, Character.project_id == project_id).first()
    if not char:
        return 0

    chunks: list[str] = []
    metadata_list: list[dict[str, Any]] = []

    identity_parts = [f"角色名称：{char.name}"]
    if char.role_type:
        identity_parts.append(f"角色类型：{char.role_type}")
    if char.appearance:
        identity_parts.append(f"外貌：{char.appearance}")
    if identity_parts:
        chunks.append("\n".join(identity_parts))
        metadata_list.append({"section": "identity", "role_type": char.role_type or ""})

    bg_parts = []
    if char.personality:
        bg_parts.append(f"性格：{char.personality}")
    if char.background:
        bg_parts.append(f"背景：{char.background}")
    if bg_parts:
        chunks.append("\n".join(bg_parts))
        metadata_list.append({"section": "personality_background"})

    state_parts = []
    if char.life_status:
        state_parts.append(f"生命状态：{char.life_status}")
    if char.current_location:
        state_parts.append(f"当前位置：{char.current_location}")
    if char.current_goal:
        state_parts.append(f"当前目标：{char.current_goal}")
    if char.abilities:
        state_parts.append(f"能力：{char.abilities}")
    if state_parts:
        chunks.append("\n".join(state_parts))
        metadata_list.append({"section": "state_abilities"})

    if not chunks:
        return 0

    all_text = "\n---\n".join(chunks)
    c_hash = _content_hash(all_text)
    doc = _get_or_create_document(db, project_id, "character", character_id, c_hash)
    _delete_chunks_for_source(db, project_id, "character", character_id)

    count = _insert_chunks(db, project_id, doc.id, "character", character_id, char.name, chunks, metadata_list)
    doc.chunk_count = count
    return count


def _index_character_timeline(db: Session, project_id: str, character_id: str) -> int:
    events = (
        db.query(CharacterTimeline)
        .filter(CharacterTimeline.character_id == character_id)
        .order_by(CharacterTimeline.created_at.desc())
        .limit(50)
        .all()
    )
    if not events:
        return 0

    char = db.query(Character).filter(Character.id == character_id).first()
    char_name = char.name if char else character_id[:8]

    event_lines = []
    for event in reversed(events):
        emo = f"（情感变化：{event.emotional_state_change}）" if event.emotional_state_change else ""
        event_lines.append(f"[{event.event_type}] {event.event_description}{emo}")

    batch_size = 5
    chunks: list[str] = []
    metadata_list: list[dict[str, Any]] = []
    for i in range(0, len(event_lines), batch_size):
        batch = event_lines[i:i + batch_size]
        chunks.append("\n".join(batch))
        metadata_list.append({"event_range": f"{i}-{i + len(batch)}"})

    all_text = "\n".join(chunks)
    c_hash = _content_hash(all_text)
    doc = _get_or_create_document(db, project_id, "character_timeline", character_id, c_hash)
    _delete_chunks_for_source(db, project_id, "character_timeline", character_id)

    title = f"{char_name}经历时间线"
    count = _insert_chunks(db, project_id, doc.id, "character_timeline", character_id, title, chunks, metadata_list)
    doc.chunk_count = count
    return count


def _index_worldbuilding(db: Session, project_id: str, entry_id: str) -> int:
    entry = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.id == entry_id, WorldbuildingEntry.project_id == project_id,
    ).first()
    if not entry:
        return 0
    content = entry.content or ""
    if not content.strip():
        return 0

    c_hash = _content_hash(content)
    doc = _get_or_create_document(db, project_id, "worldbuilding", entry_id, c_hash)
    _delete_chunks_for_source(db, project_id, "worldbuilding", entry_id)

    full_text = f"{entry.title}\n{content}"
    meta = {"dimension": entry.dimension}
    count = _insert_chunks(
        db, project_id, doc.id, "worldbuilding", entry_id, entry.title, [full_text], [meta],
    )
    doc.chunk_count = count
    return count


def _index_assistant_memory(db: Session, project_id: str, memory_id: str) -> int:
    memory = db.query(AssistantMemory).filter(
        AssistantMemory.id == memory_id,
        AssistantMemory.project_id == project_id,
    ).first()
    if not memory:
        return 0
    content = memory.value or ""
    if not content.strip():
        return 0

    c_hash = _content_hash(content)
    doc = _get_or_create_document(db, project_id, "assistant_memory", memory_id, c_hash)
    _delete_chunks_for_source(db, project_id, "assistant_memory", memory_id)

    full_text = f"[{memory.category}] {memory.key}\n{content}"
    meta = {"category": memory.category, "importance": memory.importance}
    count = _insert_chunks(
        db, project_id, doc.id, "assistant_memory", memory_id, memory.key, [full_text], [meta],
    )
    doc.chunk_count = count
    return count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_INDEXERS = {
    "chapter": _index_chapter,
    "chapter_summary": _index_chapter_summary,
    "outline": _index_outline,
    "character": _index_character,
    "character_timeline": _index_character_timeline,
    "worldbuilding": _index_worldbuilding,
    "assistant_memory": _index_assistant_memory,
}


def index_document(db: Session, project_id: str, source_type: str, source_id: str) -> dict:
    """Index a single source object. Returns {chunks_created, skipped}."""
    indexer = _INDEXERS.get(source_type)
    if not indexer:
        return {"chunks_created": 0, "skipped": True, "detail": f"未知来源类型: {source_type}"}
    try:
        count = indexer(db, project_id, source_id)
        return {"chunks_created": count, "skipped": count == 0}
    except Exception as e:
        return {"chunks_created": 0, "skipped": True, "detail": f"索引失败: {e}"}


def reindex_project(db: Session, project_id: str) -> dict:
    """Full reindex of all source objects in a project."""
    return reindex_project_types(db, project_id, source_types=None)


def _index_prompt_pack(db: Session, project_id: str, pack_id: str) -> int:
    """Index a public prompt pack as RAG chunks."""
    from app.database.models import PublicPromptPack

    pack = db.query(PublicPromptPack).filter(
        PublicPromptPack.pack_id == pack_id,
        (PublicPromptPack.project_id == project_id) | (PublicPromptPack.project_id == None),
        PublicPromptPack.enabled == True,
    ).first()

    if not pack:
        return 0

    _delete_chunks_for_source(db, project_id, "prompt_pack", pack.pack_id)

    content_parts = [pack.system_prompt]
    if pack.summary:
        content_parts.insert(0, pack.summary)
    content = "\n\n".join(content_parts)

    doc = _get_or_create_document(db, project_id, "prompt_pack", pack.pack_id, _content_hash(content))
    count = _insert_chunks(
        db,
        project_id,
        doc.id,
        "prompt_pack",
        pack.pack_id,
        pack.title,
        [content[:4000]],
        [{"scope": pack.scope, "version": pack.version, "is_builtin": pack.is_builtin}],
    )
    doc.chunk_count = count
    return count


def _index_method_card(db: Session, project_id: str, card_id: str) -> int:
    """Index a method card as RAG chunks."""
    from app.database.models import MethodCard
    import json

    card = db.query(MethodCard).filter(
        MethodCard.card_id == card_id,
        (MethodCard.project_id == project_id) | (MethodCard.project_id == None),
        MethodCard.enabled == True,
    ).first()

    if not card:
        return 0

    _delete_chunks_for_source(db, project_id, "method_card", card.card_id)

    content = json.dumps(card.content_json, ensure_ascii=False) if card.content_json else ""

    doc = _get_or_create_document(db, project_id, "method_card", card.card_id, _content_hash(content))
    count = _insert_chunks(
        db,
        project_id,
        doc.id,
        "method_card",
        card.card_id,
        card.title,
        [content[:4000]],
        [{"card_type": card.card_type, "version": card.version, "is_builtin": card.is_builtin}],
    )
    doc.chunk_count = count
    return count


def reindex_project_types(
    db: Session,
    project_id: str,
    source_types: list[str] | None = None,
) -> dict:
    """Reindex specific source types (or all if None) for a project."""
    stats: dict[str, int] = {}
    total = 0

    def should_index(st: str) -> bool:
        return source_types is None or st in source_types

    if should_index("chapter"):
        for chapter in db.query(Chapter).filter(Chapter.project_id == project_id).all():
            count = _index_chapter(db, project_id, chapter.id)
            stats["chapter"] = stats.get("chapter", 0) + count
            total += count

    if should_index("chapter_summary"):
        for summary in db.query(ChapterSummary).join(Chapter).filter(Chapter.project_id == project_id).all():
            count = _index_chapter_summary(db, project_id, summary.chapter_id)
            stats["chapter_summary"] = stats.get("chapter_summary", 0) + count
            total += count

    if should_index("outline"):
        for node in db.query(OutlineNode).filter(OutlineNode.project_id == project_id).all():
            count = _index_outline(db, project_id, node.id)
            stats["outline"] = stats.get("outline", 0) + count
            total += count

    if should_index("character") or should_index("character_timeline"):
        for char in db.query(Character).filter(Character.project_id == project_id).all():
            if should_index("character"):
                count = _index_character(db, project_id, char.id)
                stats["character"] = stats.get("character", 0) + count
                total += count
            if should_index("character_timeline"):
                tl_count = _index_character_timeline(db, project_id, char.id)
                stats["character_timeline"] = stats.get("character_timeline", 0) + tl_count
                total += tl_count

    if should_index("worldbuilding"):
        for entry in db.query(WorldbuildingEntry).filter(WorldbuildingEntry.project_id == project_id).all():
            count = _index_worldbuilding(db, project_id, entry.id)
            stats["worldbuilding"] = stats.get("worldbuilding", 0) + count
            total += count

    if should_index("assistant_memory"):
        for mem in db.query(AssistantMemory).filter(AssistantMemory.project_id == project_id).all():
            count = _index_assistant_memory(db, project_id, mem.id)
            stats["assistant_memory"] = stats.get("assistant_memory", 0) + count
            total += count

    if source_types is not None and should_index("prompt_pack"):
        from app.database.models import PublicPromptPack
        from app.services.prompt_packs.seed import ensure_builtin_packs
        ensure_builtin_packs(db)
        packs = db.query(PublicPromptPack).filter(
            (PublicPromptPack.project_id == project_id) | (PublicPromptPack.project_id == None),
            PublicPromptPack.enabled == True,
        ).all()
        for pack in packs:
            count = _index_prompt_pack(db, project_id, pack.pack_id)
            stats["prompt_pack"] = stats.get("prompt_pack", 0) + count
            total += count

    if source_types is not None and should_index("method_card"):
        from app.database.models import MethodCard
        cards = db.query(MethodCard).filter(
            (MethodCard.project_id == project_id) | (MethodCard.project_id == None),
            MethodCard.enabled == True,
        ).all()
        for card in cards:
            count = _index_method_card(db, project_id, card.card_id)
            stats["method_card"] = stats.get("method_card", 0) + count
            total += count

    return {"total_chunks": total, "by_type": stats}


def project_has_chunks(db: Session, project_id: str, source_types: list[str] | None = None) -> bool:
    """Check if a project has any indexed chunks (optionally filtered by source_types)."""
    query = db.query(RagChunk.id).filter(RagChunk.project_id == project_id)
    if source_types:
        query = query.filter(RagChunk.source_type.in_(source_types))
    return query.first() is not None


def mark_dirty(db: Session, project_id: str, source_type: str, source_id: str) -> None:
    """Delete existing chunks so next ensure_indexed triggers reindex."""
    _delete_chunks_for_source(db, project_id, source_type, source_id)
    db.query(RagDocument).filter(
        RagDocument.project_id == project_id,
        RagDocument.source_type == source_type,
        RagDocument.source_id == source_id,
    ).delete()


def refresh_source_index(db: Session, project_id: str, source_type: str, source_id: str) -> None:
    """Delete existing chunks and immediately rebuild the index for a single source."""
    mark_dirty(db, project_id, source_type, source_id)
    index_document(db, project_id, source_type, source_id)


def delete_source_index(db: Session, project_id: str, source_type: str, source_id: str) -> None:
    """Remove all RAG chunks and document record for a single source."""
    _delete_chunks_for_source(db, project_id, source_type, source_id)
    db.query(RagDocument).filter(
        RagDocument.project_id == project_id,
        RagDocument.source_type == source_type,
        RagDocument.source_id == source_id,
    ).delete()


def ensure_indexed(db: Session, project_id: str, source_type: str, source_id: str) -> bool:
    """Lazy index: only reindex if content_hash is stale or missing. Returns True if indexed."""
    indexer = _INDEXERS.get(source_type)
    if not indexer:
        return False

    doc = (
        db.query(RagDocument)
        .filter(
            RagDocument.project_id == project_id,
            RagDocument.source_type == source_type,
            RagDocument.source_id == source_id,
        )
        .first()
    )

    current_hash = _get_source_content_hash(db, source_type, source_id)
    if doc and doc.content_hash == current_hash and doc.chunk_count > 0:
        return True

    indexer(db, project_id, source_id)
    return True


def _get_source_content_hash(db: Session, source_type: str, source_id: str) -> str:
    """Get current content hash for a source object."""
    if source_type == "chapter":
        obj = db.query(Chapter).filter(Chapter.id == source_id).first()
        return _content_hash(obj.content or "") if obj else ""
    elif source_type == "chapter_summary":
        obj = db.query(ChapterSummary).filter(ChapterSummary.chapter_id == source_id).first()
        return _content_hash(obj.summary_text or "") if obj else ""
    elif source_type == "outline":
        obj = db.query(OutlineNode).filter(OutlineNode.id == source_id).first()
        if not obj:
            return ""
        parts = [obj.title or "", obj.summary or "", obj.actual_summary or "", obj.planned_summary or ""]
        return _content_hash("\n".join(p for p in parts if p))
    elif source_type == "character":
        obj = db.query(Character).filter(Character.id == source_id).first()
        if not obj:
            return ""
        parts = [obj.name or "", obj.personality or "", obj.background or "", obj.appearance or "", obj.abilities or ""]
        return _content_hash("\n".join(p for p in parts if p))
    elif source_type == "character_timeline":
        events = (
            db.query(CharacterTimeline)
            .filter(CharacterTimeline.character_id == source_id)
            .order_by(CharacterTimeline.created_at.desc())
            .limit(50)
            .all()
        )
        return _content_hash("\n".join(e.event_description or "" for e in events))
    elif source_type == "worldbuilding":
        obj = db.query(WorldbuildingEntry).filter(WorldbuildingEntry.id == source_id).first()
        return _content_hash(obj.content or "") if obj else ""
    elif source_type == "assistant_memory":
        obj = db.query(AssistantMemory).filter(AssistantMemory.id == source_id).first()
        return _content_hash(obj.value or "") if obj else ""
    return ""


def _extract_chapter_number(title: str) -> int | None:
    """Extract chapter number from title."""
    import re
    from ...core.numbers import chinese_number_to_int
    match = re.search(r"第\s*([0-9一二两三四五六七八九十百千万零〇]+)\s*章", title or "")
    if not match:
        match = re.search(r"([0-9]+)", title or "")
    return chinese_number_to_int(match.group(1)) if match else None
