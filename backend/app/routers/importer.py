"""File import — upload TXT/DOCX, parse, AI-split, and save as chapters."""
import asyncio  # kept for legacy patch paths in tests/integrations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from ..core.db_helpers import get_outline_node_or_404, get_project_or_404
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ..modules.story.interfaces.dependencies import get_story_command
from ..schemas.importer import ConfirmImportRequest, ImportSplitRequest
from ..modules.model_runtime.application.execution import model_executor as LLMGateway
from ..services import import_service as _import_service
from ..services.import_service import (
    build_split_preview,
    execute_import,
    parse_uploaded_file,
)

router = APIRouter(tags=["import"])


@router.post("/projects/{project_id}/import/file")
def import_file(project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a TXT or .docx file and return its parsed text content."""
    get_project_or_404(db, project_id)
    data = parse_uploaded_file(file)
    return ApiResponse.success(data=data, message=f"文件解析成功，共 {data['word_count']} 字符")


@router.post("/projects/{project_id}/import/preview")
async def import_preview(
    project_id: str,
    payload: ImportSplitRequest,
    db: Session = Depends(get_db),
):
    """Return regex-first chapter split suggestions, optionally LLM-corrected."""
    get_project_or_404(db, project_id)
    # Backward compatibility: older tests and integrations patch
    # app.routers.importer.LLMGateway. Keep the service module pointed at the
    # router attribute so that patch still affects the actual LLM call.
    _import_service.LLMGateway = LLMGateway
    _import_service.asyncio = asyncio
    splits, method, needs_review, failed_blocks = await build_split_preview(payload.text, payload.model)
    return ApiResponse.success(data={
        "splits": splits,
        "total": len(splits),
        "method": method,
        "needs_review": needs_review,
        "failed_blocks": failed_blocks,
    }, message=f"识别到 {len(splits)} 个章节边界")


@router.post("/projects/{project_id}/import/confirm")
def confirm_import(
    project_id: str,
    payload: ConfirmImportRequest,
    command: StoryCommandContext = Depends(get_story_command),
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
    return ApiResponse.success(data={"chapters": chapters, "total": len(chapters)}, message=f"成功导入 {len(chapters)} 章")
