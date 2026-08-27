"""Strict, versioned Siming project-package export and import.

The package is an author-data interchange format, not a generic database dump.
Every collection and field is explicitly allowlisted, validated, and restored
with deterministic identifiers. Runtime state and integration configuration are
deliberately excluded.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from sqlalchemy import Boolean, DateTime, Float, Integer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import AppException
from ..database.models import (
    CausalEdge,
    Chapter,
    ChapterCharacter,
    ChapterDraft,
    ChapterGovernanceReview,
    ChapterSnapshot,
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
    NovelCreationArtifactVersion,
    NovelCreationEntity,
    NovelCreationImportChunk,
    NovelCreationMaterialImport,
    NovelCreationSession,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    ProjectPackageImportReceipt,
    WorldbuildingEntry,
    WorldbuildingRelation,
    WorldbuildingTimeline,
    WorldbuildingVersion,
)
from ..version import APP_VERSION
from .content_store import content_root
from .novel_creation_imports import parse_creation_material, split_creation_material
from .project_creation_context import resolve_project_creation_session
from .rag.indexer import reindex_project

PackageProfile = Literal["full", "structure"]

PACKAGE_FORMAT = "siming-project-package"
PACKAGE_FORMAT_VERSION = 1
PACKAGE_EXTENSION = ".siming-project"
PACKAGE_MEDIA_TYPE = "application/vnd.siming.project+zip"
PACKAGE_ID_NAMESPACE = uuid.UUID("8a746db1-9153-5a74-977c-4ad4dc1f6cb7")

MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRY_COUNT = 10_000
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_DATA_ENTRY_BYTES = 128 * 1024 * 1024
MAX_MATERIAL_BYTES = 25 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100

ERROR_INVALID = 4600
ERROR_VERSION = 4601
ERROR_LIMIT = 4602
ERROR_CONFLICT = 4603
ERROR_ASSET = 4604


class ProjectPackageError(AppException):
    """Stable project-package API error."""

    def __init__(self, code: int, message: str, status_code: int = 400):
        super().__init__(code=code, message=message, status_code=status_code)


@dataclass(frozen=True)
class CollectionSpec:
    key: str
    model: type
    fields: tuple[str, ...]
    profiles: frozenset[PackageProfile]

    @property
    def path(self) -> str:
        return f"data/{self.key}.jsonl"


COMMON = frozenset({"full", "structure"})
FULL = frozenset({"full"})

COLLECTION_SPECS: tuple[CollectionSpec, ...] = (
    CollectionSpec(
        "project",
        Project,
        (
            "id",
            "title",
            "description",
            "tags",
            "narrative_perspective",
            "writing_style",
            "forbidden_sentence_patterns",
            "rhetoric_guidelines",
            "short_sentences",
            "custom_style_prompt",
            "daily_word_goal",
            "created_at",
            "updated_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "creation_sessions",
        NovelCreationSession,
        (
            "id",
            "source_project_id",
            "created_project_id",
            "status",
            "mode",
            "user_brief",
            "target_audience",
            "genre",
            "platform",
            "schema_version",
            "current_stage",
            "revision",
            "review_json",
            "draft_json",
            "checkpoints_json",
            "created_at",
            "updated_at",
            "completed_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "creation_entities",
        NovelCreationEntity,
        (
            "id",
            "session_id",
            "artifact_key",
            "entity_type",
            "entity_key",
            "position",
            "status",
            "revision",
            "source",
            "data_json",
            "provenance_json",
            "created_at",
            "updated_at",
            "deleted_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "outline_nodes",
        OutlineNode,
        (
            "id",
            "project_id",
            "parent_id",
            "node_type",
            "title",
            "summary",
            "status",
            "source_chapter_id",
            "actual_summary",
            "planned_summary",
            "metadata_json",
            "sort_order",
            "created_at",
            "updated_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "characters",
        Character,
        (
            "id",
            "project_id",
            "name",
            "appearance",
            "personality",
            "background",
            "abilities",
            "role_type",
            "age",
            "current_version",
            "is_evolution_tracked",
            "life_status",
            "current_location",
            "realm_or_level",
            "physical_state",
            "mental_state",
            "current_goal",
            "active_conflict",
            "abilities_state",
            "items_or_assets",
            "profile_json",
            "last_seen_chapter_id",
            "last_updated_chapter_id",
            "created_at",
            "updated_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "character_ai_configs",
        CharacterAIConfig,
        (
            "id",
            "character_id",
            "tone_style",
            "catchphrases",
            "verbosity",
            "emotion_tendency",
            "created_at",
            "updated_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "character_aliases",
        CharacterAlias,
        (
            "id",
            "project_id",
            "character_id",
            "alias",
            "alias_type",
            "description",
            "confidence",
            "source_chapter_id",
            "merged_character_id",
            "created_at",
            "updated_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "character_relationships",
        CharacterRelationship,
        (
            "id",
            "project_id",
            "character_a_id",
            "character_b_id",
            "relationship_type",
            "description",
            "created_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "worldbuilding_entries",
        WorldbuildingEntry,
        (
            "id",
            "project_id",
            "dimension",
            "title",
            "content",
            "first_seen_chapter_id",
            "last_updated_chapter_id",
            "status",
            "confidence",
            "sort_order",
            "created_at",
            "updated_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "worldbuilding_relations",
        WorldbuildingRelation,
        (
            "id",
            "project_id",
            "source_entry_id",
            "target_entry_id",
            "relation_type",
            "description",
            "metadata_json",
            "created_at",
            "updated_at",
        ),
        COMMON,
    ),
    CollectionSpec(
        "outline_characters",
        OutlineNodeCharacter,
        ("id", "outline_node_id", "character_id", "role_in_scene", "created_at"),
        COMMON,
    ),
    CollectionSpec(
        "chapters",
        Chapter,
        (
            "id",
            "project_id",
            "outline_node_id",
            "title",
            "content",
            "word_count",
            "current_version",
            "sort_order",
            "created_at",
            "updated_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "chapter_snapshots",
        ChapterSnapshot,
        (
            "id",
            "chapter_id",
            "version_number",
            "content",
            "word_count",
            "trigger_type",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "chapter_summaries",
        ChapterSummary,
        (
            "id",
            "chapter_id",
            "summary_text",
            "key_events",
            "token_count",
            "created_at",
            "updated_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "chapter_characters",
        ChapterCharacter,
        ("id", "chapter_id", "character_id", "appearance_type", "description", "created_at"),
        FULL,
    ),
    CollectionSpec(
        "chapter_worldbuilding",
        ChapterWorldbuilding,
        ("id", "chapter_id", "worldbuilding_entry_id", "description", "created_at"),
        FULL,
    ),
    CollectionSpec(
        "chapter_drafts",
        ChapterDraft,
        (
            "id",
            "project_id",
            "title",
            "outline_node_id",
            "saved_chapter_id",
            "status",
            "content",
            "created_at",
            "updated_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "character_versions",
        CharacterVersion,
        (
            "id",
            "character_id",
            "version_number",
            "snapshot_data",
            "change_summary",
            "source_chapter_id",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "character_timelines",
        CharacterTimeline,
        (
            "id",
            "character_id",
            "chapter_id",
            "event_description",
            "event_type",
            "emotional_state_change",
            "sort_order",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "character_change_logs",
        CharacterChangeLog,
        (
            "id",
            "character_id",
            "chapter_id",
            "chapter_version",
            "change_type",
            "field_name",
            "old_value",
            "new_value",
            "confirmed",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "worldbuilding_versions",
        WorldbuildingVersion,
        (
            "id",
            "entry_id",
            "version_number",
            "snapshot_data",
            "change_summary",
            "source_chapter_id",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "worldbuilding_timelines",
        WorldbuildingTimeline,
        (
            "id",
            "entry_id",
            "chapter_id",
            "event_description",
            "event_type",
            "evidence",
            "sort_order",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "foreshadowings",
        Foreshadowing,
        (
            "id",
            "project_id",
            "title",
            "description",
            "status",
            "importance",
            "source_chapter_id",
            "target_chapter_id",
            "target_chapter_number",
            "resolved_chapter_id",
            "evidence",
            "source_chapter_version",
            "resolved_chapter_version",
            "resolution_note",
            "resolution_evidence",
            "verification_note",
            "verified_at",
            "last_checked_at",
            "stale_reason",
            "closed_by",
            "storyline",
            "dedupe_key",
            "source",
            "created_at",
            "updated_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "causal_edges",
        CausalEdge,
        (
            "id",
            "project_id",
            "cause",
            "effect",
            "causal_type",
            "strength",
            "status",
            "character_ids",
            "source_chapter_id",
            "resolved_chapter_id",
            "evidence",
            "source_chapter_version",
            "resolved_chapter_version",
            "resolution_note",
            "resolution_evidence",
            "verification_note",
            "verified_at",
            "last_checked_at",
            "stale_reason",
            "closed_by",
            "dedupe_key",
            "source",
            "created_at",
            "updated_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "narrative_debts",
        NarrativeDebt,
        (
            "id",
            "project_id",
            "debt_type",
            "title",
            "description",
            "status",
            "priority",
            "source_chapter_id",
            "target_chapter_id",
            "target_chapter_number",
            "resolved_chapter_id",
            "linked_foreshadowing_id",
            "linked_causal_edge_id",
            "evidence",
            "source_chapter_version",
            "resolved_chapter_version",
            "resolution_note",
            "resolution_evidence",
            "verification_note",
            "verified_at",
            "last_checked_at",
            "stale_reason",
            "closed_by",
            "dedupe_key",
            "source",
            "created_at",
            "updated_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "character_narrative_states",
        CharacterNarrativeState,
        (
            "id",
            "project_id",
            "character_id",
            "chapter_id",
            "current_goal",
            "public_stance",
            "hidden_intent",
            "emotional_residue",
            "relationship_tension",
            "behavior_boundaries",
            "evidence",
            "source",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "narrative_checkpoints",
        NarrativeCheckpoint,
        (
            "id",
            "project_id",
            "chapter_id",
            "chapter_snapshot_id",
            "sequence",
            "label",
            "trigger_type",
            "state_json",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "chapter_governance_reviews",
        ChapterGovernanceReview,
        (
            "id",
            "project_id",
            "chapter_id",
            "chapter_version",
            "status",
            "source",
            "findings_count",
            "confidence",
            "evidence",
            "reviewed_at",
            "created_at",
            "updated_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "creation_artifact_versions",
        NovelCreationArtifactVersion,
        (
            "id",
            "session_id",
            "artifact_key",
            "revision",
            "status",
            "source",
            "change_type",
            "snapshot_json",
            "change_summary_json",
            "parent_version_id",
            "restored_from_version_id",
            "created_at",
        ),
        FULL,
    ),
    CollectionSpec(
        "creation_materials",
        NovelCreationMaterialImport,
        (
            "id",
            "session_id",
            "filename",
            "media_type",
            "file_sha256",
            "size_bytes",
            "input_revision",
            "text_length",
            "selection_json",
            "created_at",
            "updated_at",
            "completed_at",
            "asset_path",
        ),
        FULL,
    ),
)

SPECS_BY_KEY = {spec.key: spec for spec in COLLECTION_SPECS}


@dataclass
class ExportedProjectPackage:
    path: Path
    temporary_root: Path
    filename: str

    def cleanup(self) -> None:
        shutil.rmtree(self.temporary_root, ignore_errors=True)


@dataclass
class ValidatedProjectPackage:
    source_path: Path
    staging_root: Path
    manifest: dict[str, Any]
    rows: dict[str, list[dict[str, Any]]]
    package_sha256: str

    @property
    def profile(self) -> PackageProfile:
        return self.manifest["profile"]

    def entry_path(self, archive_path: str) -> Path:
        return self.staging_root.joinpath(*PurePosixPath(archive_path).parts)

    def cleanup(self) -> None:
        shutil.rmtree(self.staging_root, ignore_errors=True)


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_iso) + "\n"
    ).encode("utf-8")


def _safe_filename(value: str, fallback: str = "project") -> str:
    forbidden = '<>:"/\\|?*\x00\r\n\t'
    cleaned = "".join("-" if char in forbidden or ord(char) < 32 else char for char in value)
    cleaned = " ".join(cleaned.split()).strip(" .-")[:100]
    return cleaned or fallback


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _serialize_row(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _iso(getattr(row, field)) for field in fields if field != "asset_path"}


def _clear_structure_references(key: str, row: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(row)
    chapter_reference_fields = {
        "source_chapter_id",
        "last_seen_chapter_id",
        "last_updated_chapter_id",
        "first_seen_chapter_id",
        "target_chapter_id",
        "resolved_chapter_id",
    }
    for field in chapter_reference_fields:
        if field in sanitized:
            sanitized[field] = None
    if key == "outline_nodes":
        sanitized["actual_summary"] = None
    if key == "creation_sessions":
        sanitized["checkpoints_json"] = None
    return sanitized


class ProjectPackageExporter:
    """Build an allowlisted project package on disk."""

    def __init__(self, db: Session, project_id: str, profile: PackageProfile):
        if profile not in {"full", "structure"}:
            raise ProjectPackageError(ERROR_INVALID, "项目包档位只能是 full 或 structure")
        self.db = db
        self.project = get_project_or_404(db, project_id)
        self.project_id = project_id
        self.profile = profile

    def _collect(self) -> dict[str, list[Any]]:
        creation = resolve_project_creation_session(self.db, self.project_id)
        creation_ids = [creation.id] if creation else []
        outlines = (
            self.db.query(OutlineNode)
            .filter(OutlineNode.project_id == self.project_id)
            .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
            .all()
        )
        outline_ids = [row.id for row in outlines]
        characters = (
            self.db.query(Character)
            .filter(Character.project_id == self.project_id)
            .order_by(Character.created_at.asc())
            .all()
        )
        character_ids = [row.id for row in characters]
        worlds = (
            self.db.query(WorldbuildingEntry)
            .filter(WorldbuildingEntry.project_id == self.project_id)
            .order_by(WorldbuildingEntry.sort_order.asc(), WorldbuildingEntry.created_at.asc())
            .all()
        )
        world_ids = [row.id for row in worlds]
        rows: dict[str, list[Any]] = {
            "project": [self.project],
            "creation_sessions": [creation] if creation else [],
            "creation_entities": (
                self.db.query(NovelCreationEntity)
                .filter(
                    NovelCreationEntity.session_id.in_(creation_ids),
                    NovelCreationEntity.deleted_at.is_(None),
                )
                .order_by(NovelCreationEntity.artifact_key, NovelCreationEntity.position.asc())
                .all()
                if creation_ids
                else []
            ),
            "outline_nodes": outlines,
            "characters": characters,
            "character_ai_configs": (
                self.db.query(CharacterAIConfig)
                .filter(CharacterAIConfig.character_id.in_(character_ids))
                .order_by(CharacterAIConfig.created_at.asc())
                .all()
                if character_ids
                else []
            ),
            "character_aliases": (
                self.db.query(CharacterAlias)
                .filter(CharacterAlias.project_id == self.project_id)
                .order_by(CharacterAlias.created_at.asc())
                .all()
            ),
            "character_relationships": (
                self.db.query(CharacterRelationship)
                .filter(CharacterRelationship.project_id == self.project_id)
                .order_by(CharacterRelationship.created_at.asc())
                .all()
            ),
            "worldbuilding_entries": worlds,
            "worldbuilding_relations": (
                self.db.query(WorldbuildingRelation)
                .filter(WorldbuildingRelation.project_id == self.project_id)
                .order_by(WorldbuildingRelation.created_at.asc())
                .all()
            ),
            "outline_characters": (
                self.db.query(OutlineNodeCharacter)
                .filter(OutlineNodeCharacter.outline_node_id.in_(outline_ids))
                .order_by(OutlineNodeCharacter.created_at.asc())
                .all()
                if outline_ids
                else []
            ),
        }
        if self.profile == "structure":
            return rows

        chapters = (
            self.db.query(Chapter)
            .filter(Chapter.project_id == self.project_id)
            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc())
            .all()
        )
        chapter_ids = [row.id for row in chapters]
        rows.update(
            {
                "chapters": chapters,
                "chapter_snapshots": self._children(
                    ChapterSnapshot, ChapterSnapshot.chapter_id, chapter_ids
                ),
                "chapter_summaries": self._children(
                    ChapterSummary, ChapterSummary.chapter_id, chapter_ids
                ),
                "chapter_characters": self._children(
                    ChapterCharacter, ChapterCharacter.chapter_id, chapter_ids
                ),
                "chapter_worldbuilding": self._children(
                    ChapterWorldbuilding, ChapterWorldbuilding.chapter_id, chapter_ids
                ),
                "chapter_drafts": (
                    self.db.query(ChapterDraft)
                    .filter(
                        ChapterDraft.project_id == self.project_id,
                        ChapterDraft.saved_chapter_id.is_(None),
                    )
                    .order_by(ChapterDraft.created_at.asc())
                    .all()
                ),
                "character_versions": self._children(
                    CharacterVersion, CharacterVersion.character_id, character_ids
                ),
                "character_timelines": self._children(
                    CharacterTimeline, CharacterTimeline.character_id, character_ids
                ),
                "character_change_logs": self._children(
                    CharacterChangeLog, CharacterChangeLog.character_id, character_ids
                ),
                "worldbuilding_versions": self._children(
                    WorldbuildingVersion, WorldbuildingVersion.entry_id, world_ids
                ),
                "worldbuilding_timelines": self._children(
                    WorldbuildingTimeline, WorldbuildingTimeline.entry_id, world_ids
                ),
                "foreshadowings": self._project_rows(Foreshadowing),
                "causal_edges": self._project_rows(CausalEdge),
                "narrative_debts": self._project_rows(NarrativeDebt),
                "character_narrative_states": self._project_rows(CharacterNarrativeState),
                "narrative_checkpoints": self._project_rows(NarrativeCheckpoint),
                "chapter_governance_reviews": (
                    self.db.query(ChapterGovernanceReview)
                    .filter(
                        ChapterGovernanceReview.project_id == self.project_id,
                        ChapterGovernanceReview.status == "verified",
                    )
                    .order_by(ChapterGovernanceReview.created_at.asc())
                    .all()
                ),
                "creation_artifact_versions": (
                    self.db.query(NovelCreationArtifactVersion)
                    .filter(NovelCreationArtifactVersion.session_id.in_(creation_ids))
                    .order_by(NovelCreationArtifactVersion.revision.asc())
                    .all()
                    if creation_ids
                    else []
                ),
                "creation_materials": (
                    self.db.query(NovelCreationMaterialImport)
                    .filter(NovelCreationMaterialImport.session_id.in_(creation_ids))
                    .order_by(NovelCreationMaterialImport.created_at.asc())
                    .all()
                    if creation_ids
                    else []
                ),
            }
        )
        return rows

    def _children(self, model: type, column: Any, parent_ids: list[str]) -> list[Any]:
        if not parent_ids:
            return []
        return (
            self.db.query(model)
            .filter(column.in_(parent_ids))
            .order_by(model.created_at.asc())
            .all()
        )

    def _project_rows(self, model: type) -> list[Any]:
        return (
            self.db.query(model)
            .filter(model.project_id == self.project_id)
            .order_by(model.created_at.asc())
            .all()
        )

    def build(self) -> ExportedProjectPackage:
        collected = self._collect()
        material_metadata: dict[str, tuple[Path, int, str]] = {}
        if self.profile == "full":
            materials = collected.get("creation_materials", [])
            missing = [
                row.filename
                for row in materials
                if not Path(str(row.stored_path)).expanduser().is_file()
            ]
            if missing:
                raise ProjectPackageError(
                    ERROR_ASSET,
                    f"完整项目包无法导出，以下原始素材不存在：{', '.join(missing)}",
                )
            oversized: list[str] = []
            mismatched: list[str] = []
            for row in materials:
                source = Path(str(row.stored_path)).expanduser()
                size = source.stat().st_size
                if size > MAX_MATERIAL_BYTES:
                    oversized.append(row.filename)
                    continue
                digest = _sha256_path(source)
                if digest != row.file_sha256 or size != row.size_bytes:
                    mismatched.append(row.filename)
                    continue
                material_metadata[row.id] = (source, size, digest)
            if oversized:
                raise ProjectPackageError(
                    ERROR_LIMIT,
                    f"以下原始素材超过 25MB 上限：{', '.join(oversized)}",
                    413,
                )
            if mismatched:
                raise ProjectPackageError(
                    ERROR_ASSET,
                    f"以下原始素材与记录不一致：{', '.join(mismatched)}",
                )
        temp_root = Path(tempfile.mkdtemp(prefix="siming-project-package-export-"))
        data_root = temp_root / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        package_path = temp_root / "project.siming-project"
        manifest_entries: list[dict[str, Any]] = []
        material_assets: list[tuple[Path, str, str]] = []

        for spec in COLLECTION_SPECS:
            if self.profile not in spec.profiles:
                continue
            path = temp_root / spec.path
            path.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with path.open("wb") as handle:
                for model_row in collected.get(spec.key, []):
                    row = _serialize_row(model_row, spec.fields)
                    if spec.key == "creation_sessions":
                        if row["source_project_id"] != self.project_id:
                            row["source_project_id"] = None
                        row["created_project_id"] = self.project_id
                    if self.profile == "structure":
                        row = _clear_structure_references(spec.key, row)
                    if spec.key == "chapter_drafts":
                        row["saved_chapter_id"] = None
                        row["status"] = "pending"
                    if spec.key == "creation_materials":
                        source, size, digest = material_metadata[model_row.id]
                        filename = _safe_filename(model_row.filename, "material")
                        asset_path = f"assets/materials/{model_row.id}/{filename}"
                        row["asset_path"] = asset_path
                        material_assets.append(
                            (source, asset_path, model_row.media_type or "application/octet-stream")
                        )
                    handle.write(_json_bytes(row))
                    count += 1
            manifest_entries.append(
                {
                    "path": spec.path,
                    "media_type": "application/x-ndjson",
                    "size": path.stat().st_size,
                    "sha256": _sha256_path(path),
                    "records": count,
                }
            )

        # Official packages use stored entries. This guarantees that even very
        # repetitive author text cannot make a package produced by Siming fail
        # the protocol's 100:1 compression-bomb guard when it is imported.
        with zipfile.ZipFile(package_path, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            for entry in manifest_entries:
                archive.write(temp_root / entry["path"], entry["path"])
            for source, archive_path, media_type in material_assets:
                archive.write(source, archive_path)
                manifest_entries.append(
                    {
                        "path": archive_path,
                        "media_type": media_type,
                        "size": source.stat().st_size,
                        "sha256": _sha256_path(source),
                        "records": 1,
                    }
                )
            manifest = {
                "format": PACKAGE_FORMAT,
                "format_version": PACKAGE_FORMAT_VERSION,
                "package_id": str(uuid.uuid4()),
                "profile": self.profile,
                "producer": {"name": "siming", "app_version": APP_VERSION},
                "exported_at": datetime.utcnow().isoformat() + "Z",
                "source_project": {"id": self.project.id, "title": self.project.title},
                "entries": manifest_entries,
            }
            archive.writestr("manifest.json", _json_bytes(manifest))

        if package_path.stat().st_size > MAX_COMPRESSED_BYTES:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise ProjectPackageError(
                ERROR_LIMIT,
                "项目包超过 512MiB 上限，请减少原始素材后重试",
                413,
            )

        profile_label = "完整" if self.profile == "full" else "结构"
        filename = f"{_safe_filename(self.project.title)}_{profile_label}项目包{PACKAGE_EXTENSION}"
        return ExportedProjectPackage(package_path, temp_root, filename)


MANIFEST_FIELDS = {
    "format",
    "format_version",
    "package_id",
    "profile",
    "producer",
    "exported_at",
    "source_project",
    "entries",
}
ENTRY_FIELDS = {"path", "media_type", "size", "sha256", "records"}
PRODUCER_FIELDS = {"name", "app_version"}
SOURCE_PROJECT_FIELDS = {"id", "title"}


def _require_exact_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        detail = []
        if missing:
            detail.append(f"缺少 {', '.join(missing)}")
        if unknown:
            detail.append(f"包含未知字段 {', '.join(unknown)}")
        raise ProjectPackageError(ERROR_INVALID, f"{label} 字段无效：{'；'.join(detail)}")


def _validate_archive_name(name: str) -> None:
    if not name or "\\" in name or "\x00" in name:
        raise ProjectPackageError(ERROR_INVALID, "项目包包含非法路径")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProjectPackageError(ERROR_INVALID, f"项目包包含不安全路径：{name}")


def _entry_limit(name: str) -> int:
    if name == "manifest.json":
        return MAX_MANIFEST_BYTES
    if name.startswith("data/"):
        return MAX_DATA_ENTRY_BYTES
    if name.startswith("assets/materials/"):
        return MAX_MATERIAL_BYTES
    return 0


def _validate_row_schema(spec: CollectionSpec, row: Any, line_number: int) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ProjectPackageError(
            ERROR_INVALID,
            f"{spec.path} 第 {line_number} 行必须是 JSON 对象",
        )
    expected = set(spec.fields)
    actual = set(row)
    if actual != expected:
        raise ProjectPackageError(
            ERROR_INVALID,
            f"{spec.path} 第 {line_number} 行字段与协议不一致",
        )
    model_columns = {column.name: column for column in spec.model.__table__.columns}
    for field, value in row.items():
        if field == "asset_path":
            if not isinstance(value, str) or not value:
                raise ProjectPackageError(ERROR_INVALID, f"{spec.path} 的 asset_path 无效")
            continue
        column = model_columns[field]
        if value is None:
            if not column.nullable and column.default is None and column.server_default is None:
                raise ProjectPackageError(
                    ERROR_INVALID,
                    f"{spec.path} 第 {line_number} 行的 {field} 不能为空",
                )
            continue
        if isinstance(column.type, DateTime):
            if not isinstance(value, str):
                raise ProjectPackageError(ERROR_INVALID, f"{spec.path} 的 {field} 必须是时间字符串")
            try:
                datetime.fromisoformat(
                    value.removesuffix("Z") + ("+00:00" if value.endswith("Z") else "")
                )
            except ValueError as exc:
                raise ProjectPackageError(
                    ERROR_INVALID, f"{spec.path} 的 {field} 时间格式无效"
                ) from exc
        elif isinstance(column.type, Boolean):
            if not isinstance(value, bool):
                raise ProjectPackageError(ERROR_INVALID, f"{spec.path} 的 {field} 必须是布尔值")
        elif isinstance(column.type, Integer):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ProjectPackageError(ERROR_INVALID, f"{spec.path} 的 {field} 必须是整数")
        elif isinstance(column.type, Float):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ProjectPackageError(ERROR_INVALID, f"{spec.path} 的 {field} 必须是数字")
        elif column.type.__class__.__name__ != "JSON" and not isinstance(value, str):
            raise ProjectPackageError(ERROR_INVALID, f"{spec.path} 的 {field} 必须是字符串")
    return row


class ProjectPackageValidator:
    """Validate and safely stage a package before any business write."""

    def __init__(self, source_path: Path, package_sha256: str | None = None):
        self.source_path = source_path
        self.package_sha256 = package_sha256 or _sha256_path(source_path)

    def validate(self) -> ValidatedProjectPackage:
        if self.source_path.stat().st_size > MAX_COMPRESSED_BYTES:
            raise ProjectPackageError(ERROR_LIMIT, "项目包超过 512MiB 上限", 413)
        staging_parent = content_root() / ".project-package-staging"
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix="validate-", dir=staging_parent))
        try:
            with zipfile.ZipFile(self.source_path, "r") as archive:
                manifest, infos = self._validate_zip_structure(archive)
                self._validate_manifest(manifest, infos)
                self._extract_entries(archive, infos, manifest, staging_root)
            rows = self._read_collections(manifest, staging_root)
            self._validate_material_links(manifest, rows)
            self._validate_identifiers(rows)
            self._validate_references(rows)
            return ValidatedProjectPackage(
                source_path=self.source_path,
                staging_root=staging_root,
                manifest=manifest,
                rows=rows,
                package_sha256=self.package_sha256,
            )
        except zipfile.BadZipFile as exc:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise ProjectPackageError(ERROR_INVALID, "文件不是有效的司命项目包") from exc
        except Exception:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise

    def _validate_zip_structure(
        self,
        archive: zipfile.ZipFile,
    ) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo]]:
        infos: dict[str, zipfile.ZipInfo] = {}
        total_uncompressed = 0
        for info in archive.infolist():
            if info.is_dir():
                raise ProjectPackageError(ERROR_INVALID, "项目包不得包含目录占位条目")
            if info.filename in infos:
                raise ProjectPackageError(ERROR_INVALID, f"项目包包含重复条目：{info.filename}")
            _validate_archive_name(info.filename)
            if info.flag_bits & 0x1:
                raise ProjectPackageError(ERROR_INVALID, "项目包不得加密")
            unix_mode = info.external_attr >> 16
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise ProjectPackageError(ERROR_INVALID, "项目包不得包含符号链接")
            limit = _entry_limit(info.filename)
            if not limit:
                raise ProjectPackageError(ERROR_INVALID, f"项目包包含未知条目：{info.filename}")
            if info.file_size > limit:
                raise ProjectPackageError(ERROR_LIMIT, f"项目包条目过大：{info.filename}", 413)
            if info.file_size and not info.compress_size:
                raise ProjectPackageError(ERROR_LIMIT, f"项目包压缩比异常：{info.filename}", 413)
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise ProjectPackageError(
                    ERROR_LIMIT, f"项目包压缩比超过 100:1：{info.filename}", 413
                )
            total_uncompressed += info.file_size
            infos[info.filename] = info
        if len(infos) > MAX_ENTRY_COUNT:
            raise ProjectPackageError(ERROR_LIMIT, "项目包条目数量超过 10000", 413)
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ProjectPackageError(ERROR_LIMIT, "项目包解压总量超过 2GiB", 413)
        manifest_info = infos.get("manifest.json")
        if manifest_info is None:
            raise ProjectPackageError(ERROR_INVALID, "文件缺少 manifest.json，不是司命项目包")
        try:
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectPackageError(ERROR_INVALID, "manifest.json 不是有效 UTF-8 JSON") from exc
        if not isinstance(manifest, dict):
            raise ProjectPackageError(ERROR_INVALID, "manifest.json 必须是 JSON 对象")
        return manifest, infos

    def _validate_manifest(
        self,
        manifest: dict[str, Any],
        infos: dict[str, zipfile.ZipInfo],
    ) -> None:
        _require_exact_fields(manifest, MANIFEST_FIELDS, "manifest.json")
        if manifest["format"] != PACKAGE_FORMAT:
            raise ProjectPackageError(
                ERROR_INVALID,
                "该文件不是司命项目包；TXT/Markdown/DOCX 请使用“导入外部小说”",
                415,
            )
        if manifest["format_version"] != PACKAGE_FORMAT_VERSION:
            raise ProjectPackageError(ERROR_VERSION, "不支持的司命项目包版本")
        try:
            uuid.UUID(str(manifest["package_id"]))
        except (ValueError, TypeError) as exc:
            raise ProjectPackageError(ERROR_INVALID, "项目包 package_id 无效") from exc
        profile = manifest["profile"]
        if profile not in {"full", "structure"}:
            raise ProjectPackageError(ERROR_INVALID, "项目包 profile 无效")
        producer = manifest["producer"]
        source_project = manifest["source_project"]
        if not isinstance(producer, dict) or not isinstance(source_project, dict):
            raise ProjectPackageError(ERROR_INVALID, "项目包生产者或来源信息无效")
        _require_exact_fields(producer, PRODUCER_FIELDS, "producer")
        _require_exact_fields(source_project, SOURCE_PROJECT_FIELDS, "source_project")
        if producer["name"] != "siming":
            raise ProjectPackageError(ERROR_INVALID, "项目包生产者不是司命")
        if not isinstance(producer["app_version"], str) or not producer["app_version"]:
            raise ProjectPackageError(ERROR_INVALID, "项目包导出版本无效")
        if not isinstance(manifest["exported_at"], str):
            raise ProjectPackageError(ERROR_INVALID, "项目包导出时间无效")
        try:
            datetime.fromisoformat(manifest["exported_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProjectPackageError(ERROR_INVALID, "项目包导出时间无效") from exc
        if not isinstance(source_project["id"], str) or not isinstance(
            source_project["title"], str
        ):
            raise ProjectPackageError(ERROR_INVALID, "项目包来源作品信息无效")
        entries = manifest["entries"]
        if not isinstance(entries, list):
            raise ProjectPackageError(ERROR_INVALID, "项目包 entries 必须是数组")
        expected_data = {spec.path for spec in COLLECTION_SPECS if profile in spec.profiles}
        declared: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ProjectPackageError(ERROR_INVALID, "项目包 entry 必须是对象")
            _require_exact_fields(entry, ENTRY_FIELDS, "entry")
            path = entry["path"]
            if not isinstance(path, str) or path == "manifest.json":
                raise ProjectPackageError(ERROR_INVALID, "项目包 entry 路径无效")
            _validate_archive_name(path)
            if path in declared:
                raise ProjectPackageError(ERROR_INVALID, f"manifest 重复声明：{path}")
            if path.startswith("data/") and path not in expected_data:
                raise ProjectPackageError(ERROR_INVALID, f"项目包包含档位不允许的数据：{path}")
            if path.startswith("assets/materials/") and profile != "full":
                raise ProjectPackageError(ERROR_INVALID, "结构项目包不得包含素材文件")
            if not path.startswith("data/") and not path.startswith("assets/materials/"):
                raise ProjectPackageError(ERROR_INVALID, f"项目包包含未知路径：{path}")
            info = infos.get(path)
            if info is None:
                raise ProjectPackageError(ERROR_INVALID, f"项目包缺少已声明条目：{path}")
            if not isinstance(entry["size"], int) or entry["size"] != info.file_size:
                raise ProjectPackageError(ERROR_INVALID, f"项目包条目大小不一致：{path}")
            if not isinstance(entry["records"], int) or entry["records"] < 0:
                raise ProjectPackageError(ERROR_INVALID, f"项目包记录数无效：{path}")
            digest = entry["sha256"]
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ProjectPackageError(ERROR_INVALID, f"项目包哈希无效：{path}")
            if path.startswith("data/") and entry["media_type"] != "application/x-ndjson":
                raise ProjectPackageError(ERROR_INVALID, f"项目包数据媒体类型无效：{path}")
            if path.startswith("assets/") and entry["records"] != 1:
                raise ProjectPackageError(ERROR_INVALID, f"项目包素材记录数无效：{path}")
            if not isinstance(entry["media_type"], str) or not entry["media_type"]:
                raise ProjectPackageError(ERROR_INVALID, f"项目包媒体类型无效：{path}")
            declared[path] = entry
        if set(declared).intersection(expected_data) != expected_data:
            missing = sorted(expected_data - set(declared))
            raise ProjectPackageError(ERROR_INVALID, f"项目包缺少数据集合：{', '.join(missing)}")
        actual = set(infos) - {"manifest.json"}
        if actual != set(declared):
            raise ProjectPackageError(ERROR_INVALID, "ZIP 条目与 manifest 声明不一致")

    def _extract_entries(
        self,
        archive: zipfile.ZipFile,
        infos: dict[str, zipfile.ZipInfo],
        manifest: dict[str, Any],
        staging_root: Path,
    ) -> None:
        for entry in manifest["entries"]:
            path = entry["path"]
            target = staging_root.joinpath(*PurePosixPath(path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            written = 0
            with archive.open(infos[path], "r") as source, target.open("wb") as destination:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _entry_limit(path):
                        raise ProjectPackageError(ERROR_LIMIT, f"项目包条目解压超限：{path}", 413)
                    digest.update(chunk)
                    destination.write(chunk)
            if written != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise ProjectPackageError(ERROR_INVALID, f"项目包条目校验失败：{path}")

    def _read_collections(
        self,
        manifest: dict[str, Any],
        staging_root: Path,
    ) -> dict[str, list[dict[str, Any]]]:
        declared = {entry["path"]: entry for entry in manifest["entries"]}
        rows: dict[str, list[dict[str, Any]]] = {}
        for spec in COLLECTION_SPECS:
            if manifest["profile"] not in spec.profiles:
                continue
            parsed: list[dict[str, Any]] = []
            path = staging_root / spec.path
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            raise ProjectPackageError(
                                ERROR_INVALID,
                                f"{spec.path} 第 {line_number} 行为空",
                            )
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise ProjectPackageError(
                                ERROR_INVALID,
                                f"{spec.path} 第 {line_number} 行不是有效 JSON",
                            ) from exc
                        parsed.append(_validate_row_schema(spec, value, line_number))
            except UnicodeDecodeError as exc:
                raise ProjectPackageError(ERROR_INVALID, f"{spec.path} 不是 UTF-8") from exc
            if len(parsed) != declared[spec.path]["records"]:
                raise ProjectPackageError(ERROR_INVALID, f"{spec.path} 记录数与 manifest 不一致")
            rows[spec.key] = parsed
        if len(rows.get("project", [])) != 1:
            raise ProjectPackageError(ERROR_INVALID, "项目包必须且只能包含一条 project 记录")
        project_row = rows["project"][0]
        if project_row["id"] != manifest["source_project"]["id"]:
            raise ProjectPackageError(ERROR_INVALID, "项目包来源作品 ID 不一致")
        return rows

    def _validate_material_links(
        self,
        manifest: dict[str, Any],
        rows: dict[str, list[dict[str, Any]]],
    ) -> None:
        declared = {entry["path"]: entry for entry in manifest["entries"]}
        referenced: set[str] = set()
        for row in rows.get("creation_materials", []):
            path = row["asset_path"]
            entry = declared.get(path)
            if entry is None or not path.startswith(f"assets/materials/{row['id']}/"):
                raise ProjectPackageError(ERROR_INVALID, f"素材条目引用无效：{row['filename']}")
            if entry["sha256"] != row["file_sha256"] or entry["size"] != row["size_bytes"]:
                raise ProjectPackageError(ERROR_INVALID, f"素材元数据不一致：{row['filename']}")
            referenced.add(path)
        material_entries = {path for path in declared if path.startswith("assets/materials/")}
        if material_entries != referenced:
            raise ProjectPackageError(ERROR_INVALID, "项目包包含未关联的素材文件")

    def _validate_identifiers(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        identities: dict[str, str] = {}
        for key, collection in rows.items():
            for row in collection:
                source_id = row.get("id")
                if not isinstance(source_id, str) or not source_id.strip():
                    raise ProjectPackageError(ERROR_INVALID, f"{key} 包含无效 ID")
                previous = identities.get(source_id)
                if previous is not None:
                    raise ProjectPackageError(
                        ERROR_INVALID,
                        f"项目包 ID 在 {previous} 与 {key} 中重复：{source_id}",
                    )
                identities[source_id] = key

    def _validate_references(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        identifiers = {key: {row["id"] for row in collection} for key, collection in rows.items()}
        for key, collection in rows.items():
            targets = REFERENCE_TARGETS.get(key, {})
            for row in collection:
                for field, target_key in targets.items():
                    value = row.get(field)
                    if value is None:
                        continue
                    if not isinstance(value, str) or value not in identifiers.get(
                        target_key, set()
                    ):
                        raise ProjectPackageError(
                            ERROR_INVALID,
                            f"{key}.{field} 引用了项目包外的实体",
                        )
        character_ids = identifiers.get("characters", set())
        for row in rows.get("causal_edges", []):
            values = row.get("character_ids")
            if not isinstance(values, list) or any(
                not isinstance(value, str) or value not in character_ids for value in values
            ):
                raise ProjectPackageError(
                    ERROR_INVALID,
                    "causal_edges.character_ids 包含项目包外的角色",
                )


REFERENCE_TARGETS: dict[str, dict[str, str]] = {
    "creation_sessions": {
        "source_project_id": "project",
        "created_project_id": "project",
    },
    "creation_entities": {"session_id": "creation_sessions"},
    "outline_nodes": {
        "project_id": "project",
        "parent_id": "outline_nodes",
        "source_chapter_id": "chapters",
    },
    "characters": {
        "project_id": "project",
        "last_seen_chapter_id": "chapters",
        "last_updated_chapter_id": "chapters",
    },
    "character_ai_configs": {"character_id": "characters"},
    "character_aliases": {
        "project_id": "project",
        "character_id": "characters",
        "source_chapter_id": "chapters",
        "merged_character_id": "characters",
    },
    "character_relationships": {
        "project_id": "project",
        "character_a_id": "characters",
        "character_b_id": "characters",
    },
    "worldbuilding_entries": {
        "project_id": "project",
        "first_seen_chapter_id": "chapters",
        "last_updated_chapter_id": "chapters",
    },
    "worldbuilding_relations": {
        "project_id": "project",
        "source_entry_id": "worldbuilding_entries",
        "target_entry_id": "worldbuilding_entries",
    },
    "outline_characters": {
        "outline_node_id": "outline_nodes",
        "character_id": "characters",
    },
    "chapters": {"project_id": "project", "outline_node_id": "outline_nodes"},
    "chapter_snapshots": {"chapter_id": "chapters"},
    "chapter_summaries": {"chapter_id": "chapters"},
    "chapter_characters": {
        "chapter_id": "chapters",
        "character_id": "characters",
    },
    "chapter_worldbuilding": {
        "chapter_id": "chapters",
        "worldbuilding_entry_id": "worldbuilding_entries",
    },
    "chapter_drafts": {
        "project_id": "project",
        "outline_node_id": "outline_nodes",
        "saved_chapter_id": "chapters",
    },
    "character_versions": {
        "character_id": "characters",
        "source_chapter_id": "chapters",
    },
    "character_timelines": {
        "character_id": "characters",
        "chapter_id": "chapters",
    },
    "character_change_logs": {
        "character_id": "characters",
        "chapter_id": "chapters",
    },
    "worldbuilding_versions": {
        "entry_id": "worldbuilding_entries",
        "source_chapter_id": "chapters",
    },
    "worldbuilding_timelines": {
        "entry_id": "worldbuilding_entries",
        "chapter_id": "chapters",
    },
    "foreshadowings": {
        "project_id": "project",
        "source_chapter_id": "chapters",
        "target_chapter_id": "chapters",
        "resolved_chapter_id": "chapters",
    },
    "causal_edges": {
        "project_id": "project",
        "source_chapter_id": "chapters",
        "resolved_chapter_id": "chapters",
    },
    "narrative_debts": {
        "project_id": "project",
        "source_chapter_id": "chapters",
        "target_chapter_id": "chapters",
        "resolved_chapter_id": "chapters",
        "linked_foreshadowing_id": "foreshadowings",
        "linked_causal_edge_id": "causal_edges",
    },
    "character_narrative_states": {
        "project_id": "project",
        "character_id": "characters",
        "chapter_id": "chapters",
    },
    "narrative_checkpoints": {
        "project_id": "project",
        "chapter_id": "chapters",
        "chapter_snapshot_id": "chapter_snapshots",
    },
    "chapter_governance_reviews": {
        "project_id": "project",
        "chapter_id": "chapters",
    },
    "creation_artifact_versions": {
        "session_id": "creation_sessions",
        "parent_version_id": "creation_artifact_versions",
        "restored_from_version_id": "creation_artifact_versions",
    },
    "creation_materials": {"session_id": "creation_sessions"},
}

REFERENCE_FIELDS = {
    "project_id",
    "source_project_id",
    "created_project_id",
    "parent_id",
    "outline_node_id",
    "source_chapter_id",
    "last_seen_chapter_id",
    "last_updated_chapter_id",
    "first_seen_chapter_id",
    "target_chapter_id",
    "resolved_chapter_id",
    "chapter_id",
    "saved_chapter_id",
    "chapter_snapshot_id",
    "character_id",
    "merged_character_id",
    "character_a_id",
    "character_b_id",
    "worldbuilding_entry_id",
    "entry_id",
    "source_entry_id",
    "target_entry_id",
    "linked_foreshadowing_id",
    "linked_causal_edge_id",
    "session_id",
    "parent_version_id",
    "restored_from_version_id",
}


def _map_nested(value: Any, identifier_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return identifier_map.get(value, value)
    if isinstance(value, list):
        return [_map_nested(item, identifier_map) for item in value]
    if isinstance(value, dict):
        return {key: _map_nested(item, identifier_map) for key, item in value.items()}
    return value


@dataclass
class ProjectPackageImportOutcome:
    result: dict[str, Any]
    moved_asset_directories: list[Path]
    replayed: bool = False

    def cleanup_after_failure(self) -> None:
        for path in reversed(self.moved_asset_directories):
            shutil.rmtree(path, ignore_errors=True)


class ProjectPackageImporter:
    """Restore a validated package into a new project."""

    def __init__(
        self,
        db: Session,
        package: ValidatedProjectPackage,
        *,
        idempotency_key: uuid.UUID,
        new_title: str | None = None,
    ):
        self.db = db
        self.package = package
        self.idempotency_key = str(idempotency_key)
        self.new_title = (new_title or "").strip()[:200] or None
        self.identifier_map: dict[str, str] = {}
        self.moved_asset_directories: list[Path] = []

    def cleanup_after_failure(self) -> None:
        for path in reversed(self.moved_asset_directories):
            shutil.rmtree(path, ignore_errors=True)

    def restore(self) -> ProjectPackageImportOutcome:
        replay = self._claim_receipt()
        if replay is not None:
            return ProjectPackageImportOutcome(replay, [], replayed=True)
        self._build_identifier_map()
        project = self._restore_project()
        self._restore_creation_session()
        self._restore_outline_nodes()
        self._insert_collection("chapters")
        self._restore_outline_chapter_references()
        for key in (
            "characters",
            "worldbuilding_entries",
            "character_ai_configs",
            "character_aliases",
            "character_relationships",
            "worldbuilding_relations",
            "outline_characters",
            "chapter_snapshots",
            "chapter_summaries",
            "chapter_characters",
            "chapter_worldbuilding",
            "chapter_drafts",
            "character_versions",
            "character_timelines",
            "character_change_logs",
            "worldbuilding_versions",
            "worldbuilding_timelines",
            "foreshadowings",
            "causal_edges",
            "narrative_debts",
            "character_narrative_states",
            "narrative_checkpoints",
            "chapter_governance_reviews",
            "creation_entities",
        ):
            self._insert_collection(key)
        self._restore_creation_versions()
        self._restore_materials()
        self.db.flush()
        index_result = reindex_project(self.db, project.id)
        counts = {key: len(value) for key, value in self.package.rows.items() if key != "project"}
        result = {
            "project_id": project.id,
            "project_title": project.title,
            "package_id": self.package.manifest["package_id"],
            "profile": self.package.profile,
            "counts": counts,
            "index": index_result,
            "replayed": False,
        }
        receipt = (
            self.db.query(ProjectPackageImportReceipt)
            .filter_by(idempotency_key=self.idempotency_key)
            .one()
        )
        receipt.status = "succeeded"
        receipt.project_id = project.id
        receipt.result_json = result
        receipt.updated_at = datetime.utcnow()
        self.db.flush()
        return ProjectPackageImportOutcome(result, self.moved_asset_directories)

    def _claim_receipt(self) -> dict[str, Any] | None:
        existing = (
            self.db.query(ProjectPackageImportReceipt)
            .filter_by(idempotency_key=self.idempotency_key)
            .first()
        )
        if existing is not None:
            return self._existing_receipt(existing)
        receipt = ProjectPackageImportReceipt(
            idempotency_key=self.idempotency_key,
            package_sha256=self.package.package_sha256,
            requested_title=self.new_title,
            package_id=self.package.manifest["package_id"],
            profile=self.package.profile,
            status="processing",
        )
        self.db.add(receipt)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            existing = (
                self.db.query(ProjectPackageImportReceipt)
                .filter_by(idempotency_key=self.idempotency_key)
                .first()
            )
            if existing is None:
                raise ProjectPackageError(
                    ERROR_CONFLICT,
                    "项目包导入请求发生并发冲突",
                    409,
                ) from exc
            return self._existing_receipt(existing)
        return None

    def _existing_receipt(self, receipt: ProjectPackageImportReceipt) -> dict[str, Any] | None:
        if (
            receipt.package_sha256 != self.package.package_sha256
            or (receipt.requested_title or None) != self.new_title
        ):
            raise ProjectPackageError(
                ERROR_CONFLICT,
                "该 Idempotency-Key 已用于不同的项目包或标题",
                409,
            )
        if receipt.status == "succeeded" and isinstance(receipt.result_json, dict):
            result = dict(receipt.result_json)
            result["replayed"] = True
            return result
        raise ProjectPackageError(ERROR_CONFLICT, "相同项目包导入请求仍在处理中，请稍后重试", 409)

    def _build_identifier_map(self) -> None:
        for key, collection in self.package.rows.items():
            for row in collection:
                source_id = row["id"]
                self.identifier_map[source_id] = str(
                    uuid.uuid5(
                        PACKAGE_ID_NAMESPACE,
                        f"{self.idempotency_key}:{key}:{source_id}",
                    )
                )

    def _mapped_row(self, spec: CollectionSpec, row: dict[str, Any]) -> dict[str, Any]:
        columns = {column.name: column for column in spec.model.__table__.columns}
        mapped: dict[str, Any] = {}
        for field in spec.fields:
            if field == "asset_path":
                continue
            value = row[field]
            if field == "id":
                value = self.identifier_map[value]
            elif field in REFERENCE_FIELDS and value is not None:
                value = self.identifier_map.get(str(value))
            elif columns[field].type.__class__.__name__ == "JSON":
                value = _map_nested(value, self.identifier_map)
            if value is not None and isinstance(columns[field].type, DateTime):
                value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if value.tzinfo is not None:
                    value = value.replace(tzinfo=None)
            mapped[field] = value
        return mapped

    def _restore_project(self) -> Project:
        spec = SPECS_BY_KEY["project"]
        data = self._mapped_row(spec, self.package.rows["project"][0])
        if self.new_title:
            data["title"] = self.new_title
        project = Project(
            **data, storage_mode="db_mirror", folder_path=None, content_migrated_at=None
        )
        self.db.add(project)
        self.db.flush()
        return project

    def _restore_creation_session(self) -> None:
        self._insert_collection("creation_sessions")

    def _restore_outline_nodes(self) -> None:
        rows = self.package.rows.get("outline_nodes", [])
        spec = SPECS_BY_KEY["outline_nodes"]
        remaining = {row["id"]: row for row in rows}
        inserted: set[str] = set()
        while remaining:
            progress = False
            for source_id, row in list(remaining.items()):
                parent = row.get("parent_id")
                if parent and parent not in inserted:
                    continue
                data = self._mapped_row(spec, row)
                data["source_chapter_id"] = None
                self.db.add(OutlineNode(**data))
                inserted.add(source_id)
                remaining.pop(source_id)
                progress = True
            if not progress:
                raise ProjectPackageError(ERROR_INVALID, "大纲父子关系包含循环或缺失引用")
        self.db.flush()

    def _restore_outline_chapter_references(self) -> None:
        if self.package.profile != "full":
            return
        for row in self.package.rows.get("outline_nodes", []):
            source_chapter_id = row.get("source_chapter_id")
            if not source_chapter_id:
                continue
            outline = self.db.get(OutlineNode, self.identifier_map[row["id"]])
            if outline is not None:
                outline.source_chapter_id = self.identifier_map.get(source_chapter_id)
        self.db.flush()

    def _insert_collection(self, key: str) -> None:
        rows = self.package.rows.get(key, [])
        if not rows:
            return
        spec = SPECS_BY_KEY[key]
        for row in rows:
            data = self._mapped_row(spec, row)
            if key == "chapter_drafts":
                data["saved_chapter_id"] = None
                data["status"] = "pending"
            self.db.add(spec.model(**data))
        self.db.flush()

    def _restore_creation_versions(self) -> None:
        rows = self.package.rows.get("creation_artifact_versions", [])
        if not rows:
            return
        spec = SPECS_BY_KEY["creation_artifact_versions"]
        remaining = {row["id"]: row for row in rows}
        inserted: set[str] = set()
        while remaining:
            progress = False
            for source_id, row in list(remaining.items()):
                dependencies = {
                    value
                    for value in (row.get("parent_version_id"), row.get("restored_from_version_id"))
                    if value
                }
                if not dependencies.issubset(inserted):
                    continue
                data = self._mapped_row(spec, row)
                self.db.add(NovelCreationArtifactVersion(**data, run_id=None, operation_id=None))
                inserted.add(source_id)
                remaining.pop(source_id)
                progress = True
            if not progress:
                raise ProjectPackageError(ERROR_INVALID, "立项版本关系包含循环或缺失引用")
        self.db.flush()

    def _restore_materials(self) -> None:
        rows = self.package.rows.get("creation_materials", [])
        if not rows:
            return
        spec = SPECS_BY_KEY["creation_materials"]
        for row in rows:
            data = self._mapped_row(spec, row)
            material_id = data["id"]
            session_id = data["session_id"]
            if not session_id:
                raise ProjectPackageError(ERROR_INVALID, "素材缺少有效立项会话")
            source = self.package.entry_path(row["asset_path"])
            destination_dir = content_root() / ".creation-imports" / session_id / material_id
            destination_dir.mkdir(parents=True, exist_ok=False)
            self.moved_asset_directories.append(destination_dir)
            destination = (
                destination_dir / f"original-{_safe_filename(row['filename'], 'material')}"
            )
            os.replace(source, destination)
            raw = destination.read_bytes()
            if hashlib.sha256(raw).hexdigest() != row["file_sha256"]:
                raise ProjectPackageError(ERROR_INVALID, f"素材恢复后哈希不一致：{row['filename']}")
            try:
                text, media_type = parse_creation_material(row["filename"], raw)
            except Exception as exc:
                raise ProjectPackageError(
                    ERROR_INVALID, f"无法重新读取原始素材：{row['filename']}"
                ) from exc
            chunks = split_creation_material(text)
            data.update(
                {
                    "stored_path": str(destination),
                    "media_type": media_type,
                    "operation_id": None,
                    "source_message_id": None,
                    "status": "completed",
                    "chunk_count": len(chunks),
                    "processed_chunks": len(chunks),
                    "checkpoint_json": {"phase": "restored", "next_chunk": len(chunks)},
                    "preview_json": None,
                    "result_json": None,
                    "error": None,
                }
            )
            material = NovelCreationMaterialImport(
                **data,
            )
            self.db.add(material)
            self.db.flush()
            for index, (start, end, chunk_text) in enumerate(chunks):
                chunk_id = str(
                    uuid.uuid5(
                        PACKAGE_ID_NAMESPACE,
                        f"{self.idempotency_key}:creation_material_chunk:{row['id']}:{index}",
                    )
                )
                self.db.add(
                    NovelCreationImportChunk(
                        id=chunk_id,
                        import_run_id=material_id,
                        chunk_index=index,
                        char_start=start,
                        char_end=end,
                        content_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                        text=chunk_text,
                        status="completed",
                        extraction_json={"method": "project_package_restore"},
                    )
                )
        self.db.flush()


__all__ = [
    "ERROR_ASSET",
    "ERROR_CONFLICT",
    "ERROR_INVALID",
    "ERROR_LIMIT",
    "ERROR_VERSION",
    "MAX_COMPRESSED_BYTES",
    "PACKAGE_EXTENSION",
    "PACKAGE_FORMAT",
    "PACKAGE_FORMAT_VERSION",
    "PACKAGE_ID_NAMESPACE",
    "PACKAGE_MEDIA_TYPE",
    "PackageProfile",
    "ProjectPackageError",
    "ProjectPackageExporter",
    "ProjectPackageImporter",
    "ProjectPackageValidator",
]
