"""Authoritative Siming project-package export and import services.

The package is an author-data interchange format, not a generic database dump.
Its strict schema and archive validator live in focused sibling modules, while
this module remains the public service API used by routers and tests.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
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
from .project_package_contract import (
    COLLECTION_SPECS,
    ERROR_ASSET,
    ERROR_CONFLICT,
    ERROR_INVALID,
    ERROR_LIMIT,
    ERROR_VERSION,
    MAX_COMPRESSED_BYTES,
    MAX_MATERIAL_BYTES,
    PACKAGE_EXTENSION,
    PACKAGE_FORMAT,
    PACKAGE_FORMAT_VERSION,
    PACKAGE_ID_NAMESPACE,
    PACKAGE_MEDIA_TYPE,
    SPECS_BY_KEY,
    CollectionSpec,
    ExportedProjectPackage,
    PackageProfile,
    ProjectPackageError,
    ValidatedProjectPackage,
    _clear_structure_references,
    _json_bytes,
    _safe_filename,
    _serialize_row,
    _sha256_path,
)
from .project_package_validation import ProjectPackageValidator
from .rag.indexer import reindex_project


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
        if self.profile == "full":
            rows.update(self._collect_full(creation_ids, character_ids, world_ids))
        return rows

    def _collect_full(
        self,
        creation_ids: list[str],
        character_ids: list[str],
        world_ids: list[str],
    ) -> dict[str, list[Any]]:
        chapters = (
            self.db.query(Chapter)
            .filter(Chapter.project_id == self.project_id)
            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc())
            .all()
        )
        chapter_ids = [row.id for row in chapters]
        return {
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
    "COLLECTION_SPECS",
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
    "ExportedProjectPackage",
    "ProjectPackageError",
    "ProjectPackageExporter",
    "ProjectPackageImportOutcome",
    "ProjectPackageImporter",
    "ProjectPackageValidator",
    "ValidatedProjectPackage",
]
