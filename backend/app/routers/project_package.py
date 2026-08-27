"""Project package import / export.

Exports an approved book project (立项的书) — settings, creation brief,
outline, characters, worldbuilding and (optionally) chapter text — as a
portable ZIP archive.  Importing such an archive creates a brand-new project
with fresh IDs so the imported book is exactly like the exported one.

These endpoints wrap the generic ``ProjectBackupBuilder`` /
``ProjectBackupRestorer`` services with a clear "project package" contract.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.interfaces.dependencies import get_story_command
from ..services.project_backup_service import ProjectBackupBuilder, ProjectBackupRestorer

router = APIRouter(tags=["project-package"])

# Chapter-text entries are stripped from a "structure-only" package so authors
# can share the approved book (outline / settings / creation brief) without
# leaking the manuscript body.
_CHAPTER_ENTRIES = frozenset(
    {
        "chapters.json",
        "chapter_snapshots.json",
        "chapter_summaries.json",
        "chapter_characters.json",
        "chapter_worldbuilding.json",
        "chapter_quality_metrics.json",
        "chapter_governance_reviews.json",
        "chapter_drafts.json",
    }
)


def _safe_filename(title: str) -> str:
    cleaned = "".join(ch for ch in title if ch not in '/\\:*?"<>|').strip()
    return (cleaned or "project")[:80]


def _strip_chapter_entries(buf: io.BytesIO) -> io.BytesIO:
    """Return a copy of the archive without the chapter-text entries."""
    buf.seek(0)
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(buf, "r") as src:
        for name in src.namelist():
            if name in _CHAPTER_ENTRIES:
                continue
            entries[name] = src.read(name)

    # Keep the manifest metadata coherent with the actual package content.
    manifest_raw = entries.get("manifest.json")
    if manifest_raw is not None:
        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
            manifest.setdefault("content", {})["chapters"] = 0
            entries["manifest.json"] = json.dumps(
                manifest, ensure_ascii=False
            ).encode("utf-8")
        except (ValueError, TypeError):
            pass

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name, data in entries.items():
            dst.writestr(name, data)
    out.seek(0)
    return out


@router.post("/projects/{project_id}/project-package")
def export_project_package(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    include_chapters: bool = Query(
        True,
        description="true 导出完整项目（含正文）；false 只导出大纲、设定与立项资料",
    ),
):
    """Export an approved book project as a portable ZIP archive."""
    project = get_project_or_404(db, project_id)
    buf = ProjectBackupBuilder(db, project_id).build_archive()
    if not include_chapters:
        buf = _strip_chapter_entries(buf)
    filename = (
        f"{_safe_filename(project.title)}_项目导出_"
        f"{datetime.now().strftime('%Y%m%d')}.zip"
    )
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"export_{project.id[:8]}.zip\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.post("/projects/project-package/import")
def import_project_package(
    db: Annotated[Session, Depends(get_db)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    file: Annotated[UploadFile, File()],
    new_title: Annotated[str | None, Form()] = None,
):
    """Import a project package and create a brand-new project from it."""
    archive_bytes = file.file.read()
    if not archive_bytes:
        raise ValidationError("上传的项目包为空")

    new_title_value = new_title.strip() if new_title else None
    try:
        result = ProjectBackupRestorer(
            db,
            archive_bytes,
            new_title=new_title_value,
        ).restore()
    except Exception as exc:
        db.rollback()
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"项目导入失败: {exc}") from exc

    command.finish()
    return ApiResponse.success(
        data=result,
        message=f"项目导入成功：已创建作品「{result.get('project_title')}」",
    )


__all__ = ["router"]
