"""Export chapters, outline, characters, and worldbuilding to TXT/Word/PDF formats."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..services.export_service import (
    EXPORT_ROOT,
    _generate_docx,
    _generate_export_content,
    _generate_pdf,
    _load_export_metadata,
    _ordered_chapters,
    _store_export_file,
)

router = APIRouter(tags=["export"])


class ExportRequest(BaseModel):
    """Export options accepted by POST /export."""

    scope: str = Field("all", description="chapters/outline/characters/worldbuilding/all/single/selected")
    format: str = Field("txt", description="txt/docx/pdf")
    chapter_ids: list[str] = Field(default_factory=list)
    include_outline: bool = False
    include_characters: bool = False
    include_worldbuilding: bool = False


@router.post("/projects/{project_id}/export")
def export_project(
    project_id: str,
    scope: str = Query("all", description="Export scope: chapters/outline/characters/worldbuilding/all"),
    format: str = Query("txt", description="Export format: txt/docx/pdf"),
    payload: Optional[ExportRequest] = Body(None),
    db: Session = Depends(get_db),
):
    """Export project content in the specified format and scope."""
    requested_scope = payload.scope if payload else scope
    requested_format = payload.format if payload else format
    chapter_ids = payload.chapter_ids if payload else []
    include_outline = payload.include_outline if payload else False
    include_characters = payload.include_characters if payload else False
    include_worldbuilding = payload.include_worldbuilding if payload else False

    valid_scopes = {"chapters", "outline", "characters", "worldbuilding", "all", "single", "selected"}
    if requested_scope not in valid_scopes:
        raise ValidationError(f"无效的导出范围: {requested_scope}，支持: {', '.join(sorted(valid_scopes))}")

    valid_formats = {"txt", "docx", "pdf"}
    if requested_format not in valid_formats:
        raise ValidationError(f"无效的导出格式: {requested_format}，支持: {', '.join(sorted(valid_formats))}")

    if requested_format == "pdf":
        filename, buf = _generate_pdf(
            db,
            project_id,
            requested_scope,
            chapter_ids=chapter_ids,
            include_outline=include_outline,
            include_characters=include_characters,
            include_worldbuilding=include_worldbuilding,
        )
        metadata = _store_export_file(project_id, filename, buf.getvalue(), "application/pdf", requested_format)
    elif requested_format == "docx":
        filename, buf = _generate_docx(
            db,
            project_id,
            requested_scope,
            chapter_ids=chapter_ids,
            include_outline=include_outline,
            include_characters=include_characters,
            include_worldbuilding=include_worldbuilding,
        )
        metadata = _store_export_file(
            project_id,
            filename,
            buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            requested_format,
        )
    else:
        filename, content = _generate_export_content(
            db,
            project_id,
            requested_scope,
            chapter_ids=chapter_ids,
            include_outline=include_outline,
            include_characters=include_characters,
            include_worldbuilding=include_worldbuilding,
        )
        metadata = _store_export_file(
            project_id,
            filename,
            content.encode("utf-8"),
            "text/plain; charset=utf-8",
            requested_format,
        )

    return ApiResponse.success(data=metadata, message="导出文件已生成")


@router.get("/projects/{project_id}/export/download/{file_id}")
def download_export(project_id: str, file_id: str):
    """Download a generated export file by file_id."""
    metadata = _load_export_metadata(project_id, file_id)
    file_path = EXPORT_ROOT / project_id / metadata["stored_filename"]
    if not file_path.exists():
        raise NotFoundError("导出文件不存在")
    return FileResponse(
        file_path,
        media_type=metadata["media_type"],
        filename=metadata["filename"],
    )


@router.get("/projects/{project_id}/export/word-count")
def export_word_count_report(project_id: str, db: Session = Depends(get_db)):
    """Get a word count summary for all chapters."""
    get_project_or_404(db, project_id)
    chapters = _ordered_chapters(db, project_id)
    items = [
        {
            "id": c.id,
            "title": c.title,
            "word_count": c.word_count or 0,
            "version": c.current_version or 1,
        }
        for c in chapters
    ]
    total_words = sum(item["word_count"] for item in items)
    return ApiResponse.success(data={
        "chapters": items,
        "total_chapters": len(items),
        "total_words": total_words,
    })
