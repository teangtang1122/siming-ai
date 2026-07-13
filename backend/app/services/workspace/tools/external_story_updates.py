"""Compatibility wrapper for external story updates.

External agents used to submit grouped updates here. In 2.7.0 this tool
converts those grouped updates into standard cataloging candidates and delegates
to archive_chapter_after_write, so all writes go through the same applier.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....database.models import Character, WorldbuildingEntry
from ....services.story_granularity import CHARACTER_STABLE_FIELDS, CHARACTER_STATE_FIELDS, NARRATIVE_STATE_FIELDS
from .story_granularity import archive_chapter_after_write


def _text(value: Any) -> str:
    return str(value or "").strip()


def _candidate_fields(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source[field] for field in fields if field in source and source[field] not in (None, "")}


def _legacy_candidates(
    db: Session,
    project_id: str,
    updates: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in updates.get("characters", []) if isinstance(updates.get("characters"), list) else []:
        if not isinstance(item, dict):
            continue
        char_id = _text(item.get("id") or item.get("character_id"))
        name = _text(item.get("name") or item.get("character_name"))
        character = None
        if char_id:
            character = db.query(Character).filter(
                Character.id == char_id,
                Character.project_id == project_id,
            ).first()
        if not character and name:
            character = db.query(Character).filter(
                Character.name == name,
                Character.project_id == project_id,
            ).first()
        if not character and char_id:
            skipped.append({"type": "character", "id": char_id, "reason": "not found"})
            continue
        identity = {"id": character.id, "name": character.name} if character else {"name": name}
        state = _candidate_fields(item, CHARACTER_STATE_FIELDS)
        stable = _candidate_fields(item, CHARACTER_STABLE_FIELDS)
        if state:
            candidates.append({"type": "character_state_update", **identity, **state})
        if stable:
            candidates.append({"type": "character_update", **identity, **stable})
        if not state and not stable:
            skipped.append({"type": "character", "id": char_id or name, "reason": "no supported fields"})

    for item in updates.get("relationships", []) if isinstance(updates.get("relationships"), list) else []:
        if isinstance(item, dict):
            candidates.append({"type": "character_relationship", **item})

    for item in updates.get("worldbuilding", []) if isinstance(updates.get("worldbuilding"), list) else []:
        if not isinstance(item, dict):
            continue
        entry_id = _text(item.get("id") or item.get("entry_id"))
        title = _text(item.get("title") or item.get("entry_title"))
        if entry_id:
            entry = db.query(WorldbuildingEntry).filter(
                WorldbuildingEntry.id == entry_id,
                WorldbuildingEntry.project_id == project_id,
            ).first()
            if not entry:
                skipped.append({"type": "worldbuilding", "id": entry_id, "reason": "not found"})
                continue
            payload = {"type": "worldbuilding_update", "id": entry.id, "title": entry.title, **item}
        elif title:
            payload = {"type": "worldbuilding_create", **item}
        else:
            skipped.append({"type": "worldbuilding", "reason": "missing title/id"})
            continue
        candidates.append(payload)

    for item in updates.get("outline", []) if isinstance(updates.get("outline"), list) else []:
        if isinstance(item, dict):
            action = _text(item.get("action") or item.get("operation"))
            candidates.append({"type": "outline_update" if action == "update" else "outline_create", **item})

    summary = updates.get("chapter_summary")
    if isinstance(summary, dict):
        candidates.append({"type": "chapter_summary", **summary})
    elif _text(summary):
        candidates.append({"type": "chapter_summary", "summary_text": _text(summary)})

    narrative = updates.get("narrative_state")
    if isinstance(narrative, dict):
        candidates.append({"type": "chapter_state", **narrative})
    else:
        narrative_payload = {
            field: updates[field]
            for field in NARRATIVE_STATE_FIELDS
            if field in updates and updates[field] not in (None, "", [], {})
        }
        if narrative_payload:
            candidates.append({"type": "chapter_state", **narrative_payload})

    for item in updates.get("chapter_links", []) if isinstance(updates.get("chapter_links"), list) else []:
        if isinstance(item, dict):
            candidates.append({"type": "chapter_link", **item})

    return candidates, skipped


async def apply_external_story_updates(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Convert legacy grouped updates to standard post-write archive candidates."""
    chapter_id = _text(args.get("chapter_id"))
    updates = args.get("updates", {})
    mode = _text(args.get("mode") or "manual").lower()
    mode = mode if mode in {"manual", "auto"} else "manual"

    if not isinstance(updates, dict):
        return {
            "tool": "apply_external_story_updates",
            "status": "skipped",
            "detail": "updates must be a dict",
            "data": None,
        }

    candidates, skipped = _legacy_candidates(db, project_id, updates)
    if not candidates:
        return {
            "tool": "apply_external_story_updates",
            "status": "ok",
            "detail": f"{mode} mode: 0 applied, 0 candidates, {len(skipped)} skipped",
            "data": {
                "mode": mode,
                "candidates": [],
                "applied": [],
                "skipped": skipped,
                "warnings": ["no_supported_updates"] if not skipped else [],
            },
        }

    archive = await archive_chapter_after_write(db, project_id, {
        "chapter_id": chapter_id,
        "outline_node_id": args.get("outline_node_id"),
        "context_manifest_id": args.get("context_manifest_id"),
        "_context_execution_route": args.get("_context_execution_route"),
        "candidates": candidates,
        "mode": mode,
        "source": "external_agent",
        "generate_if_missing": True,
    })
    data = archive.get("data") or {}
    applied = data.get("applied_events") or []
    manual_candidates = candidates if mode == "manual" else []
    warnings = list(data.get("warnings") or [])
    return {
        "tool": "apply_external_story_updates",
        "status": archive.get("status") if archive.get("status") != "error" else "error",
        "detail": (
            f"{mode} mode: {len(applied)} applied, "
            f"{len(manual_candidates)} candidates, {len(skipped)} skipped"
        ),
        "data": {
            "mode": mode,
            "candidates": manual_candidates,
            "applied": applied,
            "skipped": skipped,
            "warnings": warnings,
            "archive": data,
        },
    }
