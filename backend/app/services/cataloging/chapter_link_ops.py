"""Chapter relation cataloging writes."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, Chapter
from .facts import record_cataloging_fact
from .links import link_chapter_character, link_chapter_worldbuilding
from .lookups import find_character_by_name_or_id, find_outline_by_title_or_id, find_worldbuilding_by_title_or_id


def apply_chapter_link(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
) -> dict[str, Any]:
    linked = {"characters": [], "worldbuilding": [], "outline": None}
    for name in payload.get("character_names") or []:
        character = find_character_by_name_or_id(db, chapter.project_id, name)
        if character:
            link_chapter_character(db, chapter, character, str(payload.get("description") or "关联"))
            linked["characters"].append(character.name)

    for title in payload.get("worldbuilding_titles") or []:
        entry = find_worldbuilding_by_title_or_id(db, chapter.project_id, title)
        if entry:
            link_chapter_worldbuilding(db, chapter, entry, str(payload.get("description") or "关联"))
            linked["worldbuilding"].append(entry.title)

    outline_title = payload.get("outline_title")
    if outline_title:
        node = find_outline_by_title_or_id(db, chapter.project_id, outline_title)
        if node:
            chapter.outline_node_id = node.id
            linked["outline"] = node.title
    element_payload = {
        key: payload.get(key)
        for key in ("locations", "items", "events", "importance", "appearance_order", "description")
        if payload.get(key) not in (None, "", [], {})
    }
    fact = None
    if element_payload:
        element_payload.update({
            "chapter_id": chapter.id,
            "chapter_title": chapter.title,
            "linked": linked,
        })
        fact = record_cataloging_fact(
            db,
            candidate,
            chapter,
            fact_type="chapter_element_links",
            payload=element_payload,
            identity_keys=("chapter_id", "appearance_order", "description"),
        )

    return {
        "target_type": "chapter",
        "target_id": chapter.id,
        "old_value": None,
        "new_value": {**linked, "element_fact_id": fact.id if fact else None},
        "detail": "章节关联已更新",
    }
