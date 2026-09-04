"""Resolve exact, project-owned sources selected for model-driven tasks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..database.models import (
    AssistantMemory,
    Chapter,
    Character,
    CharacterTimeline,
    ContextManifest,
    ContextManifestItem,
    OutlineNode,
    WorldbuildingEntry,
)
from ..database.query_filters import current_worldbuilding_clause
from .character_archive import character_archive_text
from .outline_service import load_outline_nodes, outline_chapter_number
from .rag.context_packer import estimate_tokens
from .rag.indexer import _get_source_content_hash


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clean_context_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if limit is None:
        return text
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


@dataclass(frozen=True)
class ExactTaskContextSource:
    source_type: str
    source_id: str
    source_hash: str
    title: str
    content: str
    lexical_score: float | None = None
    semantic_score: float | None = None
    recency_score: float | None = None
    structural_score: float | None = None
    final_score: float = 0.0

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.content)


class TaskContextSourceResolver:
    """Expand a verified task search result into its authoritative record."""

    def __init__(self, db: Session):
        self.db = db

    def _source_hash(
        self,
        project_id: str,
        source_type: str,
        source_id: str,
    ) -> str | None:
        if source_type == "narrative_governance":
            if source_id != project_id:
                return None
            try:
                from .narrative_governance import governance_context

                value = governance_context(self.db, project_id, limit=None)
            except Exception:
                return None
            return _sha256(value)
        value = _get_source_content_hash(self.db, source_type, source_id)
        return value or None

    def _timeline_source(self, project_id: str, source_id: str) -> tuple[str, str] | None:
        character = (
            self.db.query(Character)
            .filter(
                Character.project_id == project_id,
                Character.id == source_id,
            )
            .first()
        )
        if not character:
            return None
        rows = (
            self.db.query(CharacterTimeline)
            .filter(CharacterTimeline.character_id == source_id)
            .order_by(CharacterTimeline.created_at.desc())
            .all()
        )
        if not rows:
            return None
        content = "\n".join(
            f"[{row.event_type}] {row.event_description}"
            + (
                f" (emotional change: {row.emotional_state_change})"
                if row.emotional_state_change
                else ""
            )
            for row in reversed(rows)
        )
        return f"{character.name} timeline", clean_context_text(content)

    def _target_chapter_number(self, manifest: ContextManifest) -> int | None:
        if manifest.task_type != "writing":
            return None
        query = manifest.query_json if isinstance(manifest.query_json, dict) else {}
        arguments = query.get("arguments") if isinstance(query.get("arguments"), dict) else {}
        outline_id = str(arguments.get("outline_node_id") or "")
        if not outline_id:
            outline_id = next(
                (
                    str(item.source_id)
                    for item in manifest.items
                    if item.category == "target_outline" and item.source_id
                ),
                "",
            )
        if not outline_id:
            return None
        nodes = load_outline_nodes(self.db, manifest.project_id)
        return outline_chapter_number(nodes, outline_id)

    def _chapter_source(
        self, project_id: str, source_id: str, summary_only: bool
    ) -> tuple[str, str] | None:
        row = (
            self.db.query(Chapter)
            .filter(
                Chapter.project_id == project_id,
                Chapter.id == source_id,
            )
            .first()
        )
        if not row:
            return None
        summary = row.summary.summary_text if row.summary else ""
        if summary_only:
            content = "\n".join(
                value
                for value in (
                    f"Chapter summary: {row.title}",
                    clean_context_text(summary),
                )
                if value
            )
        else:
            values = [f"Chapter: {row.title}"]
            if summary:
                values.append(f"Summary: {clean_context_text(summary)}")
            values.append(f"Text:\n{clean_context_text(row.content)}")
            content = "\n".join(values)
        return row.title, content

    def _exact_content(
        self,
        project_id: str,
        source_type: str,
        source_id: str,
        *,
        manifest: ContextManifest | None = None,
    ) -> tuple[str, str] | None:
        if source_type == "character":
            row = (
                self.db.query(Character)
                .filter(
                    Character.project_id == project_id,
                    Character.id == source_id,
                )
                .first()
            )
            if not row:
                return None
            return row.name, "Character archive (authoritative):\n" + clean_context_text(
                character_archive_text(
                    row,
                    db=self.db,
                    target_chapter_number=(
                        self._target_chapter_number(manifest) if manifest is not None else None
                    ),
                ),
            )
        if source_type == "character_timeline":
            return self._timeline_source(project_id, source_id)
        if source_type == "worldbuilding":
            row = (
                self.db.query(WorldbuildingEntry)
                .filter(
                    WorldbuildingEntry.project_id == project_id,
                    WorldbuildingEntry.id == source_id,
                    current_worldbuilding_clause(WorldbuildingEntry.status),
                )
                .first()
            )
            if not row:
                return None
            return row.title, "\n".join(
                filter(
                    None,
                    (
                        f"Worldbuilding: {row.title}",
                        f"Dimension: {row.dimension}",
                        clean_context_text(row.content),
                    ),
                )
            )
        if source_type == "outline":
            row = (
                self.db.query(OutlineNode)
                .filter(
                    OutlineNode.project_id == project_id,
                    OutlineNode.id == source_id,
                )
                .first()
            )
            if not row:
                return None
            values = [f"Outline: {row.title}", f"Node type: {row.node_type or 'unknown'}"]
            for label, value in (
                ("Summary", row.summary),
                ("Planned", row.planned_summary),
                ("Actual", row.actual_summary),
                ("Status", row.status),
            ):
                if value:
                    values.append(f"{label}: {clean_context_text(value)}")
            return row.title or "Outline", "\n".join(values)
        if source_type in {"chapter", "chapter_summary"}:
            return self._chapter_source(
                project_id,
                source_id,
                source_type == "chapter_summary",
            )
        if source_type == "assistant_memory":
            row = (
                self.db.query(AssistantMemory)
                .filter(
                    AssistantMemory.project_id == project_id,
                    AssistantMemory.id == source_id,
                )
                .first()
            )
            return (row.key, clean_context_text(row.value)) if row else None
        if source_type == "narrative_governance":
            if source_id != project_id:
                return None
            try:
                from .narrative_governance import governance_context

                value = governance_context(self.db, project_id, limit=None)
            except Exception:
                return None
            return "Narrative governance ledger", clean_context_text(
                value or "Narrative governance: no due or high-risk items.",
            )
        return None

    def exact_source(
        self,
        manifest: ContextManifest,
        item: ContextManifestItem,
    ) -> ExactTaskContextSource | None:
        project_id = str(manifest.project_id or "")
        source_id = str(item.source_id or "")
        if not project_id or not source_id:
            return None
        resolved = self._exact_content(
            project_id,
            item.source_type,
            source_id,
            manifest=manifest,
        )
        source_hash = self._source_hash(project_id, item.source_type, source_id)
        if not resolved or not source_hash:
            return None
        title, content = resolved
        if not content.strip():
            return None
        return ExactTaskContextSource(
            source_type=item.source_type,
            source_id=source_id,
            source_hash=source_hash,
            title=title,
            content=content.strip(),
            lexical_score=item.lexical_score,
            semantic_score=item.semantic_score,
            recency_score=item.recency_score,
            structural_score=item.structural_score,
            final_score=float(item.final_score or 0),
        )

    def exact_identity_source(
        self,
        manifest: ContextManifest,
        source_type: str,
        source_id: str,
    ) -> ExactTaskContextSource | None:
        """Resolve one identity for a safe search preview under this manifest."""
        project_id = str(manifest.project_id or "")
        resolved = self._exact_content(
            project_id,
            source_type,
            source_id,
            manifest=manifest,
        )
        source_hash = self._source_hash(project_id, source_type, source_id)
        if not resolved or not source_hash:
            return None
        title, content = resolved
        return ExactTaskContextSource(
            source_type=source_type,
            source_id=source_id,
            source_hash=source_hash,
            title=title,
            content=content,
        )

    def governance_candidate(self, manifest: ContextManifest) -> ExactTaskContextSource | None:
        project_id = str(manifest.project_id or "")
        if not project_id:
            return None
        resolved = self._exact_content(project_id, "narrative_governance", project_id)
        source_hash = self._source_hash(project_id, "narrative_governance", project_id)
        if not resolved or not source_hash:
            return None
        title, content = resolved
        return ExactTaskContextSource(
            source_type="narrative_governance",
            source_id=project_id,
            source_hash=source_hash,
            title=title,
            content=content,
            structural_score=1.0,
            final_score=1.0,
        )


__all__ = [
    "ExactTaskContextSource",
    "TaskContextSourceResolver",
    "clean_context_text",
]
