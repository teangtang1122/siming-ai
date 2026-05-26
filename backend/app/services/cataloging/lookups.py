"""Local lookup helpers for cataloging.

These helpers intentionally avoid importing workspace assistant packages, so the
cataloging pipeline can be imported without optional web-search dependencies.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import Character, CharacterAlias, OutlineNode, WorldbuildingEntry


def find_character_by_name_or_id(db: Session, project_id: str, value: Any) -> Character | None:
    text = str(value or "").strip()
    if not text:
        return None
    character = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .filter((Character.id == text) | (Character.name == text))
        .first()
    )
    if character:
        return character
    alias = (
        db.query(CharacterAlias)
        .filter(CharacterAlias.project_id == project_id, CharacterAlias.alias == text)
        .order_by(CharacterAlias.updated_at.desc())
        .first()
    )
    return alias.character if alias else None


def find_worldbuilding_by_title_or_id(db: Session, project_id: str, value: Any) -> WorldbuildingEntry | None:
    text = str(value or "").strip()
    if not text:
        return None
    return (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .filter((WorldbuildingEntry.id == text) | (WorldbuildingEntry.title == text))
        .first()
    )


def find_outline_by_title_or_id(db: Session, project_id: str, value: Any) -> OutlineNode | None:
    text = str(value or "").strip()
    if not text:
        return None
    exact = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .filter((OutlineNode.id == text) | (OutlineNode.title == text))
        .order_by(OutlineNode.updated_at.desc())
        .first()
    )
    if exact:
        return exact

    normalized = normalize_lookup(text)
    for node in (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.updated_at.desc())
        .all()
    ):
        node_title = normalize_lookup(node.title)
        if node_title and (node_title == normalized or normalized in node_title or node_title in normalized):
            return node
    return None


def next_outline_sort_order(db: Session, project_id: str, parent_id: str | None) -> int:
    last = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.parent_id == parent_id)
        .order_by(OutlineNode.sort_order.desc(), OutlineNode.created_at.desc())
        .first()
    )
    return (last.sort_order + 1) if last and last.sort_order is not None else 0


def next_worldbuilding_sort_order(db: Session, project_id: str, dimension: str) -> int:
    last = (
        db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id, WorldbuildingEntry.dimension == dimension)
        .order_by(WorldbuildingEntry.sort_order.desc(), WorldbuildingEntry.created_at.desc())
        .first()
    )
    return (last.sort_order + 1) if last and last.sort_order is not None else 0


def normalize_lookup(value: str) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s:：，,。.!！?？（）()\[\]【】《》<>\"'“”‘’-]+", "", text)
