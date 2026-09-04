"""Strict archive and schema validation for Siming project packages."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer

from .content_store import content_root
from .project_package_contract import (
    COLLECTION_SPECS,
    ERROR_INVALID,
    ERROR_LIMIT,
    ERROR_VERSION,
    MAX_COMPRESSED_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_DATA_ENTRY_BYTES,
    MAX_ENTRY_COUNT,
    MAX_MANIFEST_BYTES,
    MAX_MATERIAL_BYTES,
    MAX_UNCOMPRESSED_BYTES,
    PACKAGE_FORMAT,
    PACKAGE_FORMAT_VERSION,
    CollectionSpec,
    ProjectPackageError,
    ValidatedProjectPackage,
    _sha256_path,
)

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
                    if (
                        key == "narrative_checkpoints"
                        and field in {"chapter_id", "chapter_snapshot_id"}
                    ):
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
