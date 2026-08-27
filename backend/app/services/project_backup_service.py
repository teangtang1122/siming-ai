"""Project full backup / restore service.

Exports every piece of a project — settings, chapters and snapshots, outline,
characters (with versions/aliases/timeline/change logs), worldbuilding
(entries/relations/versions/timeline), narrative governance (foreshadowing,
causal edges, debts, checkpoints), chapter summaries and quality metrics —
into a single ZIP archive.

Import reverses the process with optional ID preservation. When ``preserve_ids``
is False (default) every entity receives a fresh UUID and all foreign-key
references are remapped so the restored project is self-consistent.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import date, datetime
from typing import Any

from sqlalchemy import DateTime, or_
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import ValidationError
from ..database.models import (
    AssistantConversation,
    AssistantMemory,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
    CausalEdge,
    Chapter,
    ChapterCharacter,
    ChapterDraft,
    ChapterGovernanceReview,
    ChapterQualityMetric,
    ChapterSnapshot,
    # continuity
    ChapterSummary,
    ChapterWorldbuilding,
    Character,
    CharacterAIConfig,
    CharacterAlias,
    CharacterChangeLog,
    CharacterNarrativeState,
    CharacterRelationship,
    CharacterTimeline,
    CharacterVersion,
    Foreshadowing,
    NarrativeCheckpoint,
    NarrativeDebt,
    NarrativeGovernanceEvent,
    NovelCreationArtifactVersion,
    NovelCreationEntity,
    NovelCreationImportChunk,
    NovelCreationMaterialImport,
    NovelCreationRunClaim,
    # creation
    NovelCreationSession,
    NovelCreationStageEvent,
    NovelCreationStageRun,
    OutlineNode,
    OutlineNodeCharacter,
    # story
    Project,
    RagChunk,
    # RAG content (optional, can be rebuilt)
    RagDocument,
    # operations
    ScheduledTask,
    WorldbuildingEntry,
    WorldbuildingRelation,
    WorldbuildingTimeline,
    WorldbuildingVersion,
)

BACKUP_FORMAT = "siming-project-backup"
BACKUP_VERSION = "1.0"

# ── helpers ────────────────────────────────────────────────────────────────────


def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value.isoformat()


def _model_to_dict(obj: Any, *, include_id: bool = True) -> dict[str, Any]:
    """Serialize a SQLAlchemy model into a plain dict.

    Foreign keys are included verbatim. ``_sa_instance_state`` is stripped.
    """
    data: dict[str, Any] = {}
    for column in obj.__table__.columns:
        key = column.name
        value = getattr(obj, key)
        if isinstance(value, datetime | date):
            value = _iso(value)
        elif isinstance(value, bytes):
            continue
        data[key] = value
    return data


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return _iso(value)
    if isinstance(value, (set, tuple, list)):
        return list(value)
    return str(value)


def _dump_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        default=_json_default,
        indent=2,
    ).encode("utf-8")


# ── export ─────────────────────────────────────────────────────────────────────


class ProjectBackupBuilder:
    """Collects every project-scoped row and serialises it into ZIP entries."""

    def __init__(self, db: Session, project_id: str):
        self.db = db
        self.project_id = project_id
        self.project = get_project_or_404(db, project_id)

    # -- collect per-type data ----------------------------------------------

    def _project_entry(self) -> dict[str, Any]:
        return _model_to_dict(self.project)

    def _chapter_entries(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(Chapter)
            .filter(Chapter.project_id == self.project_id)
            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _chapter_snapshots(self, chapter_ids: list[str]) -> list[dict[str, Any]]:
        if not chapter_ids:
            return []
        rows = (
            self.db.query(ChapterSnapshot)
            .filter(ChapterSnapshot.chapter_id.in_(chapter_ids))
            .order_by(ChapterSnapshot.chapter_id, ChapterSnapshot.version_number.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _chapter_summaries(self, chapter_ids: list[str]) -> list[dict[str, Any]]:
        if not chapter_ids:
            return []
        rows = (
            self.db.query(ChapterSummary)
            .filter(ChapterSummary.chapter_id.in_(chapter_ids))
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _chapter_characters(self, chapter_ids: list[str]) -> list[dict[str, Any]]:
        if not chapter_ids:
            return []
        rows = (
            self.db.query(ChapterCharacter)
            .filter(ChapterCharacter.chapter_id.in_(chapter_ids))
            .order_by(ChapterCharacter.chapter_id)
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _chapter_worldbuilding(self, chapter_ids: list[str]) -> list[dict[str, Any]]:
        if not chapter_ids:
            return []
        rows = (
            self.db.query(ChapterWorldbuilding)
            .filter(ChapterWorldbuilding.chapter_id.in_(chapter_ids))
            .order_by(ChapterWorldbuilding.chapter_id)
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _chapter_quality_metrics(self, chapter_ids: list[str]) -> list[dict[str, Any]]:
        if not chapter_ids:
            return []
        rows = (
            self.db.query(ChapterQualityMetric)
            .filter(ChapterQualityMetric.chapter_id.in_(chapter_ids))
            .order_by(ChapterQualityMetric.chapter_id, ChapterQualityMetric.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _chapter_governance_reviews(self, chapter_ids: list[str]) -> list[dict[str, Any]]:
        if not chapter_ids:
            return []
        rows = (
            self.db.query(ChapterGovernanceReview)
            .filter(ChapterGovernanceReview.chapter_id.in_(chapter_ids))
            .order_by(ChapterGovernanceReview.chapter_id, ChapterGovernanceReview.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _outline_entries(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(OutlineNode)
            .filter(OutlineNode.project_id == self.project_id)
            .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _outline_characters(self, outline_ids: list[str]) -> list[dict[str, Any]]:
        if not outline_ids:
            return []
        rows = (
            self.db.query(OutlineNodeCharacter)
            .filter(OutlineNodeCharacter.outline_node_id.in_(outline_ids))
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _character_entries(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(Character)
            .filter(Character.project_id == self.project_id)
            .order_by(Character.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _character_children(self, character_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not character_ids:
            return {
                "versions": [],
                "ai_configs": [],
                "aliases": [],
                "timeline": [],
                "change_logs": [],
            }
        return {
            "versions": [
                _model_to_dict(r)
                for r in self.db.query(CharacterVersion)
                .filter(CharacterVersion.character_id.in_(character_ids))
                .order_by(CharacterVersion.character_id, CharacterVersion.version_number.asc())
                .all()
            ],
            "ai_configs": [
                _model_to_dict(r)
                for r in self.db.query(CharacterAIConfig)
                .filter(CharacterAIConfig.character_id.in_(character_ids))
                .all()
            ],
            "aliases": [
                _model_to_dict(r)
                for r in self.db.query(CharacterAlias)
                .filter(CharacterAlias.character_id.in_(character_ids))
                .order_by(CharacterAlias.created_at.asc())
                .all()
            ],
            "timeline": [
                _model_to_dict(r)
                for r in self.db.query(CharacterTimeline)
                .filter(CharacterTimeline.character_id.in_(character_ids))
                .order_by(CharacterTimeline.character_id, CharacterTimeline.sort_order.asc())
                .all()
            ],
            "change_logs": [
                _model_to_dict(r)
                for r in self.db.query(CharacterChangeLog)
                .filter(CharacterChangeLog.character_id.in_(character_ids))
                .order_by(CharacterChangeLog.character_id, CharacterChangeLog.created_at.asc())
                .all()
            ],
        }

    def _character_relationships(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(CharacterRelationship)
            .filter(CharacterRelationship.project_id == self.project_id)
            .order_by(CharacterRelationship.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _worldbuilding_entries(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(WorldbuildingEntry)
            .filter(WorldbuildingEntry.project_id == self.project_id)
            .order_by(WorldbuildingEntry.dimension.asc(), WorldbuildingEntry.sort_order.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _worldbuilding_children(
        self, entry_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        if not entry_ids:
            return {"versions": [], "timeline": []}
        return {
            "versions": [
                _model_to_dict(r)
                for r in self.db.query(WorldbuildingVersion)
                .filter(WorldbuildingVersion.entry_id.in_(entry_ids))
                .order_by(WorldbuildingVersion.entry_id, WorldbuildingVersion.version_number.asc())
                .all()
            ],
            "timeline": [
                _model_to_dict(r)
                for r in self.db.query(WorldbuildingTimeline)
                .filter(WorldbuildingTimeline.entry_id.in_(entry_ids))
                .order_by(WorldbuildingTimeline.entry_id, WorldbuildingTimeline.sort_order.asc())
                .all()
            ],
        }

    def _worldbuilding_relations(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(WorldbuildingRelation)
            .filter(WorldbuildingRelation.project_id == self.project_id)
            .order_by(WorldbuildingRelation.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _narrative_governance(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "foreshadowings": [
                _model_to_dict(r)
                for r in self.db.query(Foreshadowing)
                .filter(Foreshadowing.project_id == self.project_id)
                .order_by(Foreshadowing.created_at.asc())
                .all()
            ],
            "causal_edges": [
                _model_to_dict(r)
                for r in self.db.query(CausalEdge)
                .filter(CausalEdge.project_id == self.project_id)
                .order_by(CausalEdge.created_at.asc())
                .all()
            ],
            "narrative_debts": [
                _model_to_dict(r)
                for r in self.db.query(NarrativeDebt)
                .filter(NarrativeDebt.project_id == self.project_id)
                .order_by(NarrativeDebt.created_at.asc())
                .all()
            ],
            "character_narrative_states": [
                _model_to_dict(r)
                for r in self.db.query(CharacterNarrativeState)
                .filter(CharacterNarrativeState.project_id == self.project_id)
                .order_by(
                    CharacterNarrativeState.character_id,
                    CharacterNarrativeState.created_at.asc(),
                )
                .all()
            ],
            "checkpoints": [
                _model_to_dict(r)
                for r in self.db.query(NarrativeCheckpoint)
                .filter(NarrativeCheckpoint.project_id == self.project_id)
                .order_by(NarrativeCheckpoint.sequence.asc())
                .all()
            ],
            "events": [
                _model_to_dict(r)
                for r in self.db.query(NarrativeGovernanceEvent)
                .filter(NarrativeGovernanceEvent.project_id == self.project_id)
                .order_by(NarrativeGovernanceEvent.created_at.asc())
                .all()
            ],
        }

    def _assistant_conversations(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(AssistantConversation)
            .filter(AssistantConversation.project_id == self.project_id)
            .order_by(AssistantConversation.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _assistant_messages(
        self, conversation_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not conversation_ids:
            return []
        rows = (
            self.db.query(AssistantMessage)
            .filter(AssistantMessage.conversation_id.in_(conversation_ids))
            .order_by(AssistantMessage.conversation_id, AssistantMessage.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _assistant_runs(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(AssistantRun)
            .filter(AssistantRun.project_id == self.project_id)
            .order_by(AssistantRun.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _assistant_run_steps(self, run_ids: list[str]) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        rows = (
            self.db.query(AssistantRunStep)
            .filter(AssistantRunStep.run_id.in_(run_ids))
            .order_by(AssistantRunStep.run_id, AssistantRunStep.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _assistant_memory(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(AssistantMemory)
            .filter(AssistantMemory.project_id == self.project_id)
            .order_by(AssistantMemory.importance.desc(), AssistantMemory.updated_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _chapter_drafts(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(ChapterDraft)
            .filter(ChapterDraft.project_id == self.project_id)
            .order_by(ChapterDraft.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _rag_documents(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(RagDocument)
            .filter(RagDocument.project_id == self.project_id)
            .order_by(RagDocument.indexed_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _rag_chunks(self, document_ids: list[str]) -> list[dict[str, Any]]:
        if not document_ids:
            return []
        rows = (
            self.db.query(RagChunk)
            .filter(RagChunk.document_id.in_(document_ids))
            .order_by(RagChunk.document_id, RagChunk.chunk_index.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _scheduled_tasks(self) -> list[dict[str, Any]]:
        rows = (
            self.db.query(ScheduledTask)
            .filter(ScheduledTask.project_id == self.project_id)
            .order_by(ScheduledTask.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _novel_creation_sessions(self) -> list[dict[str, Any]]:
        # A session may reference the project either as the source that
        # initiated it (draft) or as the formal project it created.
        rows = (
            self.db.query(NovelCreationSession)
            .filter(
                or_(
                    NovelCreationSession.source_project_id == self.project_id,
                    NovelCreationSession.created_project_id == self.project_id,
                )
            )
            .order_by(NovelCreationSession.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _novel_creation_runs(self, session_ids: list[str]) -> list[dict[str, Any]]:
        if not session_ids:
            return []
        rows = (
            self.db.query(NovelCreationStageRun)
            .filter(NovelCreationStageRun.session_id.in_(session_ids))
            .order_by(NovelCreationStageRun.session_id, NovelCreationStageRun.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _novel_creation_events(
        self, run_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not run_ids:
            return []
        rows = (
            self.db.query(NovelCreationStageEvent)
            .filter(NovelCreationStageEvent.run_id.in_(run_ids))
            .order_by(NovelCreationStageEvent.run_id, NovelCreationStageEvent.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _novel_creation_entities(self, session_ids: list[str]) -> list[dict[str, Any]]:
        if not session_ids:
            return []
        rows = (
            self.db.query(NovelCreationEntity)
            .filter(NovelCreationEntity.session_id.in_(session_ids))
            .order_by(NovelCreationEntity.session_id, NovelCreationEntity.created_at.asc())
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _novel_creation_artifact_versions(
        self, session_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not session_ids:
            return []
        rows = (
            self.db.query(NovelCreationArtifactVersion)
            .filter(NovelCreationArtifactVersion.session_id.in_(session_ids))
            .order_by(
                NovelCreationArtifactVersion.session_id,
                NovelCreationArtifactVersion.created_at.asc(),
            )
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _novel_creation_claims(self, session_ids: list[str]) -> list[dict[str, Any]]:
        if not session_ids:
            return []
        rows = (
            self.db.query(NovelCreationRunClaim)
            .filter(NovelCreationRunClaim.session_id.in_(session_ids))
            .order_by(
                NovelCreationRunClaim.session_id,
                NovelCreationRunClaim.created_at.asc(),
            )
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _novel_creation_material_imports(
        self, session_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not session_ids:
            return []
        rows = (
            self.db.query(NovelCreationMaterialImport)
            .filter(NovelCreationMaterialImport.session_id.in_(session_ids))
            .order_by(
                NovelCreationMaterialImport.session_id,
                NovelCreationMaterialImport.created_at.asc(),
            )
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    def _novel_creation_import_chunks(
        self, import_run_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not import_run_ids:
            return []
        rows = (
            self.db.query(NovelCreationImportChunk)
            .filter(NovelCreationImportChunk.import_run_id.in_(import_run_ids))
            .order_by(
                NovelCreationImportChunk.import_run_id,
                NovelCreationImportChunk.chunk_index.asc(),
            )
            .all()
        )
        return [_model_to_dict(r) for r in rows]

    # -- public build --------------------------------------------------------

    def build_archive(self) -> io.BytesIO:
        """Collect all project data and return an in-memory ZIP archive."""
        chapter_rows = self._chapter_entries()
        chapter_ids = [r["id"] for r in chapter_rows]
        outline_rows = self._outline_entries()
        outline_ids = [r["id"] for r in outline_rows]
        character_rows = self._character_entries()
        character_ids = [r["id"] for r in character_rows]
        character_children = self._character_children(character_ids)
        worldbuilding_rows = self._worldbuilding_entries()
        worldbuilding_ids = [r["id"] for r in worldbuilding_rows]
        worldbuilding_children = self._worldbuilding_children(worldbuilding_ids)
        conversation_rows = self._assistant_conversations()
        conversation_ids = [r["id"] for r in conversation_rows]
        run_rows = self._assistant_runs()
        run_ids = [r["id"] for r in run_rows]
        rag_docs = self._rag_documents()
        rag_doc_ids = [r["id"] for r in rag_docs]
        creation_sessions = self._novel_creation_sessions()
        creation_session_ids = [r["id"] for r in creation_sessions]
        creation_runs = self._novel_creation_runs(creation_session_ids)
        creation_run_ids = [r["id"] for r in creation_runs]
        creation_artifact_versions = self._novel_creation_artifact_versions(creation_session_ids)
        creation_claims = self._novel_creation_claims(creation_session_ids)
        creation_material_imports = self._novel_creation_material_imports(creation_session_ids)
        creation_import_run_ids = [r["id"] for r in creation_material_imports]
        creation_import_chunks = self._novel_creation_import_chunks(creation_import_run_ids)

        manifest = {
            "format": BACKUP_FORMAT,
            "format_version": BACKUP_VERSION,
            "app_version": "3.3.2",
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "project_id": self.project_id,
            "project_title": self.project.title,
            "content": {
                "chapters": len(chapter_rows),
                "outline_nodes": len(outline_rows),
                "characters": len(character_rows),
                "worldbuilding_entries": len(worldbuilding_rows),
                "assistant_conversations": len(conversation_rows),
                "assistant_runs": len(run_rows),
                "rag_documents": len(rag_docs),
                "creation_sessions": len(creation_sessions),
                "creation_artifact_versions": len(creation_artifact_versions),
                "creation_claims": len(creation_claims),
                "creation_material_imports": len(creation_material_imports),
                "creation_import_chunks": len(creation_import_chunks),
            },
        }

        payloads: dict[str, Any] = {
            "manifest.json": manifest,
            "project.json": self._project_entry(),
            "chapters.json": chapter_rows,
            "chapter_snapshots.json": self._chapter_snapshots(chapter_ids),
            "chapter_summaries.json": self._chapter_summaries(chapter_ids),
            "chapter_characters.json": self._chapter_characters(chapter_ids),
            "chapter_worldbuilding.json": self._chapter_worldbuilding(chapter_ids),
            "chapter_quality_metrics.json": self._chapter_quality_metrics(chapter_ids),
            "chapter_governance_reviews.json": self._chapter_governance_reviews(chapter_ids),
            "outline.json": outline_rows,
            "outline_characters.json": self._outline_characters(outline_ids),
            "characters.json": character_rows,
            "character_versions.json": character_children["versions"],
            "character_ai_configs.json": character_children["ai_configs"],
            "character_aliases.json": character_children["aliases"],
            "character_timeline.json": character_children["timeline"],
            "character_change_logs.json": character_children["change_logs"],
            "character_relationships.json": self._character_relationships(),
            "worldbuilding.json": worldbuilding_rows,
            "worldbuilding_versions.json": worldbuilding_children["versions"],
            "worldbuilding_timeline.json": worldbuilding_children["timeline"],
            "worldbuilding_relations.json": self._worldbuilding_relations(),
            "assistant_conversations.json": conversation_rows,
            "assistant_messages.json": self._assistant_messages(conversation_ids),
            "assistant_runs.json": run_rows,
            "assistant_run_steps.json": self._assistant_run_steps(run_ids),
            "assistant_memory.json": self._assistant_memory(),
            "chapter_drafts.json": self._chapter_drafts(),
            "rag_documents.json": rag_docs,
            "rag_chunks.json": self._rag_chunks(rag_doc_ids),
            "scheduled_tasks.json": self._scheduled_tasks(),
            "novel_creation_sessions.json": creation_sessions,
            "novel_creation_runs.json": creation_runs,
            "novel_creation_events.json": self._novel_creation_events(creation_run_ids),
            "novel_creation_entities.json": self._novel_creation_entities(creation_session_ids),
            "novel_creation_artifact_versions.json": creation_artifact_versions,
            "novel_creation_claims.json": creation_claims,
            "novel_creation_material_imports.json": creation_material_imports,
            "novel_creation_import_chunks.json": creation_import_chunks,
        }
        policy = self._narrative_governance()
        for key, value in policy.items():
            payloads[f"narrative_{key}.json"] = value

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, payload in payloads.items():
                zf.writestr(name, _dump_json(payload))
        buf.seek(0)
        return buf


def build_project_backup(db: Session, project_id: str) -> io.BytesIO:
    """Convenience one-liner: build a full backup ZIP for a project."""
    return ProjectBackupBuilder(db, project_id).build_archive()


# ── import ─────────────────────────────────────────────────────────────────────


class _IdMapper:
    """Maps old → new UUIDs when preserve_ids is False."""

    def __init__(self, preserve_ids: bool):
        self.preserve_ids = preserve_ids
        self._map: dict[str, str] = {}

    def remap(self, old_id: str | None) -> str | None:
        if not old_id:
            return None
        if self.preserve_ids:
            return old_id
        if old_id not in self._map:
            self._map[old_id] = str(uuid.uuid4())
        return self._map[old_id]

    def project(self, old_id: str) -> str:
        mapped = self.remap(old_id)
        assert mapped is not None
        return mapped


class ProjectBackupRestorer:
    """Reads a Siming backup ZIP and restores it into the database."""

    def __init__(
        self,
        db: Session,
        archive_bytes: bytes,
        *,
        preserve_ids: bool = False,
        new_title: str | None = None,
    ):
        self.db = db
        self.archive_bytes = archive_bytes
        self.preserve_ids = preserve_ids
        self.new_title = new_title
        self._files: dict[str, Any] = {}
        self._id = _IdMapper(preserve_ids)
        self._new_project_id: str | None = None
        self._source_project_id: str = ""

    # -- zip loading ---------------------------------------------------------

    def _load_archive(self) -> None:
        try:
            zf = zipfile.ZipFile(io.BytesIO(self.archive_bytes), "r")
        except zipfile.BadZipFile as exc:
            raise ValidationError("上传的文件不是有效的 ZIP 备份文件") from exc
        try:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                self._files[name] = json.loads(zf.read(name).decode("utf-8"))
        finally:
            zf.close()

    def _get(self, name: str) -> list[dict[str, Any]] | dict[str, Any] | None:
        return self._files.get(name)

    def _validate(self) -> None:
        manifest = self._files.get("manifest.json")
        if not isinstance(manifest, dict):
            raise ValidationError("备份文件缺少 manifest.json，无法识别为司命备份")
        if manifest.get("format") != BACKUP_FORMAT:
            raise ValidationError(
                f"不支持的备份格式: {manifest.get('format')}。"
                f"仅支持 {BACKUP_FORMAT}"
            )

    # -- low-level row plumbing ---------------------------------------------

    def _deserialize(self, row: dict[str, Any], model: type) -> Any:
        """Build a model instance from a backup row dict, applying ID remap.

        The strategy deliberately does not use ``model(**data)`` because
        some server-side defaults (e.g. default=generate_uuid) would be
        shadowed by an explicit ``None`` from JSON.
        """
        instance = model()
        for key, value in row.items():
            if value is None:
                continue
            setattr(instance, key, value)
        return instance

    def _coerce_row(self, row: dict[str, Any], model: type) -> dict[str, Any]:
        """Turn ISO datetime strings back into datetime objects for the model.

        ``_model_to_dict`` serialises datetimes to ISO strings before they are
        written into the archive; SQLite's DateTime binding requires real
        ``datetime`` objects, so every column must be coerced on restore.
        """
        coerced = dict(row)
        for column in model.__table__.columns:
            key = column.name
            value = coerced.get(key)
            if value is None or isinstance(value, datetime):
                continue
            if isinstance(column.type, DateTime):
                try:
                    coerced[key] = datetime.fromisoformat(str(value))
                except (ValueError, TypeError):
                    coerced[key] = None
        return coerced

    def _make(self, model: type, mapped: dict[str, Any]) -> Any:
        """Construct a model instance from a remapped row with datetime coercion."""
        return model(**self._coerce_row(mapped, model))

    # -- remap helpers -------------------------------------------------------

    def _remap_chapter(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["id"] = self._id.remap(row.get("id"))
        row["project_id"] = self._new_project_id
        old_node = row.get("outline_node_id")
        row["outline_node_id"] = self._id.remap(old_node) if old_node else None
        old_manifest = row.get("context_manifest_id")
        row["context_manifest_id"] = self._id.remap(old_manifest) if old_manifest else None
        return row

    def _remap_outline(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["id"] = self._id.remap(row.get("id"))
        row["project_id"] = self._new_project_id
        old_parent = row.get("parent_id")
        row["parent_id"] = self._id.remap(old_parent) if old_parent else None
        old_chapter = row.get("source_chapter_id")
        row["source_chapter_id"] = self._id.remap(old_chapter) if old_chapter else None
        return row

    def _remap_character(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["id"] = self._id.remap(row.get("id"))
        row["project_id"] = self._new_project_id
        for fk in ("last_seen_chapter_id", "last_updated_chapter_id"):
            old = row.get(fk)
            row[fk] = self._id.remap(old) if old else None
        return row

    def _remap_worldbuilding(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["id"] = self._id.remap(row.get("id"))
        row["project_id"] = self._new_project_id
        for fk in ("first_seen_chapter_id", "last_updated_chapter_id"):
            old = row.get(fk)
            row[fk] = self._id.remap(old) if old else None
        return row

    def _remap_chapter_child(
        self, row: dict[str, Any], *, chapter_fk: str
    ) -> dict[str, Any]:
        row = dict(row)
        row["id"] = self._id.remap(row.get("id"))
        old = row.get(chapter_fk)
        row[chapter_fk] = self._id.remap(old) if old else None
        return row

    def _remap_character_child(
        self, row: dict[str, Any], *, character_fk: str = "character_id"
    ) -> dict[str, Any]:
        row = dict(row)
        row["id"] = self._id.remap(row.get("id"))
        old = row.get(character_fk)
        row[character_fk] = self._id.remap(old) if old else None
        return row

    def _remap_worldbuilding_child(
        self, row: dict[str, Any], *, entry_fk: str = "entry_id"
    ) -> dict[str, Any]:
        row = dict(row)
        row["id"] = self._id.remap(row.get("id"))
        old = row.get(entry_fk)
        row[entry_fk] = self._id.remap(old) if old else None
        return row

    # -- restore project -----------------------------------------------------

    def _restore_project(self, project_data: dict[str, Any]) -> Project:
        data = dict(project_data)
        old_project_id = str(data.get("id") or "")
        self._source_project_id = old_project_id
        self._new_project_id = self._id.project(old_project_id)
        data["id"] = self._new_project_id
        if self.new_title:
            data["title"] = self.new_title[:200]
        # Drop fields that are not safe to restore
        for key in ("folder_path", "content_migrated_at"):
            data.pop(key, None)
        project = Project(**self._coerce_row(data, Project))
        self.db.add(project)
        self.db.flush()
        return project

    # -- restore chapters ----------------------------------------------------

    def _restore_chapters(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = self._remap_chapter(row)
            chapter = Chapter(**self._make(Chapter, mapped))
            self.db.add(chapter)
        self.db.flush()

    def _restore_chapter_children(
        self,
        rows: list[dict[str, Any]],
        *,
        model: type,
        chapter_fk: str = "chapter_id",
    ) -> None:
        for row in rows:
            mapped = self._remap_chapter_child(row, chapter_fk=chapter_fk)
            self.db.add(self._make(model, mapped))

    # -- restore outline -----------------------------------------------------

    def _restore_outline(self, rows: list[dict[str, Any]]) -> None:
        # Insert parent first so FK constraints are satisfied even when the
        # archive lists children before parents.
        ordered_rows = list(rows)
        inserted: set[str] = set()

        def insert(row: dict[str, Any]) -> None:
            old_id = str(row.get("id") or "")
            if old_id in inserted:
                return
            old_parent = row.get("parent_id")
            if old_parent:
                parent_row = next(
                    (r for r in ordered_rows if r.get("id") == old_parent),
                    None,
                )
                if parent_row:
                    insert(parent_row)
            mapped = self._remap_outline(row)
            self.db.add(self._make(OutlineNode, mapped))
            inserted.add(old_id)

        for row in ordered_rows:
            insert(row)
        self.db.flush()

    def _restore_outline_characters(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_node = mapped.get("outline_node_id")
            mapped["outline_node_id"] = self._id.remap(old_node) if old_node else None
            old_char = mapped.get("character_id")
            mapped["character_id"] = self._id.remap(old_char) if old_char else None
            self.db.add(self._make(OutlineNodeCharacter, mapped))

    # -- restore characters --------------------------------------------------

    def _restore_characters(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = self._remap_character(row)
            # Insert with a fresh id via the column default
            char = self._make(Character, mapped)
            self.db.add(char)
        self.db.flush()

    def _restore_character_children(
        self,
        rows: list[dict[str, Any]],
        *,
        model: type,
        character_fk: str = "character_id",
    ) -> None:
        for row in rows:
            mapped = self._remap_character_child(row, character_fk=character_fk)
            self.db.add(self._make(model, mapped))

    def _restore_character_relationships(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            for fk in ("character_a_id", "character_b_id"):
                old = mapped.get(fk)
                mapped[fk] = self._id.remap(old) if old else None
            self.db.add(self._make(CharacterRelationship, mapped))

    # -- restore worldbuilding -----------------------------------------------

    def _restore_worldbuilding(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = self._remap_worldbuilding(row)
            self.db.add(self._make(WorldbuildingEntry, mapped))
        self.db.flush()

    def _restore_worldbuilding_children(
        self,
        rows: list[dict[str, Any]],
        *,
        model: type,
        entry_fk: str = "entry_id",
    ) -> None:
        for row in rows:
            mapped = self._remap_worldbuilding_child(row, entry_fk=entry_fk)
            self.db.add(self._make(model, mapped))

    def _restore_worldbuilding_relations(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            for fk in ("source_entry_id", "target_entry_id"):
                old = mapped.get(fk)
                mapped[fk] = self._id.remap(old) if old else None
            self.db.add(self._make(WorldbuildingRelation, mapped))

    # -- restore narrative governance ----------------------------------------

    def _restore_narrative_items(
        self,
        rows: list[dict[str, Any]],
        *,
        model: type,
        extra_remap: dict[str, str] | None = None,
    ) -> None:
        extra_remap = extra_remap or {}
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            for column, old in list(mapped.items()):
                if old is None or column not in extra_remap:
                    continue
                mapped[column] = (_id := self._id.remap(old)) if old else None
            self.db.add(self._make(model, mapped))

    def _restore_narrative(self, data: dict[str, list[dict[str, Any]]]) -> None:
        models = {
            "foreshadowings": Foreshadowing,
            "causal_edges": CausalEdge,
            "narrative_debts": NarrativeDebt,
            "character_narrative_states": CharacterNarrativeState,
            "checkpoints": NarrativeCheckpoint,
            "events": NarrativeGovernanceEvent,
        }
        fk_map = {
            "foreshadowings": {
                "source_chapter_id": "chapter_id",
                "target_chapter_id": "chapter_id",
                "resolved_chapter_id": "chapter_id",
            },
            "causal_edges": {
                "source_chapter_id": "chapter_id",
                "resolved_chapter_id": "chapter_id",
            },
            "narrative_debts": {
                "source_chapter_id": "chapter_id",
                "target_chapter_id": "chapter_id",
                "resolved_chapter_id": "chapter_id",
                "linked_foreshadowing_id": "foreshadowing_id",
                "linked_causal_edge_id": "causal_edge_id",
            },
            "character_narrative_states": {
                "character_id": "character_id",
                "chapter_id": "chapter_id",
            },
            "checkpoints": {
                "chapter_id": "chapter_id",
                "chapter_snapshot_id": "chapter_snapshot_id",
            },
            "events": {
                "chapter_id": "chapter_id",
            },
        }
        for key, model in models.items():
            rows = data.get(key) or []
            if not rows:
                continue
            self._restore_narrative_items(
                rows,
                model=model,
                extra_remap=fk_map.get(key, {}),
            )

    # -- restore assistant ---------------------------------------------------

    def _restore_assistant_conversations(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            self.db.add(self._make(AssistantConversation, mapped))

    def _restore_assistant_messages(
        self,
        rows: list[dict[str, Any]],
        *,
        conversation_fk: str = "conversation_id",
    ) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old = mapped.get(conversation_fk)
            mapped[conversation_fk] = self._id.remap(old) if old else None
            self.db.add(self._make(AssistantMessage, mapped))

    def _restore_assistant_runs(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            self.db.add(self._make(AssistantRun, mapped))

    def _restore_assistant_run_steps(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old = mapped.get("run_id")
            mapped["run_id"] = self._id.remap(old) if old else None
            self.db.add(self._make(AssistantRunStep, mapped))

    def _restore_assistant_memory(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            self.db.add(self._make(AssistantMemory, mapped))

    def _restore_chapter_drafts(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            old_chapter = mapped.get("chapter_id")
            mapped["chapter_id"] = self._id.remap(old_chapter) if old_chapter else None
            self.db.add(self._make(ChapterDraft, mapped))

    # -- restore RAG ---------------------------------------------------------

    def _restore_rag_documents(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            self.db.add(self._make(RagDocument, mapped))

    def _restore_rag_chunks(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_doc = mapped.get("document_id")
            mapped["document_id"] = self._id.remap(old_doc) if old_doc else None
            self.db.add(self._make(RagChunk, mapped))

    # -- restore creation ----------------------------------------------------

    def _restore_creation_sessions(self, rows: list[dict[str, Any]]) -> None:
        old_project_id = str(getattr(self, "_source_project_id", "") or "")
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            for key in ("source_project_id", "created_project_id"):
                old = str(mapped.get(key) or "")
                mapped[key] = self._new_project_id if old == old_project_id else None
            self.db.add(self._make(NovelCreationSession, mapped))

    def _restore_creation_runs(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_session = mapped.get("session_id")
            mapped["session_id"] = self._id.remap(old_session) if old_session else None
            old_retry = mapped.get("retry_of_run_id")
            mapped["retry_of_run_id"] = self._id.remap(old_retry) if old_retry else None
            # Global runtimes (operation/context manifests) are not part of a
            # project archive; clear them so the restored rows stay consistent.
            mapped["operation_id"] = None
            mapped["context_manifest_id"] = None
            self.db.add(self._make(NovelCreationStageRun, mapped))

    def _restore_creation_events(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_run = mapped.get("run_id")
            mapped["run_id"] = self._id.remap(old_run) if old_run else None
            self.db.add(self._make(NovelCreationStageEvent, mapped))

    def _restore_creation_entities(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_session = mapped.get("session_id")
            mapped["session_id"] = self._id.remap(old_session) if old_session else None
            self.db.add(self._make(NovelCreationEntity, mapped))

    def _restore_creation_artifact_versions(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_session = mapped.get("session_id")
            mapped["session_id"] = self._id.remap(old_session) if old_session else None
            old_run = mapped.get("run_id")
            mapped["run_id"] = self._id.remap(old_run) if old_run else None
            for key in ("parent_version_id", "restored_from_version_id"):
                old = mapped.get(key)
                mapped[key] = self._id.remap(old) if old else None
            mapped["operation_id"] = None
            self.db.add(self._make(NovelCreationArtifactVersion, mapped))

    def _restore_creation_claims(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_session = mapped.get("session_id")
            mapped["session_id"] = self._id.remap(old_session) if old_session else None
            old_run = mapped.get("run_id")
            mapped["run_id"] = self._id.remap(old_run) if old_run else None
            mapped["operation_id"] = None
            self.db.add(self._make(NovelCreationRunClaim, mapped))

    def _restore_creation_material_imports(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_session = mapped.get("session_id")
            mapped["session_id"] = self._id.remap(old_session) if old_session else None
            mapped["operation_id"] = None
            self.db.add(self._make(NovelCreationMaterialImport, mapped))

    def _restore_creation_import_chunks(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            old_run = mapped.get("import_run_id")
            mapped["import_run_id"] = self._id.remap(old_run) if old_run else None
            self.db.add(self._make(NovelCreationImportChunk, mapped))

    # -- restore scheduled tasks ---------------------------------------------

    def _restore_scheduled_tasks(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            mapped = dict(row)
            mapped["id"] = self._id.remap(mapped.get("id"))
            mapped["project_id"] = self._new_project_id
            self.db.add(self._make(ScheduledTask, mapped))

    # -- main entry ----------------------------------------------------------

    def restore(self) -> dict[str, Any]:
        """Restore the archive and return a summary dict."""
        self._load_archive()
        self._validate()

        counts: dict[str, int] = {}

        # 1. Project
        project_row = self._get("project.json")
        if not isinstance(project_row, dict):
            raise ValidationError("备份缺少 project.json，无法创建作品")
        project = self._restore_project(project_row)
        project_id = str(project.id)

        # 2. Chapters (insert parent rows first)
        chapter_rows = self._get("chapters.json")
        if isinstance(chapter_rows, list):
            self._restore_chapters(chapter_rows)
            counts["chapters"] = len(chapter_rows)
        else:
            counts["chapters"] = 0

        self.db.flush()

        # 3. Chapter children
        chapter_children_specs = [
            ("chapter_snapshots.json", ChapterSnapshot, "chapter_id"),
            ("chapter_summaries.json", ChapterSummary, "chapter_id"),
            ("chapter_characters.json", ChapterCharacter, "chapter_id"),
            ("chapter_worldbuilding.json", ChapterWorldbuilding, "chapter_id"),
            ("chapter_quality_metrics.json", ChapterQualityMetric, "chapter_id"),
            ("chapter_governance_reviews.json", ChapterGovernanceReview, "chapter_id"),
        ]
        for filename, model, fk in chapter_children_specs:
            rows = self._get(filename)
            if isinstance(rows, list) and rows:
                self._restore_chapter_children(rows, model=model, chapter_fk=fk)
                counts[filename.replace(".json", "")] = len(rows)

        # 4. Outline
        outline_rows = self._get("outline.json")
        if isinstance(outline_rows, list):
            self._restore_outline(outline_rows)
            counts["outline_nodes"] = len(outline_rows)
        outline_char_rows = self._get("outline_characters.json")
        if isinstance(outline_char_rows, list) and outline_char_rows:
            self._restore_outline_characters(outline_char_rows)
            counts["outline_characters"] = len(outline_char_rows)

        # 5. Characters
        char_rows = self._get("characters.json")
        if isinstance(char_rows, list):
            self._restore_characters(char_rows)
            counts["characters"] = len(char_rows)

        self.db.flush()

        char_child_specs = [
            ("character_versions.json", CharacterVersion),
            ("character_ai_configs.json", CharacterAIConfig),
            ("character_aliases.json", CharacterAlias),
            ("character_timeline.json", CharacterTimeline),
            ("character_change_logs.json", CharacterChangeLog),
        ]
        for filename, model in char_child_specs:
            rows = self._get(filename)
            if isinstance(rows, list) and rows:
                self._restore_character_children(rows, model=model)
                counts[filename.replace(".json", "")] = len(rows)

        rel_rows = self._get("character_relationships.json")
        if isinstance(rel_rows, list) and rel_rows:
            self._restore_character_relationships(rel_rows)
            counts["character_relationships"] = len(rel_rows)

        # 6. Worldbuilding
        wb_rows = self._get("worldbuilding.json")
        if isinstance(wb_rows, list):
            self._restore_worldbuilding(wb_rows)
            counts["worldbuilding_entries"] = len(wb_rows)

        self.db.flush()

        wb_child_specs = [
            ("worldbuilding_versions.json", WorldbuildingVersion),
            ("worldbuilding_timeline.json", WorldbuildingTimeline),
        ]
        for filename, model in wb_child_specs:
            rows = self._get(filename)
            if isinstance(rows, list) and rows:
                self._restore_worldbuilding_children(rows, model=model)
                counts[filename.replace(".json", "")] = len(rows)

        wb_rel_rows = self._get("worldbuilding_relations.json")
        if isinstance(wb_rel_rows, list) and wb_rel_rows:
            self._restore_worldbuilding_relations(wb_rel_rows)
            counts["worldbuilding_relations"] = len(wb_rel_rows)

        # 7. Narrative governance
        narrative_sections = [
            "narrative_foreshadowings.json",
            "narrative_causal_edges.json",
            "narrative_narrative_debts.json",
            "narrative_character_narrative_states.json",
            "narrative_checkpoints.json",
            "narrative_events.json",
        ]
        narrative_data: dict[str, list[dict[str, Any]]] = {}
        for filename in narrative_sections:
            rows = self._get(filename)
            if isinstance(rows, list):
                key = filename.replace("narrative_", "").replace(".json", "")
                narrative_data[key] = rows
        if narrative_data:
            self._restore_narrative(narrative_data)
            for key, rows in narrative_data.items():
                counts[f"narrative_{key}"] = len(rows)

        # 8. Assistant conversations + messages
        conv_rows = self._get("assistant_conversations.json")
        if isinstance(conv_rows, list):
            self._restore_assistant_conversations(conv_rows)
            counts["assistant_conversations"] = len(conv_rows)
        msg_rows = self._get("assistant_messages.json")
        if isinstance(msg_rows, list) and msg_rows:
            self._restore_assistant_messages(msg_rows)
            counts["assistant_messages"] = len(msg_rows)

        # 9. Assistant runs + steps
        run_rows = self._get("assistant_runs.json")
        if isinstance(run_rows, list):
            self._restore_assistant_runs(run_rows)
            counts["assistant_runs"] = len(run_rows)
        step_rows = self._get("assistant_run_steps.json")
        if isinstance(step_rows, list) and step_rows:
            self._restore_assistant_run_steps(step_rows)
            counts["assistant_run_steps"] = len(step_rows)

        # 10. Assistant memory + chapter drafts
        mem_rows = self._get("assistant_memory.json")
        if isinstance(mem_rows, list) and mem_rows:
            self._restore_assistant_memory(mem_rows)
            counts["assistant_memory"] = len(mem_rows)
        draft_rows = self._get("chapter_drafts.json")
        if isinstance(draft_rows, list) and draft_rows:
            self._restore_chapter_drafts(draft_rows)
            counts["chapter_drafts"] = len(draft_rows)

        # 11. RAG (optional — can be rebuilt via cataloging)
        rag_doc_rows = self._get("rag_documents.json")
        if isinstance(rag_doc_rows, list) and rag_doc_rows:
            self._restore_rag_documents(rag_doc_rows)
            counts["rag_documents"] = len(rag_doc_rows)
        rag_chunk_rows = self._get("rag_chunks.json")
        if isinstance(rag_chunk_rows, list) and rag_chunk_rows:
            self._restore_rag_chunks(rag_chunk_rows)
            counts["rag_chunks"] = len(rag_chunk_rows)

        # 12. Novel creation sessions + runs (parents first)
        create_rows = self._get("novel_creation_sessions.json")
        if isinstance(create_rows, list) and create_rows:
            self._restore_creation_sessions(create_rows)
            counts["novel_creation_sessions"] = len(create_rows)
        create_run_rows = self._get("novel_creation_runs.json")
        if isinstance(create_run_rows, list) and create_run_rows:
            self._restore_creation_runs(create_run_rows)
            counts["novel_creation_runs"] = len(create_run_rows)

        self.db.flush()

        create_event_rows = self._get("novel_creation_events.json")
        if isinstance(create_event_rows, list) and create_event_rows:
            self._restore_creation_events(create_event_rows)
            counts["novel_creation_events"] = len(create_event_rows)
        create_entity_rows = self._get("novel_creation_entities.json")
        if isinstance(create_entity_rows, list) and create_entity_rows:
            self._restore_creation_entities(create_entity_rows)
            counts["novel_creation_entities"] = len(create_entity_rows)
        create_version_rows = self._get("novel_creation_artifact_versions.json")
        if isinstance(create_version_rows, list) and create_version_rows:
            self._restore_creation_artifact_versions(create_version_rows)
            counts["novel_creation_artifact_versions"] = len(create_version_rows)
        create_claim_rows = self._get("novel_creation_claims.json")
        if isinstance(create_claim_rows, list) and create_claim_rows:
            self._restore_creation_claims(create_claim_rows)
            counts["novel_creation_claims"] = len(create_claim_rows)

        self.db.flush()

        create_import_rows = self._get("novel_creation_material_imports.json")
        if isinstance(create_import_rows, list) and create_import_rows:
            self._restore_creation_material_imports(create_import_rows)
            counts["novel_creation_material_imports"] = len(create_import_rows)
        create_chunk_rows = self._get("novel_creation_import_chunks.json")
        if isinstance(create_chunk_rows, list) and create_chunk_rows:
            self._restore_creation_import_chunks(create_chunk_rows)
            counts["novel_creation_import_chunks"] = len(create_chunk_rows)

        # 13. Scheduled tasks
        task_rows = self._get("scheduled_tasks.json")
        if isinstance(task_rows, list) and task_rows:
            self._restore_scheduled_tasks(task_rows)
            counts["scheduled_tasks"] = len(task_rows)

        self.db.flush()

        return {
            "project_id": project_id,
            "project_title": project.title,
            "counts": counts,
            "preserved_ids": self.preserve_ids,
        }


def restore_project_backup(
    db: Session,
    archive_bytes: bytes,
    *,
    preserve_ids: bool = False,
    new_title: str | None = None,
) -> dict[str, Any]:
    """Convenience one-liner: restore a project backup ZIP."""
    restorer = ProjectBackupRestorer(
        db,
        archive_bytes,
        preserve_ids=preserve_ids,
        new_title=new_title,
    )
    return restorer.restore()


__all__ = [
    "BACKUP_FORMAT",
    "BACKUP_VERSION",
    "ProjectBackupBuilder",
    "ProjectBackupRestorer",
    "build_project_backup",
    "restore_project_backup",
]