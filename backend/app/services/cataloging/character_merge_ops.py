"""Apply character merge candidates from cataloging."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, Chapter
from ..character_merge_service import merge_characters
from .lookups import find_character_by_name_or_id


def apply_character_merge_candidate(
    db: Session,
    candidate: CatalogingCandidate,
    chapter: Chapter,
    payload: dict[str, Any],
) -> dict[str, Any]:
    primary_name = str(payload.get("primary_name") or payload.get("canonical_name") or "").strip()
    secondary_name = str(payload.get("secondary_name") or "").strip()
    if not primary_name or not secondary_name:
        raise ValueError("角色合并候选缺少 primary_name 或 secondary_name")
    primary = find_character_by_name_or_id(db, chapter.project_id, primary_name)
    secondary = find_character_by_name_or_id(db, chapter.project_id, secondary_name)
    if not primary or not secondary:
        raise ValueError("角色合并需要两个已存在角色")
    result = merge_characters(
        db,
        chapter.project_id,
        primary.id,
        secondary.id,
        {
            **payload,
            "confidence": candidate.confidence,
            "reason": payload.get("confidence_reason") or payload.get("background_append") or candidate.evidence,
        },
        source_chapter=chapter,
    )
    result["detail"] = f"角色合并候选已应用: {secondary.name} -> {primary.name}"
    return result
