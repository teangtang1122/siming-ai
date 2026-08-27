"""Full project backup/restore — package every project row into a ZIP archive."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile, Body
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.interfaces.dependencies import get_story_command
from ..services.project_backup_service import (
    ProjectBackupBuilder,
    ProjectBackupRestorer,
)

router = APIRouter(tags=["project-backup"])

BACKUP_STORE_ROOT = Path(__file__).resolve().parents[3] / "artifacts" / "backups"


class BackupRestoreRequest(BaseModel):
    """Options accepted when restoring a project backup."""

    preserve_ids: bool = Field(
        False,
        description="True to keep original entity IDs from the archive. "
        "Only safe when the archive was exported from this exact database.",
    )
    new_title: Optional[str] = Field(
        None,
        max_length=200,
        description="Optional new title used instead of the archived title.",
    )


def _safe_filename(title: str) -> str:
    return title.replace("/", "_").replace("\\", "_").replace(":", "_")[:80]


def _store_backup_file(
    project_id: str,
    filename: str,
    data: bytes,
) -> dict:
    """Persist a backup archive under artifacts/backups/{project_id}/."""
    file_id = str(UUID(hex="0" * 8 + "0" * 4 + "0" * 4 + "0" * 4 + "0" * 12))
    # simpler: regenerate with full uuid
    import uuid

    file_id = str(uuid.uuid4())
    project_dir = BACKUP_STORE_ROOT / project_id
    project_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{file_id}.zip"
    (project_dir / stored_filename).write_bytes(data)

    metadata = {
        "file_id": file_id,
        "project_id": project_id,
        "filename": filename,
        "stored_filename": stored_filename,
        "format": "zip",
        "media_type": "application/zip",
        "size": len(data),
        "download_url": f"/api/v1/projects/{project_id}/backup/download/{file_id}",
    }
    (project_dir / f"{file_id}.json").write_text(
        __import__("json").dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )
    return metadata


def _load_backup_metadata(project_id: str, file_id: str) -> dict:
    try:
        UUID(file_id)
    except ValueError:
        raise NotFoundError("备份文件不存在")

    metadata_path = BACKUP_STORE_ROOT / project_id / f"{file_id}.json"
    if not metadata_path.exists():
        raise NotFoundError("备份文件不存在")
    return __import__("json").loads(metadata_path.read_text(encoding="utf-8"))


# ── export ────────────────────────────────────────────────────────────────────


@router.post("/projects/{project_id}/backup")
def export_project_backup(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
):
    """Package the entire project (settings, text, outline, characters,
    worldbuilding, governance, summaries, snapshots) into a ZIP archive."""
    import json as json_module

    project = get_project_or_404(db, project_id)
    buf = ProjectBackupBuilder(db, project_id).build_archive()
    safe_title = _safe_filename(project.title)
    filename = f"{safe_title}_backup_{json_module.dumps(json_module.loads('{}')).__class__.__name__}"

    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_title}_backup_{timestamp}.zip"

    metadata = _store_backup_file(project_id, filename, buf.getvalue())
    return ApiResponse.success(data=metadata, message="完整作品备份已生成")


@router.get("/projects/{project_id}/backup/download/{file_id}")
def download_project_backup(project_id: str, file_id: str):
    """Download a previously generated full project backup ZIP."""
    metadata = _load_backup_metadata(project_id, file_id)
    file_path = BACKUP_STORE_ROOT / project_id / metadata["stored_filename"]
    if not file_path.exists():
        raise NotFoundError("备份文件不存在")
    return FileResponse(
        file_path,
        media_type="application/zip",
        filename=metadata["filename"],
    )


@router.get("/projects/{project_id}/backup/list")
def list_project_backups(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
):
    """List all generated backup archives for a project."""
    get_project_or_404(db, project_id)
    project_dir = BACKUP_STORE_ROOT / project_id
    if not project_dir.exists():
        return ApiResponse.success(data={"items": [], "total": 0})

    items = []
    for json_path in sorted(project_dir.glob("*.json")):
        try:
            items.append(
                __import__("json").loads(json_path.read_text(encoding="utf-8"))
            )
        except (json.JSONDecodeError, OSError):
            continue
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return ApiResponse.success(data={"items": items, "total": len(items)})


# ── import ────────────────────────────────────────────────────────────────────


@router.post("/projects/{project_id}/backup/restore")
def restore_project_backup_route(
    project_id: str,
    payload: Optional[BackupRestoreRequest] = Body(None),
    db: Annotated[Session, Depends(get_db)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    file: UploadFile = File(...),
):
    """Restore a project from a ZIP backup archive.

    Upload the archive as ``file`` multipart field. The archive must be a
    Siming project backup (as produced by POST /projects/{project_id}/backup).
    """
    import json as json_module

    # Validate project exists
    get_project_or_404(db, project_id)

    archive_bytes = file.file.read()
    if not archive_bytes:
        raise ValidationError("上传的备份文件为空")

    preserve_ids = bool(payload.preserve_ids if payload else False)
    new_title = payload.new_title if payload else None

    if new_title:
        new_title = new_title.strip()

    try:
        result = ProjectBackupRestorer(
            db,
            archive_bytes,
            preserve_ids=preserve_ids,
            new_title=new_title,
        ).restore()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"备份恢复失败: {exc}") from exc

    command.finish()

    # The restored archive creates a *new* project — return it with the
    # restored data summary.
    return ApiResponse.success(
        data=result,
        message=(
            f"备份恢复成功：创建作品「{result.get('project_title')}」，"
            f"共恢复 {sum((result.get('counts') or {}).values())} 条数据"
        ),
    )


@router.post("/backup/restore/new")
def restore_backup_as_new_project(
    payload: Optional[BackupRestoreRequest] = Body(None),
    db: Annotated[Session, Depends(get_db)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    file: UploadFile = File(...),
):
    """Restore a project backup ZIP as a brand-new project.

    This endpoint does not require an existing project ID — it reads the
    archive and creates a new project entry from its project.json.
    """
    archive_bytes = file.file.read()
    if not archive_bytes:
        raise ValidationError("上传的备份文件为空")

    preserve_ids = bool(payload.preserve_ids if payload else False)
    new_title = payload.new_title if payload else None
    if new_title:
        new_title = new_title.strip()

    try:
        result = ProjectBackupRestorer(
            db,
            archive_bytes,
            preserve_ids=preserve_ids,
            new_title=new_title,
        ).restore()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"备份恢复失败: {exc}") from exc

    command.finish()

    return ApiResponse.success(
        data=result,
        message=f"备份恢复成功：创建作品「{result.get('project_title')}」",
    )


__all__ = ["router"]