"""External novel import — TXT/Markdown/DOCX to projects and chapters."""

import asyncio  # kept for legacy patch paths in tests/integrations
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from ..core.db_helpers import get_outline_node_or_404, get_project_or_404
from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.gateway.infrastructure.service import GatewayService
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.application.projects import ProjectWorkspace
from ..modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ..modules.story.interfaces.dependencies import get_story_command
from ..modules.story.interfaces.project_dependencies import get_project_workspace
from ..schemas.importer import ConfirmImportRequest, ImportSplitRequest
from ..schemas.project import ProjectCreate
from ..services import import_service as _import_service
from ..services.import_service import (
    MAX_IMPORT_CHAPTERS,
    build_split_preview,
    execute_import,
    parse_uploaded_file,
)

router = APIRouter(tags=["import"])


def _is_paired_android(request: Request) -> bool:
    return bool(
        getattr(request.state, "gateway_device_id", None)
        and getattr(request.state, "gateway_device_platform", None) == "android"
    )


@router.post("/import/project-file")
async def import_project_file_as_project(
    request: Request,
    workspace: Annotated[ProjectWorkspace, Depends(get_project_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File()],
):
    """Decode, split and persist a complete novel in one transaction."""

    parsed = parse_uploaded_file(file)
    title = Path(parsed["filename"]).stem.strip() or "导入作品"
    project_change = workspace.create(
        ProjectCreate(
            title=title[:200],
            description="由手机或桌面批量导入的已有小说",
        ).model_dump()
    )
    project_id = str(project_change.data["id"])
    splits, method, needs_review, failed_blocks = await build_split_preview(
        parsed["text"],
        None,
    )
    if len(splits) > MAX_IMPORT_CHAPTERS:
        command.rollback()
        raise ValidationError(
            f"识别到 {len(splits)} 章，超过 {MAX_IMPORT_CHAPTERS} 章安全上限；"
            "请检查章节标题格式后重试"
        )

    chapters = execute_import(db, project_id, parsed["text"], splits, None)
    intents = [
        *project_change.sync_intents,
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.PROJECT,
            source="batch_import",
        ),
    ]
    seen_intents: set[str] = set()
    for intent in intents:
        if intent.dedupe_key in seen_intents:
            continue
        seen_intents.add(intent.dedupe_key)
        command.queue(intent)
    command.finish()

    project_data = dict(project_change.data)
    if _is_paired_android(request):
        GatewayService(db).enable_project(project_id)
        project_data["folder_path"] = None

    return ApiResponse.success(
        data={
            "project_id": project_id,
            "project": project_data,
            "filename": parsed["filename"],
            "format": parsed["format"],
            "encoding": parsed["encoding"],
            "word_count": parsed["word_count"],
            "chapters": chapters,
            "total": len(chapters),
            "method": method,
            "needs_review": needs_review,
            "failed_blocks": failed_blocks,
        },
        message=f"已一次性创建作品并导入 {len(chapters)} 章",
    )


@router.post("/projects/{project_id}/import/file")
def import_file(
    project_id: str,
    file: Annotated[UploadFile, File()],
    db: Annotated[Session, Depends(get_db)],
):
    """Upload a TXT, Markdown, or DOCX file and return its parsed text content."""
    get_project_or_404(db, project_id)
    data = parse_uploaded_file(file)
    return ApiResponse.success(data=data, message=f"文件解析成功，共 {data['word_count']} 字符")


@router.post("/projects/{project_id}/import/preview")
async def import_preview(
    project_id: str,
    payload: ImportSplitRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Return regex-first chapter split suggestions, optionally LLM-corrected."""
    get_project_or_404(db, project_id)
    # Backward compatibility: older tests and integrations patch
    # app.routers.importer.LLMGateway. Keep the service module pointed at the
    # router attribute so that patch still affects the actual LLM call.
    _import_service.LLMGateway = LLMGateway
    _import_service.asyncio = asyncio
    splits, method, needs_review, failed_blocks = await build_split_preview(
        payload.text, payload.model
    )
    return ApiResponse.success(
        data={
            "splits": splits,
            "total": len(splits),
            "method": method,
            "needs_review": needs_review,
            "failed_blocks": failed_blocks,
        },
        message=f"识别到 {len(splits)} 个章节边界",
    )


@router.post("/projects/{project_id}/import/confirm")
def confirm_import(
    project_id: str,
    payload: ConfirmImportRequest,
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    """Save imported text as chapters based on split suggestions."""
    db = command.session
    get_project_or_404(db, project_id)
    get_outline_node_or_404(db, project_id, payload.outline_node_id)
    chapters = execute_import(db, project_id, payload.text, payload.splits, payload.outline_node_id)
    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.PROJECT,
            source="import",
        ),
    )
    command.finish()
    return ApiResponse.success(
        data={"chapters": chapters, "total": len(chapters)}, message=f"成功导入 {len(chapters)} 章"
    )
