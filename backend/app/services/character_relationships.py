"""Deterministic persistence rules for directed character relationships."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.database.models import CharacterRelationship


def collapse_directed_relationship_pair(
    db: Session,
    project_id: str,
    source_id: str,
    target_id: str,
    *,
    preferred_id: str | None = None,
) -> tuple[CharacterRelationship | None, list[str]]:
    """Return the one authoritative row for a directed pair and remove extras.

    Relationship type and prose can evolve.  They are attributes of the edge,
    not part of its identity.  Keeping the pair as the identity makes HTTP,
    workspace-tool, CLI, and cataloging writes converge on the same row.
    """

    rows = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            CharacterRelationship.character_a_id == source_id,
            CharacterRelationship.character_b_id == target_id,
        )
        .order_by(
            CharacterRelationship.created_at.asc(),
            CharacterRelationship.id.asc(),
        )
        .all()
    )
    if not rows:
        return None, []

    preferred = next(
        (row for row in rows if preferred_id and row.id == preferred_id),
        None,
    )
    authoritative = preferred or rows[0]
    removed_ids: list[str] = []
    for row in rows:
        if row.id == authoritative.id:
            continue
        removed_ids.append(str(row.id))
        db.delete(row)
    return authoritative, removed_ids


def relationship_snapshot(value: CharacterRelationship) -> dict[str, Any]:
    return {
        "id": value.id,
        "project_id": value.project_id,
        "character_a_id": value.character_a_id,
        "character_b_id": value.character_b_id,
        "relationship_type": value.relationship_type,
        "description": value.description,
    }


__all__ = ["collapse_directed_relationship_pair", "relationship_snapshot"]
