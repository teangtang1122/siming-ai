"""Chapter CRUD, version, diff and restore HTTP interface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..architecture.uow import commit_session
from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.story.application.chapters import ChapterWorkspace
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.interfaces.chapter_dependencies import get_chapter_workspace
from ..modules.story.interfaces.dependencies import get_story_command
from ..schemas.chapter import (
    ChapterCatalogingRequest,
    ChapterCreate,
    ChapterDeAiPreviewRequest,
    ChapterQualityScoreRequest,
    ChapterReorderRequest,
    ChapterUpdate,
)
from ..services.cataloging.launcher import (
    CHAPTER_SAVE_SOURCE,
    cancel_superseded_chapter_cataloging_jobs,
    create_and_queue_cataloging_job,
)
from ..services.chapter_quality import preview_chapter_quality
from ..services.chapter_revision import preview_de_ai_revision
from ..services.workspace.generated_drafts import (
    chapter_draft_result_data,
    ensure_generated_draft_outline_is_unused,
    find_chapter_draft,
    latest_pending_chapter_draft,
    lock_chapter_draft_project,
    mark_chapter_draft_saved,
    update_chapter_draft,
)

router = APIRouter(tags=["chapters"])


def _draft_or_error(db: Session, project_id: str, draft_id: str):
    draft = find_chapter_draft(db, project_id, draft_id)
    if not draft:
        raise ValidationError("章节草稿不存在")
    return draft


def _save_message(data: dict) -> str:
    launch = data.get("cataloging_job")
    if isinstance(launch, dict):
        if launch.get("started"):
            return "章节已保存并启动建档"
        return "章节已保存，但建档启动失败"
    return "章节已保存，尚未建档"


def _start_chapter_cataloging(
    db: Session,
    project_id: str,
    data: dict,
    *,
    model: str | None = None,
) -> dict:
    if int(data.get("word_count") or 0) <= 0:
        return data
    try:
        _job, launch = create_and_queue_cataloging_job(
            db,
            project_id,
            [str(data.get("id") or data.get("chapter_id"))],
            execution_mode="auto",
            model_override=model,
            trigger_source=CHAPTER_SAVE_SOURCE,
            run_now=True,
        )
        data["cataloging_job"] = launch
    except Exception as exc:
        data["cataloging_job"] = {
            "started": False,
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


@router.get("/projects/{project_id}/chapter-drafts/pending")
def get_pending_chapter_draft(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
):
    """Restore the latest author-visible unsaved draft after a page reload."""
    draft = latest_pending_chapter_draft(db, project_id)
    data = chapter_draft_result_data(draft, db=db) if draft else None
    return ApiResponse.success(data=data)


@router.post("/projects/{project_id}/chapters")
async def create_chapter(
    project_id: str,
    payload: ChapterCreate,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    db: Annotated[Session, Depends(get_db)],
):
    values = payload.model_dump()
    lock_chapter_draft_project(db, project_id)
    draft_id = values.pop("draft_id", None)
    cataloging_mode = values.pop("cataloging_mode", "save_only")
    draft = None
    if draft_id:
        existing_draft = _draft_or_error(db, project_id, draft_id)
        if str(existing_draft.draft_kind or "new") != "new":
            raise ValidationError("修订候选不能新建为另一份章节；请在原章节中审阅并保存")
        if existing_draft.status == "saved" and existing_draft.saved_chapter_id:
            data = workspace.detail(project_id, existing_draft.saved_chapter_id)
            if cataloging_mode == "save_and_catalog" and data.get("cataloging_required"):
                data = _start_chapter_cataloging(db, project_id, data)
            return ApiResponse.success(data=data, message=_save_message(data))
        ensure_generated_draft_outline_is_unused(
            db,
            project_id,
            values.get("outline_node_id"),
            draft=existing_draft,
        )
        draft = update_chapter_draft(
            db,
            project_id,
            draft_id,
            title=values["title"],
            outline_node_id=values.get("outline_node_id"),
            content=values.get("content") or "",
        )
    result = workspace.create(project_id, values)
    command.queue_all(result.sync_intents)
    if draft is not None:
        mark_chapter_draft_saved(db, draft, str(result.data["id"]))
    command.finish()
    data = result.data
    if cataloging_mode == "save_and_catalog":
        data = _start_chapter_cataloging(db, project_id, data)
    return ApiResponse.success(data=data, message=_save_message(data))


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
    values = payload.model_dump(exclude_unset=True)
    cataloging_mode = values.pop("cataloging_mode", "save_only")
    lock_chapter_draft_project(db, project_id)
    draft_id = values.pop("draft_id", None)
    draft = None
    if draft_id:
        draft = _draft_or_error(db, project_id, draft_id)
        if str(draft.draft_kind or "new") != "revision":
            raise ValidationError("新章草稿只能创建章节，不能覆盖现有章节")
        if str(draft.target_chapter_id or "") != chapter_id:
            raise ValidationError("修订候选与当前章节不匹配，未写入任何正文")
        if draft.status == "saved" and draft.saved_chapter_id:
            data = workspace.detail(project_id, draft.saved_chapter_id)
            if cataloging_mode == "save_and_catalog" and data.get("cataloging_required"):
                data = _start_chapter_cataloging(db, project_id, data)
            return ApiResponse.success(data=data, message=_save_message(data))
        if draft.status != "pending":
            raise ValidationError("该修订候选已经失效，未写入任何正文")
        supplied_version = values.get("expected_version")
        base_version = int(draft.base_chapter_version or 0)
        if supplied_version is not None and int(supplied_version) != base_version:
            raise ValidationError("修订候选的基准版本与保存请求不一致")
        values["expected_version"] = base_version
        values["trigger_type"] = "ai_revision"
        update_chapter_draft(
            db,
            project_id,
            draft_id,
            title=str(values.get("title") or draft.title or ""),
            outline_node_id=values.get("outline_node_id", draft.outline_node_id),
            content=str(values.get("content") if "content" in values else draft.content or ""),
        )
    result = workspace.save(project_id, chapter_id, values)
    command.queue_all(result.sync_intents)
    if draft is not None:
        mark_chapter_draft_saved(db, draft, chapter_id)
    command.finish()
    data = result.data
    if data.get("narrative_content_changed"):
        cancel_superseded_chapter_cataloging_jobs(db, project_id, [chapter_id])
    if cataloging_mode == "save_and_catalog":
        data = _start_chapter_cataloging(db, project_id, data)
    return ApiResponse.success(data=data, message=_save_message(data))


@router.post("/projects/{project_id}/chapters/{chapter_id}/cataloging")
async def start_chapter_cataloging(
    project_id: str,
    chapter_id: str,
    payload: ChapterCatalogingRequest | None,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    db: Annotated[Session, Depends(get_db)],
):
    data = workspace.detail(project_id, chapter_id)
    data = _start_chapter_cataloging(
        db,
        project_id,
        data,
        model=payload.model if payload else None,
    )
    if not data.get("cataloging_job"):
        raise ValidationError("章节正文为空，无法启动建档")
    if not data["cataloging_job"].get("started"):
        raise ValidationError(
            str(data["cataloging_job"].get("error") or "章节建档启动失败")
        )
    return ApiResponse.success(data=data, message="章节建档已启动")


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


@router.post("/projects/{project_id}/chapter-drafts/{draft_id}/de-ai-preview")
async def draft_de_ai_preview(
    project_id: str,
    draft_id: str,
    payload: ChapterDeAiPreviewRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Run de-AI against the editor text without requiring an official chapter."""
    _draft_or_error(db, project_id, draft_id)
    data = await preview_de_ai_revision(
        db,
        project_id,
        None,
        content=payload.content,
        original_content=payload.original_content,
        revision_round=payload.revision_round,
        model=payload.model,
    )
    data["draft_id"] = draft_id
    return ApiResponse.success(data=data, message="草稿去除 AI 味候选已生成；尚未保存")


@router.post("/projects/{project_id}/chapter-drafts/{draft_id}/quality-score-preview")
async def draft_quality_score_preview(
    project_id: str,
    draft_id: str,
    payload: ChapterQualityScoreRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Score the current editor draft without creating chapter metadata."""
    _draft_or_error(db, project_id, draft_id)
    data = await preview_chapter_quality(
        db,
        project_id,
        None,
        content=payload.content,
        title=payload.title,
        model=payload.model,
    )
    data["draft_id"] = draft_id
    return ApiResponse.success(data=data, message="草稿质量评分已完成；草稿仍未保存")


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
    return ApiResponse.success(data=result.data, message="章节已恢复，尚未建档")
