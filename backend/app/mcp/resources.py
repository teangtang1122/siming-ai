"""MCP resource URI scheme for Moshu.

Defines the moshu:// URI scheme, parsing, and resource types.
All Moshu resources use a stable, hierarchical URI format.

URI patterns:
    moshu://projects
    moshu://projects/{project_id}
    moshu://projects/{project_id}/chapters
    moshu://projects/{project_id}/chapters/{chapter_id}
    moshu://projects/{project_id}/characters
    moshu://projects/{project_id}/characters/{character_id}
    moshu://projects/{project_id}/worldbuilding
    moshu://projects/{project_id}/worldbuilding/{entry_id}
    moshu://projects/{project_id}/outline
    moshu://projects/{project_id}/outline/{node_id}
    moshu://projects/{project_id}/relationships
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ── Resource URI patterns ────────────────────────────────────────────────

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^moshu://projects$"), "projects_index"),
    (re.compile(r"^moshu://projects/([^/]+)$"), "project_detail"),
    (re.compile(r"^moshu://projects/([^/]+)/chapters$"), "chapters_index"),
    (re.compile(r"^moshu://projects/([^/]+)/chapters/([^/]+)$"), "chapter_detail"),
    (re.compile(r"^moshu://projects/([^/]+)/characters$"), "characters_index"),
    (re.compile(r"^moshu://projects/([^/]+)/characters/([^/]+)$"), "character_detail"),
    (re.compile(r"^moshu://projects/([^/]+)/worldbuilding$"), "worldbuilding_index"),
    (re.compile(r"^moshu://projects/([^/]+)/worldbuilding/([^/]+)$"), "worldbuilding_detail"),
    (re.compile(r"^moshu://projects/([^/]+)/outline$"), "outline_index"),
    (re.compile(r"^moshu://projects/([^/]+)/outline/([^/]+)$"), "outline_detail"),
    (re.compile(r"^moshu://projects/([^/]+)/relationships$"), "relationships"),
    (re.compile(r"^moshu://projects/([^/]+)/rag/search(?:\?.*)?$"), "rag_search"),
]


@dataclass(frozen=True)
class ParsedUri:
    """Result of parsing a moshu:// URI."""
    uri: str
    resource_type: str
    project_id: str = ""
    entity_id: str = ""
    query_params: dict[str, str] | None = None


def parse_uri(uri: str) -> ParsedUri | None:
    """Parse a moshu:// URI into a ParsedUri, or None if invalid.

    Args:
        uri: A string like "moshu://projects/abc123/chapters"

    Returns:
        ParsedUri with resource_type and extracted IDs, or None if no match.
    """
    # Extract query string before matching
    query_params: dict[str, str] | None = None
    if "?" in uri:
        base, qs = uri.split("?", 1)
        query_params = {}
        for part in qs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                query_params[k] = v
    else:
        base = uri

    for pattern, resource_type in _PATTERNS:
        m = pattern.match(uri)  # match against full URI (pattern includes optional ?)
        if m:
            groups = m.groups()
            project_id = groups[0] if len(groups) >= 1 else ""
            entity_id = groups[1] if len(groups) >= 2 else ""
            return ParsedUri(
                uri=uri,
                resource_type=resource_type,
                project_id=project_id,
                entity_id=entity_id,
                query_params=query_params,
            )
    return None


def build_uri(*parts: str) -> str:
    """Build a moshu:// URI from path parts.

    Examples:
        build_uri("projects") -> "moshu://projects"
        build_uri("projects", "abc") -> "moshu://projects/abc"
        build_uri("projects", "abc", "chapters") -> "moshu://projects/abc/chapters"
    """
    return "moshu://" + "/".join(parts)


# ── Resource metadata ────────────────────────────────────────────────────

@dataclass
class ResourceMeta:
    """Metadata for an MCP resource."""
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"


# Resource type descriptions for listings
_RESOURCE_DESCRIPTIONS: dict[str, str] = {
    "projects_index": "List of all projects",
    "project_detail": "Project metadata and settings",
    "chapters_index": "Chapter list for a project",
    "chapter_detail": "Chapter content and metadata",
    "characters_index": "Character list for a project",
    "character_detail": "Character card with full details",
    "worldbuilding_index": "Worldbuilding entry list",
    "worldbuilding_detail": "Worldbuilding entry content",
    "outline_index": "Outline tree structure",
    "outline_detail": "Outline node with summary",
    "relationships": "Character relationships",
    "rag_search": "RAG search results across indexed content",
}


def get_resource_description(resource_type: str) -> str:
    """Return a human-readable description for a resource type."""
    return _RESOURCE_DESCRIPTIONS.get(resource_type, "Moshu resource")


def list_resource_uris(project_id: str) -> list[str]:
    """List all resource URIs for a given project.

    Returns the index-level URIs. Entity-detail URIs require
    knowing the entity IDs and are constructed on demand.
    """
    return [
        "moshu://projects",
        build_uri("projects", project_id),
        build_uri("projects", project_id, "chapters"),
        build_uri("projects", project_id, "characters"),
        build_uri("projects", project_id, "worldbuilding"),
        build_uri("projects", project_id, "outline"),
        build_uri("projects", project_id, "relationships"),
    ]


