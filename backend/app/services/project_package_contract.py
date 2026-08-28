"""Versioned schema and shared values for Siming project packages."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

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
    NovelCreationMaterialImport,
    NovelCreationSession,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    WorldbuildingEntry,
    WorldbuildingRelation,
    WorldbuildingTimeline,
    WorldbuildingVersion,
)

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
