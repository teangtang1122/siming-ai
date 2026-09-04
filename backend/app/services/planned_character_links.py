"""Resolve confirmed future-outline names when the named character is created."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..database.models import Character, OutlineNode, OutlineNodeCharacter


def resolve_planned_outline_character_links(
    db: Session,
    character: Character,
) -> list[str]:
    """Link an exact unresolved planned name to its newly created character.

    Outline confirmation records names that could not be linked because no
    card existed yet. Once the exact canonical name is created, this lifecycle
    step resolves only that stored reference. It never uses aliases, keywords,
    or semantic guesses.
    """

    name = str(character.name or "").strip()
    if not name:
        return []
    nodes = (
        db.query(OutlineNode)
        .filter(
            OutlineNode.project_id == character.project_id,
            OutlineNode.metadata_json.is_not(None),
        )
        .all()
    )
    resolved: list[str] = []
    for node in nodes:
        metadata = dict(node.metadata_json) if isinstance(node.metadata_json, dict) else {}
        unresolved = metadata.get("unlinked_planned_character_names")
        if not isinstance(unresolved, list):
            continue
        if name not in [str(value or "").strip() for value in unresolved]:
            continue
        if not any(link.character_id == character.id for link in node.linked_characters):
            node.linked_characters.append(
                OutlineNodeCharacter(
                    character_id=character.id,
                    role_in_scene="计划人物补链",
                )
            )
        metadata["unlinked_planned_character_names"] = [
            value
            for value in unresolved
            if str(value or "").strip() != name
        ]
        node.metadata_json = metadata
        resolved.append(node.id)
    return resolved


__all__ = ["resolve_planned_outline_character_links"]
