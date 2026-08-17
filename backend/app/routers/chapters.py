"""Chapter CRUD, version, diff and restore HTTP interface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..architecture.uow import commit_session
from ..database.session import get_db
from ..modules.story.application.chapters import ChapterWorkspace
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.interfaces.chapter_dependencies import get_chapter_workspace
from ..modules.story.interfaces.dependencies import get_story_command
from ..schemas.chapter import (
    ChapterCreate,
    ChapterDeAiPreviewRequest,
    ChapterQualityScoreRequest,
    ChapterReorderRequest,
    ChapterUpdate,
)
from ..services.chapter_quality import preview_chapter_quality
from ..services.chapter_revision import preview_de_ai_revision
from ..services.cataloging.launcher import (
    AUTO_CHAPTER_WRITE_SOURCE,
    create_and_queue_cataloging_job,
)

router = APIRouter(tags=["chapters"])


def _start_chapter_cataloging(
    db: Session,
    project_id: str,
    data: dict,
    *,
    should_start: bool = True,
) -> dict:
    if not should_start or int(data.get("word_count") or 0) <= 0:
        return data
    try:
        _job, launch = create_and_queue_cataloging_job(
            db,
            project_id,
            [str(data.get("id") or data.get("chapter_id"))],
            execution_mode="auto",
            trigger_source=AUTO_CHAPTER_WRITE_SOURCE,
            run_now=True,
        )
        data["cataloging_job"] = launch
    except Exception as exc:
        data["cataloging_job"] = {
            "auto_started": False,
            "status": "failed_to_start",
            "error": str(exc)[:2000],
        }
    return data


@router.get("/projects/{project_id}/chapters")
def list_chapters(
    project_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
):
    return ApiResponse.success(data=workspace.list(project_id))


@router.post("/projects/{project_id}/chapters")
async def create_chapter(
    project_id: str,
    payload: ChapterCreate,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    db: Annotated[Session, Depends(get_db)],
):
    result = workspace.create(project_id, payload.model_dump())
    command.queue_all(result.sync_intents)
    command.finish()
    data = _start_chapter_cataloging(db, project_id, result.data)
    return ApiResponse.success(data=data, message="章节已创建，正式建档任务已启动")


@router.put("/projects/{project_id}/chapters/reorder")
def reorder_chapters(
    project_id: str,
    payload: ChapterReorderRequest,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.reorder(project_id, payload.ids)
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="章节顺序已更新")


@router.get("/projects/{project_id}/chapters/{chapter_id}")
def get_chapter_detail(
    project_id: str,
    chapter_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
):
    return ApiResponse.success(data=workspace.detail(project_id, chapter_id))


@router.put("/projects/{project_id}/chapters/{chapter_id}")
async def save_chapter(
    project_id: str,
    chapter_id: str,
    payload: ChapterUpdate,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    db: Annotated[Session, Depends(get_db)],
):
    result = workspace.save(
        project_id, chapter_id, payload.model_dump(exclude_unset=True)
    )
    command.queue_all(result.sync_intents)
    command.finish()
    data = _start_chapter_cataloging(
        db,
        project_id,
        result.data,
        should_start=bool(result.data.get("narrative_content_changed")),
    )
    message = "章节已保存，正式建档任务已启动" if data.get("cataloging_job") else "章节已保存"
    return ApiResponse.success(data=data, message=message)


@router.post("/projects/{project_id}/chapters/{chapter_id}/de-ai-preview")
async def de_ai_preview(
    project_id: str,
    chapter_id: str,
    payload: ChapterDeAiPreviewRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Generate a reviewable candidate; never write it to the chapter automatically."""
    data = await preview_de_ai_revision(
        db,
        project_id,
        chapter_id,
        content=payload.content,
        original_content=payload.original_content,
        revision_round=payload.revision_round,
        model=payload.model,
    )
    warning_count = len(data.get("warnings") or [])
    message = (
        f"去除 AI 味候选稿已生成，有 {warning_count} 项审核提醒；"
        "原文未变，请对照后自行决定是否应用"
        if warning_count
        else "去除 AI 味候选稿已生成；原文未变，请对照后自行决定是否应用"
    )
    return ApiResponse.success(data=data, message=message)


@router.post("/projects/{project_id}/chapters/{chapter_id}/quality-score-preview")
async def quality_score_preview(
    project_id: str,
    chapter_id: str,
    payload: ChapterQualityScoreRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Return a manual quality review without changing chapter data."""
    data = await preview_chapter_quality(
        db,
        project_id,
        chapter_id,
        content=payload.content,
        title=payload.title,
        model=payload.model,
    )
    commit_session(db)
    return ApiResponse.success(data=data, message="章节质量评分已完成并写入质量曲线")


@router.delete("/projects/{project_id}/chapters/{chapter_id}")
def delete_chapter(
    project_id: str,
    chapter_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.delete(project_id, chapter_id)
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(message="章节已删除")


@router.get("/projects/{project_id}/chapters/{chapter_id}/snapshots")
def list_chapter_snapshots(
    project_id: str,
    chapter_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
):
    return ApiResponse.success(data=workspace.snapshots(project_id, chapter_id))


@router.get("/projects/{project_id}/chapters/{chapter_id}/snapshots/diff")
def diff_chapter_snapshots(
    project_id: str,
    chapter_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    from_snapshot_id: str = Query(..., description="Base snapshot ID"),
    to_snapshot_id: str = Query(..., description="Target snapshot ID"),
):
    return ApiResponse.success(
        data=workspace.diff(
            project_id, chapter_id, from_snapshot_id, to_snapshot_id
        )
    )


@router.get("/projects/{project_id}/chapters/{chapter_id}/snapshots/{snapshot_id}")
def get_chapter_snapshot_detail(
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
):
    return ApiResponse.success(
        data=workspace.snapshot(project_id, chapter_id, snapshot_id)
    )


@router.post("/projects/{project_id}/chapters/{chapter_id}/restore/{snapshot_id}")
async def restore_chapter_snapshot(
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    db: Annotated[Session, Depends(get_db)],
):
    result = workspace.restore(project_id, chapter_id, snapshot_id)
    command.queue_all(result.sync_intents)
    command.finish()
    data = _start_chapter_cataloging(db, project_id, result.data)
    return ApiResponse.success(data=data, message="章节已恢复，正式建档任务已启动")