# ── Resource content readers ─────────────────────────────────────────────

@dataclass
class ResourceContent:
    """Content returned by a resource reader."""
    uri: str
    mime_type: str
    text: str


def read_resource(db: Any, uri: str) -> ResourceContent | None:
    """Read a resource by its moshu:// URI.

    Args:
        db: SQLAlchemy session.
        uri: The moshu:// URI to read.

    Returns:
        ResourceContent with the resource data, or None if URI is invalid.
    """
    parsed = parse_uri(uri)
    if parsed is None:
        return None

    dispatch = {
        "projects_index": _read_projects_index,
        "project_detail": _read_project_detail,
        "chapters_index": _read_chapters_index,
        "chapter_detail": _read_chapter_detail,
        "characters_index": _read_characters_index,
        "character_detail": _read_character_detail,
        "worldbuilding_index": _read_worldbuilding_index,
        "worldbuilding_detail": _read_worldbuilding_detail,
        "outline_index": _read_outline_index,
        "outline_detail": _read_outline_detail,
        "relationships": _read_relationships,
        "rag_search": _read_rag_search,
    }

    reader = dispatch.get(parsed.resource_type)
    if reader is None:
        return None
    return reader(db, parsed)


def _read_projects_index(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import Project
    projects = db.query(Project).order_by(Project.updated_at.desc()).all()
    items = [
        {"id": p.id, "title": p.title, "description": p.description}
        for p in projects
    ]
    return ResourceContent(
        uri=parsed.uri,
        mime_type="application/json",
        text=json.dumps({"projects": items, "total": len(items)}, ensure_ascii=False),
    )


def _read_project_detail(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import Project
    project = db.query(Project).filter(Project.id == parsed.project_id).first()
    if not project:
        return ResourceContent(uri=parsed.uri, mime_type="application/json",
                               text=json.dumps({"error": "Project not found"}))
    data = {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "tags": project.tags,
        "narrative_perspective": project.narrative_perspective,
        "writing_style": project.writing_style,
        "daily_word_goal": project.daily_word_goal,
    }
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps(data, ensure_ascii=False))


def _read_chapters_index(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import Chapter
    chapters = db.query(Chapter).filter(
        Chapter.project_id == parsed.project_id
    ).order_by(Chapter.created_at).all()
    items = [
        {"id": c.id, "title": c.title, "word_count": c.word_count,
         "outline_node_id": c.outline_node_id}
        for c in chapters
    ]
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps({"chapters": items, "total": len(items)}, ensure_ascii=False))


def _read_chapter_detail(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import (
        Chapter, ChapterSummary, OutlineNode,
        ChapterCharacter, Character,
        ChapterWorldbuilding, WorldbuildingEntry,
    )
    chapter = db.query(Chapter).filter(
        Chapter.project_id == parsed.project_id,
        Chapter.id == parsed.entity_id,
    ).first()
    if not chapter:
        return ResourceContent(uri=parsed.uri, mime_type="application/json",
                               text=json.dumps({"error": "Chapter not found"}))

    data: dict[str, Any] = {
        "id": chapter.id,
        "title": chapter.title,
        "content": chapter.content,
        "word_count": chapter.word_count,
        "outline_node_id": chapter.outline_node_id,
    }

    # Linked summary
    if chapter.summary:
        data["summary"] = {
            "text": chapter.summary.summary_text,
            "key_events": chapter.summary.key_events,
        }

    # Linked outline node
    if chapter.outline_node_id:
        node = db.query(OutlineNode).filter(OutlineNode.id == chapter.outline_node_id).first()
        if node:
            data["outline_node"] = {
                "id": node.id,
                "title": node.title,
                "summary": node.summary,
                "node_type": node.node_type,
            }

    # Linked characters
    char_links = db.query(ChapterCharacter).filter(
        ChapterCharacter.chapter_id == chapter.id
    ).all()
    if char_links:
        characters = []
        for link in char_links:
            char = db.query(Character).filter(Character.id == link.character_id).first()
            if char:
                characters.append({
                    "id": char.id,
                    "name": char.name,
                    "role_type": char.role_type,
                    "appearance_type": link.appearance_type,
                })
        data["characters"] = characters

    # Linked worldbuilding
    wb_links = db.query(ChapterWorldbuilding).filter(
        ChapterWorldbuilding.chapter_id == chapter.id
    ).all()
    if wb_links:
        worldbuilding = []
        for link in wb_links:
            entry = db.query(WorldbuildingEntry).filter(
                WorldbuildingEntry.id == link.worldbuilding_entry_id
            ).first()
            if entry:
                worldbuilding.append({
                    "id": entry.id,
                    "title": entry.title,
                    "dimension": entry.dimension,
                })
        data["worldbuilding"] = worldbuilding

    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps(data, ensure_ascii=False))


def _read_characters_index(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import Character
    chars = db.query(Character).filter(
        Character.project_id == parsed.project_id
    ).order_by(Character.name).all()
    items = [
        {"id": c.id, "name": c.name, "role_type": c.role_type}
        for c in chars
    ]
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps({"characters": items, "total": len(items)}, ensure_ascii=False))


def _read_character_detail(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import Character
    char = db.query(Character).filter(
        Character.project_id == parsed.project_id,
        Character.id == parsed.entity_id,
    ).first()
    if not char:
        return ResourceContent(uri=parsed.uri, mime_type="application/json",
                               text=json.dumps({"error": "Character not found"}))
    data = {
        "id": char.id,
        "name": char.name,
        "appearance": char.appearance,
        "personality": char.personality,
        "background": char.background,
        "abilities": char.abilities,
        "role_type": char.role_type,
    }
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps(data, ensure_ascii=False))


def _read_worldbuilding_index(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import WorldbuildingEntry
    entries = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == parsed.project_id
    ).order_by(WorldbuildingEntry.sort_order).all()
    items = [
        {"id": e.id, "title": e.title, "dimension": e.dimension}
        for e in entries
    ]
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps({"worldbuilding": items, "total": len(items)}, ensure_ascii=False))


