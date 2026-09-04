"""Chapter relation cataloging writes."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, Chapter, OutlineNode
from ...modules.continuity.domain.cataloging_contract import (
    canonical_chapter_link_characters,
)
from .facts import record_cataloging_fact
from .links import link_chapter_character, link_chapter_worldbuilding
from .lookups import find_character_by_name_or_id, find_worldbuilding_by_title_or_id


def apply_chapter_link(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
) -> dict[str, Any]:
    linked = {"characters": [], "worldbuilding": [], "outline": None}
    normalized_character_links = canonical_chapter_link_characters(payload)
    worldbuilding_titles = list(payload.get("worldbuilding_titles") or [])
    generic_endpoints = [payload.get("source"), payload.get("target")]
    for value in generic_endpoints:
        name = str(value or "").strip()
        if not name or name == chapter.title:
            continue
        character = find_character_by_name_or_id(db, chapter.project_id, name)
        entry = find_worldbuilding_by_title_or_id(db, chapter.project_id, name)
        if entry and name not in worldbuilding_titles:
            worldbuilding_titles.append(name)
            continue

    for item in normalized_character_links:
        name = item["name"]
        appearance_type = item["appearance_type"]
        character = find_character_by_name_or_id(db, chapter.project_id, name)
        if character:
            link_chapter_character(
                db,
                chapter,
                character,
                appearance_type=appearance_type,
                description=str(payload.get("description") or "关联"),
            )
            if character.name not in linked["characters"]:
                linked["characters"].append(character.name)

    for title in worldbuilding_titles:
        entry = find_worldbuilding_by_title_or_id(db, chapter.project_id, title)
        if entry:
            link_chapter_worldbuilding(
                db,
                chapter,
                entry,
                str(payload.get("description") or "关联"),
            )
            if entry.title not in linked["worldbuilding"]:
                linked["worldbuilding"].append(entry.title)

    # The required chapter-outline candidate owns this relation.  A later
    # chapter_link candidate carries descriptive names only and must not
    # redirect the chapter by title, especially when old and new titles differ.
    outline = db.get(OutlineNode, chapter.outline_node_id) if chapter.outline_node_id else None
    if (
        outline is not None
        and outline.project_id == chapter.project_id
        and outline.node_type == "chapter"
    ):
        linked["outline"] = outline.title
    element_payload = {
        key: payload.get(key)
        for key in (
            "locations",
            "items",
            "events",
            "importance",
            "appearance_order",
            "description",
            "source",
            "target",
            "relation",
            "chapter",
        )
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
