"""Canonical chapter reading-order helpers.

Chapter.sort_order is the authoritative narrative sequence. outline_node_id is
planning metadata only and must never be used to infer chapter order.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database.models import Chapter

CHAPTER_ORDER_STEP = 1000
UNASSIGNED_CHAPTER_SORT_ORDER = 1_000_000_000


def next_chapter_sort_order(db: Session, project_id: str) -> int:
    highest = (
        db.query(func.max(Chapter.sort_order))
        .filter(Chapter.project_id == project_id)
        .scalar()
        or 0
    )
    return int(highest) + CHAPTER_ORDER_STEP


def chapter_order_asc() -> tuple:
    return (Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())


def chapter_order_desc() -> tuple:
    return (Chapter.sort_order.desc(), Chapter.created_at.desc(), Chapter.id.desc())


__all__ = [
    "CHAPTER_ORDER_STEP",
    "UNASSIGNED_CHAPTER_SORT_ORDER",
    "chapter_order_asc",
    "chapter_order_desc",
    "next_chapter_sort_order",
]
