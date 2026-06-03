"""RAG retriever: hybrid FTS5/BM25 + LIKE search with Chinese fallback."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...database.models import RagChunk
from .indexer import detect_fts5_available, ensure_indexed


@dataclass
class SearchResult:
    chunk_id: str
    source_type: str
    source_id: str
    title: str
    content: str
    metadata: dict[str, Any]
    score: float
    reason: str


# ---------------------------------------------------------------------------
# Term extraction (reuses logic from context_builders)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "一个", "一些", "以及", "已经", "正在", "如果", "没有", "需要", "用户", "要求",
    "生成", "创建", "修改", "更新", "章节", "本章", "前文", "后续", "下一章",
    "大纲", "节点", "剧情", "摘要", "角色", "世界观", "设定", "背景", "内容",
    "当前", "之前", "之后", "这个", "那个", "这里", "那里", "他们", "她们",
    "进行", "出现", "发现", "继续", "相关", "信息", "写作", "正文",
}


def _extract_terms(query: str) -> tuple[list[str], list[str]]:
    """Split query into (fts_terms, like_terms).

    fts_terms: ASCII alphanumeric tokens suitable for FTS5 MATCH.
    like_terms: CJK tokens >= 2 chars for LIKE fallback.
    """
    if not query:
        return [], []

    fts_terms: list[str] = []
    like_terms: list[str] = []

    for term in re.findall(r"[一-鿿]{2,12}|[A-Za-z][A-Za-z0-9_-]{2,30}", query):
        value = term.strip()
        if not value or value in _STOPWORDS:
            continue
        if value.isdigit():
            continue
        if value.isascii():
            fts_terms.append(value)
        else:
            like_terms.append(value)

    return fts_terms, like_terms


def _build_fts_query(terms: list[str]) -> str:
    """Build FTS5 MATCH query from terms. Returns empty string if no terms."""
    if not terms:
        return ""
    # Each term is quoted and OR'd
    parts = [f'"{t}"' for t in terms[:20]]
    return " OR ".join(parts)


# ---------------------------------------------------------------------------
# Search implementations
# ---------------------------------------------------------------------------

def _search_fts(
    db: Session,
    project_id: str,
    fts_query: str,
    source_types: list[str] | None,
    limit: int,
) -> list[dict]:
    """Search via FTS5 with BM25 ranking."""
    if not fts_query:
        return []

    type_filter = ""
    params: dict[str, Any] = {"pid": project_id, "query": fts_query, "limit": limit}
    if source_types:
        placeholders = ", ".join(f":st{i}" for i in range(len(source_types)))
        type_filter = f"AND source_type IN ({placeholders})"
        for i, st in enumerate(source_types):
            params[f"st{i}"] = st

    sql = f"""
        SELECT chunk_id, project_id, source_type, title, content, metadata_json,
               rank AS score
        FROM rag_chunks_fts
        WHERE rag_chunks_fts MATCH :query
          AND project_id = :pid
          {type_filter}
        ORDER BY rank
        LIMIT :limit
    """
    try:
        rows = db.execute(text(sql), params).fetchall()
    except Exception:
        return []

    results = []
    for row in rows:
        meta = {}
        if row[5]:
            try:
                meta = json.loads(row[5])
            except Exception:
                pass
        results.append({
            "chunk_id": row[0],
            "source_type": row[2],
            "title": row[3] or "",
            "content": row[4] or "",
            "metadata": meta,
            "score": abs(float(row[6])),  # BM25 rank is negative
            "reason": "BM25命中",
        })
    return results


def _search_like(
    db: Session,
    project_id: str,
    like_terms: list[str],
    source_types: list[str] | None,
    limit: int,
) -> list[dict]:
    """Search via LIKE conditions with weighted scoring."""
    if not like_terms:
        return []

    query = db.query(RagChunk).filter(RagChunk.project_id == project_id)
    if source_types:
        query = query.filter(RagChunk.source_type.in_(source_types))

    chunks = query.all()
    if not chunks:
        return []

    scored: list[dict] = []
    for chunk in chunks:
        title_lower = (chunk.title or "").lower()
        content_lower = (chunk.content or "").lower()
        meta_lower = (chunk.metadata_json or "").lower()

        score = 0.0
        matched_terms: list[str] = []
        for term in like_terms:
            term_lower = term.lower()
            if term in title_lower or term_lower in title_lower:
                score += 8.0
                matched_terms.append(term)
            elif term in content_lower or term_lower in content_lower:
                score += 2.0
                matched_terms.append(term)
            elif term in meta_lower or term_lower in meta_lower:
                score += 4.0
                matched_terms.append(term)

        if score > 0:
            meta = {}
            if chunk.metadata_json:
                try:
                    meta = json.loads(chunk.metadata_json)
                except Exception:
                    pass
            scored.append({
                "chunk_id": chunk.id,
                "source_type": chunk.source_type,
                "title": chunk.title or "",
                "content": chunk.content or "",
                "metadata": meta,
                "score": score,
                "reason": f"LIKE命中({', '.join(matched_terms[:3])})",
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def _search_like_only(
    db: Session,
    project_id: str,
    all_terms: list[str],
    source_types: list[str] | None,
    limit: int,
) -> list[dict]:
    """Pure LIKE fallback when FTS5 is unavailable."""
    return _search_like(db, project_id, all_terms, source_types, limit)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_chunks(
    db: Session,
    project_id: str,
    query: str,
    source_types: list[str] | None = None,
    limit: int = 20,
    use_fts: bool = True,
) -> list[SearchResult]:
    """Hybrid search: FTS5 BM25 for English/numeric terms + LIKE for CJK terms.

    Falls back to LIKE-only when FTS5 is unavailable.
    """
    if not query or not query.strip():
        return []

    fts_terms, like_terms = _extract_terms(query)
    all_terms = fts_terms + like_terms
    if not all_terms:
        return []

    results_by_id: dict[str, dict] = {}

    if use_fts and detect_fts5_available(db) and fts_terms:
        fts_query = _build_fts_query(fts_terms)
        fts_results = _search_fts(db, project_id, fts_query, source_types, limit * 2)
        for r in fts_results:
            cid = r["chunk_id"]
            if cid not in results_by_id or r["score"] > results_by_id[cid]["score"]:
                results_by_id[cid] = r

    if like_terms:
        like_results = _search_like(db, project_id, like_terms, source_types, limit * 2)
        for r in like_results:
            cid = r["chunk_id"]
            if cid in results_by_id:
                results_by_id[cid]["score"] += r["score"]
                results_by_id[cid]["reason"] += " + LIKE补充"
            else:
                results_by_id[cid] = r

    if not results_by_id and not use_fts:
        results_by_id = {
            r["chunk_id"]: r
            for r in _search_like_only(db, project_id, all_terms, source_types, limit * 2)
        }

    sorted_results = sorted(results_by_id.values(), key=lambda x: x["score"], reverse=True)[:limit]

    return [
        SearchResult(
            chunk_id=r["chunk_id"],
            source_type=r["source_type"],
            source_id=_get_source_id_from_chunk(db, r["chunk_id"]),
            title=r["title"],
            content=r["content"],
            metadata=r["metadata"],
            score=r["score"],
            reason=r["reason"],
        )
        for r in sorted_results
    ]


def get_chunks_for_source(db: Session, project_id: str, source_type: str, source_id: str) -> list[dict]:
    """Get all chunks for a specific source object."""
    chunks = (
        db.query(RagChunk)
        .filter(
            RagChunk.project_id == project_id,
            RagChunk.source_type == source_type,
            RagChunk.source_id == source_id,
        )
        .order_by(RagChunk.chunk_index.asc())
        .all()
    )
    return [
        {
            "chunk_id": c.id,
            "source_type": c.source_type,
            "source_id": c.source_id,
            "title": c.title or "",
            "content": c.content or "",
            "metadata": json.loads(c.metadata_json) if c.metadata_json else {},
            "chunk_index": c.chunk_index,
        }
        for c in chunks
    ]


def get_chunk_by_id(db: Session, chunk_id: str) -> dict | None:
    """Get a single chunk by ID."""
    chunk = db.query(RagChunk).filter(RagChunk.id == chunk_id).first()
    if not chunk:
        return None
    return {
        "chunk_id": chunk.id,
        "source_type": chunk.source_type,
        "source_id": chunk.source_id,
        "title": chunk.title or "",
        "content": chunk.content or "",
        "metadata": json.loads(chunk.metadata_json) if chunk.metadata_json else {},
        "chunk_index": chunk.chunk_index,
    }


def _get_source_id_from_chunk(db: Session, chunk_id: str) -> str:
    chunk = db.query(RagChunk.source_id).filter(RagChunk.id == chunk_id).first()
    return chunk[0] if chunk else ""
