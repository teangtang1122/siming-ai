"""Hard task anchors used to create compact context-manifest baselines."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..database.models import Chapter, ChapterDraft, OutlineNode
from ..modules.story.domain.outline_contract import OUTLINE_PROPOSAL_MAX_NODES
from .rag.context_packer import estimate_tokens


@dataclass
class ManifestCandidate:
    category: str
    source_type: str
    source_id: str | None
    title: str
    content: str
    required: bool = False
    pinned: bool = False
    tier: int = 4
    lexical_score: float | None = None
    semantic_score: float | None = None
    recency_score: float | None = None
    structural_score: float | None = None
    final_score: float = 0.0
    selection_reason: str = ""
    chunk_id: str | None = None
    source_hash: str | None = None
    applicable: bool = True

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.content)


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _outline_text(node: OutlineNode) -> str:
    values = [f"Outline: {node.title}", f"Node type: {node.node_type or 'unknown'}"]
    for label, value in (
        ("Summary", node.summary),
        ("Planned", node.planned_summary),
        ("Actual", node.actual_summary),
        ("Status", node.status),
    ):
        if value:
            values.append(f"{label}: {_clean_text(value, 1200)}")
    return "\n".join(values)


def _chapter_text(chapter: Chapter, *, max_chars: int = 12_000) -> str:
    body = _clean_text(chapter.content, max_chars)
    summary = chapter.summary.summary_text if chapter.summary else ""
    values = [f"Chapter: {chapter.title}"]
    if summary:
        values.append(f"Summary: {_clean_text(summary, 1800)}")
    values.append(f"Text:\n{body}")
    return "\n".join(values)


def outline_position_text(
    parent_id: str | None,
    insert_after_id: str | None,
    batch_count: int,
) -> str:
    return json.dumps(
        {
            "parent_id": parent_id or None,
            "insert_after_id": insert_after_id or None,
            "batch_count": max(
                1, min(OUTLINE_PROPOSAL_MAX_NODES, int(batch_count or 1))
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _outline_node(db: Session, project_id: str, node_id: str) -> OutlineNode | None:
    return (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.id == node_id)
        .first()
    )


def _collect_writing_target(
    db: Session,
    add: Callable[[ManifestCandidate], None],
    coverage: dict[str, Any],
    project_id: str,
    arguments: dict[str, Any],
) -> None:
    target_id = str(
        arguments.get("outline_node_id")
        or arguments.get("target_outline_node_id")
        or ""
    ).strip()
    node = _outline_node(db, project_id, target_id) if target_id else None
    if node is None:
        coverage["target_outline"] = {
            "required": True,
            "status": "missing",
            "item_count": 0,
            "reason": (
                "The selected outline node no longer exists."
                if target_id
                else "Writing needs a target outline or section."
            ),
        }
        return
    add(
        ManifestCandidate(
            category="target_outline",
            source_type="outline",
            source_id=node.id,
            title=node.title or "Target outline",
            content=_outline_text(node),
            required=True,
            tier=1,
            structural_score=1.0,
            final_score=1.0,
            selection_reason="Target outline/section required by the writing contract.",
        )
    )
    coverage["target_outline"] = {
        "required": True,
        "status": "covered",
        "item_count": 1,
    }

    source_draft_id = str(arguments.get("source_draft_id") or "").strip()
    if not source_draft_id:
        return
    source_draft = db.query(ChapterDraft).filter(
        ChapterDraft.project_id == project_id,
        ChapterDraft.id == source_draft_id,
        ChapterDraft.status == "pending",
    ).first()
    if source_draft is None or str(source_draft.outline_node_id or "") != target_id:
        coverage["target_draft"] = {
            "required": True,
            "status": "missing",
            "item_count": 0,
            "reason": "The selected pending chapter draft is unavailable or has a different outline.",
        }
        return

    from .workspace.generated_drafts import (
        chapter_draft_source_hash,
        chapter_draft_source_text,
    )

    add(
        ManifestCandidate(
            category="target_draft",
            source_type="chapter_draft",
            source_id=source_draft.id,
            title=source_draft.title or node.title or "Current unsaved chapter draft",
            content=chapter_draft_source_text(source_draft),
            required=True,
            tier=1,
            structural_score=1.0,
            final_score=1.0,
            selection_reason="Exact pending draft selected by the Agent for revision.",
            source_hash=chapter_draft_source_hash(source_draft),
        )
    )
    coverage["target_draft"] = {
        "required": True,
        "status": "covered",
        "item_count": 1,
    }


def _collect_outline_position(
    db: Session,
    add: Callable[[ManifestCandidate], None],
    coverage: dict[str, Any],
    project_id: str,
    arguments: dict[str, Any],
) -> None:
    parent_id = str(arguments.get("parent_id") or "").strip()
    insert_after_id = str(arguments.get("insert_after_id") or "").strip()
    parent = _outline_node(db, project_id, parent_id) if parent_id else None
    if parent_id and parent is None:
        coverage["outline_position"] = {
            "required": True,
            "status": "missing",
            "item_count": 0,
            "reason": "The requested outline parent does not exist in this project.",
        }
        return
    insert_after = (
        _outline_node(db, project_id, insert_after_id) if insert_after_id else None
    )
    if insert_after_id and insert_after is None:
        coverage["outline_position"] = {
            "required": True,
            "status": "missing",
            "item_count": 0,
            "reason": "The requested insertion anchor does not exist in this project.",
        }
        return
    resolved_parent_id = str(parent.id) if parent else str(
        (insert_after.parent_id if insert_after else "") or ""
    )
    if insert_after and str(insert_after.parent_id or "") != resolved_parent_id:
        coverage["outline_position"] = {
            "required": True,
            "status": "missing",
            "item_count": 0,
            "reason": "The insertion anchor is not a child of the requested parent.",
        }
        return
    if parent is not None:
        add(
            ManifestCandidate(
                category="outline_parent",
                source_type="outline",
                source_id=parent.id,
                title=parent.title or "Outline parent",
                content=_outline_text(parent),
                required=True,
                tier=1,
                structural_score=1.0,
                final_score=1.0,
                selection_reason="Author-selected parent for the proposed outline nodes.",
            )
        )
        coverage["outline_parent"] = {
            "required": True,
            "status": "covered",
            "item_count": 1,
        }
    else:
        coverage["outline_parent"] = {
            "required": False,
            "status": "not_applicable",
            "item_count": 0,
        }
    position = outline_position_text(
        resolved_parent_id or None,
        insert_after_id or None,
        int(arguments.get("batch_count") or 1),
    )
    add(
        ManifestCandidate(
            category="outline_position",
            source_type="inline",
            source_id="outline-position",
            title="Author-selected outline insertion position",
            content=position,
            required=True,
            tier=1,
            structural_score=1.0,
            final_score=1.0,
            selection_reason="Exact parent and insertion anchor for this outline proposal.",
        )
    )
    coverage["outline_position"] = {
        "required": True,
        "status": "covered",
        "item_count": 1,
    }


def _collect_text_target(
    db: Session,
    add: Callable[[ManifestCandidate], None],
    coverage: dict[str, Any],
    project_id: str,
    task_type: str,
    arguments: dict[str, Any],
) -> None:
    chapter_id = str(
        arguments.get("chapter_id") or arguments.get("target_chapter_id") or ""
    ).strip()
    chapter = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.id == chapter_id)
        .first()
        if chapter_id
        else None
    )
    if task_type == "cataloging":
        if chapter is not None:
            add(
                ManifestCandidate(
                    category="target_chapter",
                    source_type="chapter",
                    source_id=chapter.id,
                    title=chapter.title or "Target chapter",
                    content=_chapter_text(chapter),
                    required=True,
                    tier=1,
                    structural_score=1.0,
                    final_score=1.0,
                    selection_reason="Source chapter required for cataloging.",
                )
            )
            coverage["target_chapter"] = {
                "required": True,
                "status": "covered",
                "item_count": 1,
            }
        else:
            coverage["target_chapter"] = {
                "required": True,
                "status": "missing",
                "item_count": 0,
                "reason": "Target chapter not found." if chapter_id else "Cataloging needs chapter_id.",
            }
        return
    if task_type not in {"review", "rewrite"}:
        return
    direct_text = str(
        arguments.get("content")
        or arguments.get("text")
        or arguments.get("chapter_text")
        or ""
    ).strip()
    if chapter is not None:
        candidate = ManifestCandidate(
            category="target_text",
            source_type="chapter",
            source_id=chapter.id,
            title=chapter.title or "Target text",
            content=_chapter_text(chapter),
            required=True,
            tier=1,
            structural_score=1.0,
            final_score=1.0,
            selection_reason="Target chapter required by the review/rewrite contract.",
        )
    elif direct_text:
        candidate = ManifestCandidate(
            category="target_text",
            source_type="inline",
            source_id="inline-target",
            title=str(arguments.get("title") or "Inline target text"),
            content=_clean_text(direct_text, 20_000),
            required=True,
            tier=1,
            structural_score=1.0,
            final_score=1.0,
            selection_reason="Inline target text supplied by the caller.",
        )
    else:
        coverage["target_text"] = {
            "required": True,
            "status": "missing",
            "item_count": 0,
            "reason": "A target chapter or text is required.",
        }
        return
    add(candidate)
    coverage["target_text"] = {
        "required": True,
        "status": "covered",
        "item_count": 1,
    }


def collect_target_candidates(
    db: Session,
    add: Callable[[ManifestCandidate], None],
    coverage: dict[str, Any],
    project_id: str,
    task_type: str,
    arguments: dict[str, Any],
) -> None:
    if task_type == "writing":
        _collect_writing_target(db, add, coverage, project_id, arguments)
    elif task_type == "outline_planning":
        _collect_outline_position(db, add, coverage, project_id, arguments)
    _collect_text_target(db, add, coverage, project_id, task_type, arguments)


__all__ = [
    "ManifestCandidate",
    "collect_target_candidates",
    "outline_position_text",
]