def _read_worldbuilding_detail(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import WorldbuildingEntry
    entry = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == parsed.project_id,
        WorldbuildingEntry.id == parsed.entity_id,
    ).first()
    if not entry:
        return ResourceContent(uri=parsed.uri, mime_type="application/json",
                               text=json.dumps({"error": "Worldbuilding entry not found"}))
    data = {
        "id": entry.id,
        "title": entry.title,
        "content": entry.content,
        "dimension": entry.dimension,
    }
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps(data, ensure_ascii=False))


def _read_outline_index(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import OutlineNode
    nodes = db.query(OutlineNode).filter(
        OutlineNode.project_id == parsed.project_id
    ).order_by(OutlineNode.sort_order).all()
    items = [
        {"id": n.id, "title": n.title, "node_type": n.node_type,
         "parent_id": n.parent_id, "status": n.status}
        for n in nodes
    ]
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps({"outline": items, "total": len(items)}, ensure_ascii=False))


def _read_outline_detail(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import OutlineNode
    node = db.query(OutlineNode).filter(
        OutlineNode.project_id == parsed.project_id,
        OutlineNode.id == parsed.entity_id,
    ).first()
    if not node:
        return ResourceContent(uri=parsed.uri, mime_type="application/json",
                               text=json.dumps({"error": "Outline node not found"}))
    data = {
        "id": node.id,
        "title": node.title,
        "summary": node.summary,
        "node_type": node.node_type,
        "status": node.status,
        "parent_id": node.parent_id,
    }
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps(data, ensure_ascii=False))


def _read_relationships(db: Any, parsed: ParsedUri) -> ResourceContent:
    import json
    from app.database.models import CharacterRelationship
    rels = db.query(CharacterRelationship).filter(
        CharacterRelationship.project_id == parsed.project_id
    ).all()
    items = [
        {"source_id": r.source_id, "target_id": r.target_id,
         "relationship_type": r.relationship_type, "description": r.description}
        for r in rels
    ]
    return ResourceContent(uri=parsed.uri, mime_type="application/json",
                           text=json.dumps({"relationships": items, "total": len(items)}, ensure_ascii=False))


def _read_rag_search(db: Any, parsed: ParsedUri) -> ResourceContent:
    """RAG search resource — returns search results by query parameter.

    URI format: moshu://projects/{id}/rag/search?q=keyword&limit=20
    """
    import json
    from app.services.rag.retriever import search_chunks
    from app.services.rag.indexer import project_has_chunks, reindex_project_types

    query = (parsed.query_params or {}).get("q", "").strip()
    if not query:
        return ResourceContent(
            uri=parsed.uri, mime_type="application/json",
            text=json.dumps({"error": "Missing query parameter 'q'"}),
        )

    limit = int((parsed.query_params or {}).get("limit", "20"))
    limit = max(1, min(limit, 50))

    # Auto-index if needed
    auto_indexed = False
    if not project_has_chunks(db, parsed.project_id):
        reindex_project_types(db, parsed.project_id)
        auto_indexed = True

    results = search_chunks(db, parsed.project_id, query, limit=limit)

    data = {
        "query": query,
        "auto_indexed": auto_indexed,
        "results": [
            {
                "chunk_id": r.chunk_id,
                "source_type": r.source_type,
                "source_id": r.source_id,
                "title": r.title,
                "content": r.content[:2000],
                "score": round(r.score, 2),
                "reason": r.reason,
            }
            for r in results
        ],
        "total": len(results),
    }
    return ResourceContent(
        uri=parsed.uri,
        mime_type="application/json",
        text=json.dumps(data, ensure_ascii=False),
    )
