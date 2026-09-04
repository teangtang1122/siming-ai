"""Chapter CRUD, version, diff and restore HTTP interface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from ..architecture.uow import commit_session
from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.story.application.chapters import ChapterWorkspace
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ..modules.story.interfaces.chapter_dependencies import get_chapter_workspace
from ..modules.story.interfaces.dependencies import get_story_command
from ..schemas.chapter import (
    ChapterCatalogingRequest,
    ChapterCreate,
    ChapterDeAiPreviewRequest,
    ChapterDraftUpdate,
    ChapterQualityScoreRequest,
    ChapterReorderRequest,
    ChapterSummaryUpdate,
    ChapterUpdate,
)
from ..services.cataloging.chapter_rollback import cataloging_required_suffix_ids
from ..services.cataloging.launcher import (
    CHAPTER_SAVE_SOURCE,
    create_and_queue_cataloging_job,
)
from ..services.chapter_quality import preview_chapter_quality
from ..services.chapter_revision import preview_de_ai_revision
from ..services.chapter_summary_service import update_author_chapter_summary
from ..services.workspace.generated_drafts import (
    chapter_draft_result_data,
    discard_chapter_draft,
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


def _bind_draft_manifest(values: dict, draft) -> None:
    """Use server-owned draft provenance when promoting reviewed content."""
    supplied = str(values.get("context_manifest_id") or "").strip()
    authoritative = str(draft.context_manifest_id or "").strip()
    if supplied and supplied != authoritative:
        raise ValidationError("章节草稿的生成上下文与保存请求不一致")
    values["context_manifest_id"] = authoritative or None


def _save_message(data: dict) -> str:
    if (
        data.get("cataloging_impact") == "style_only"
        and data.get("chapter_text_changed")
    ):
        return "章节已保存；本次标记为仅润色，原建档状态已保留"
    launch = data.get("cataloging_job")
    if isinstance(launch, dict):
        count = len(data.get("recatalog_required_chapter_ids") or [])
        if launch.get("idempotent_reuse"):
            if launch.get("next_action") == "already_cataloged":
                return "章节已保存，当前版本已完成建档"
            if count > 1:
                return (
                    "章节已保存，当前版本正在建档；"
                    f"后续 {count - 1} 章仍需在当前任务完成后重新建档"
                )
            return "章节已保存，当前版本正在建档"
        if launch.get("started"):
            return (
                f"章节已保存，并已启动当前章及后续 {max(count - 1, 0)} 章重新建档"
                if count > 1
                else "章节已保存并启动建档"
            )
        return "章节已保存，但建档启动失败"
    count = len(data.get("recatalog_required_chapter_ids") or [])
    if count > 1:
        return f"章节已保存；当前章及后续 {count - 1} 章需要重新建档"
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
    if data.get("cataloging_impact") == "style_only" and not data.get(
        "cataloging_required"
    ):
        return data
    requested = data.get("recatalog_required_chapter_ids")
    chapter_ids = [
        str(item)
        for item in (
            requested
            if isinstance(requested, list) and requested
            else [data.get("id") or data.get("chapter_id")]
        )
        if str(item or "").strip()
    ]
    chapter_ids = list(dict.fromkeys(chapter_ids))
    if not chapter_ids:
        return data
    try:
        _job, launch = create_and_queue_cataloging_job(
            db,
            project_id,
            chapter_ids,
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


@router.put("/projects/{project_id}/chapter-drafts/{draft_id}")
def update_pending_chapter_draft(
    project_id: str,
    draft_id: str,
    payload: ChapterDraftUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
):
    """Synchronize editor text without promoting it to a formal chapter."""

    lock_chapter_draft_project(db, project_id)
    if payload.outline_node_id and not workspace.chapter_outline_exists(
        project_id, payload.outline_node_id
    ):
        raise ValidationError("章节草稿只能绑定当前作品中的章级大纲节点")
    draft = update_chapter_draft(
        db,
        project_id,
        draft_id,
        title=payload.title,
        outline_node_id=payload.outline_node_id,
        content=payload.content,
    )
    commit_session(db)
    return ApiResponse.success(
        data=chapter_draft_result_data(draft, db=db),
        message="未保存章节草稿已同步",
    )


@router.delete("/projects/{project_id}/chapter-drafts/{draft_id}")
def discard_generated_chapter_draft(
    project_id: str,
    draft_id: str,
    db: Annotated[Session, Depends(get_db)],
):
    draft = discard_chapter_draft(db, project_id, draft_id)
    return ApiResponse.success(
        data=chapter_draft_result_data(draft, db=db),
        message="章节草稿已丢弃；正式正文未改变",
    )


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
            if (
                cataloging_mode == "save_and_catalog"
                and data.get("cataloging_required")
            ):
                data = _start_chapter_cataloging(db, project_id, data)
            return ApiResponse.success(data=data, message=_save_message(data))
        ensure_generated_draft_outline_is_unused(
            db,
            project_id,
            values.get("outline_node_id"),
            draft=existing_draft,
        )
        _bind_draft_manifest(values, existing_draft)
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


@router.put("/projects/{project_id}/chapters/{chapter_id}/summary")
def update_chapter_summary(
    project_id: str,
    chapter_id: str,
    payload: ChapterSummaryUpdate,
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    db: Annotated[Session, Depends(get_db)],
):
    """Correct catalog metadata without creating a new body version or re-cataloging."""

    try:
        data = update_author_chapter_summary(
            db,
            project_id,
            chapter_id,
            expected_version=payload.expected_version,
            summary_text=payload.summary_text,
            key_events=payload.key_events,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.CHAPTER,
            entity_id=chapter_id,
            source="author_summary_update",
        )
    )
    command.finish()
    return ApiResponse.success(
        data=data,
        message="章节摘要已按作者复核更新",
    )


@router.put("/projects/{project_id}/chapters/{chapter_id}")
async def save_chapter(
    project_id: str,
    chapter_id: str,
    payload: ChapterUpdate,
    request: Request,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
    db: Annotated[Session, Depends(get_db)],
):
    values = payload.model_dump(exclude_unset=True)
    cataloging_impact = str(
        request.headers.get("X-Siming-Cataloging-Impact") or "semantic"
    ).strip().lower()
    if cataloging_impact not in {"semantic", "style_only"}:
        raise ValidationError("X-Siming-Cataloging-Impact 必须是 semantic 或 style_only")
    values["cataloging_impact"] = cataloging_impact
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
            if (
                cataloging_mode == "save_and_catalog"
                and data.get("cataloging_required")
            ):
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
        _bind_draft_manifest(values, draft)
        update_chapter_draft(
            db,
            project_id,
            draft_id,
            title=str(values.get("title") or draft.title or ""),
            outline_node_id=values.get("outline_node_id", draft.outline_node_id),
            content=str(
                values.get("content")
                if "content" in values
                else draft.content or ""
            ),
        )
    result = workspace.save(project_id, chapter_id, values)
    command.queue_all(result.sync_intents)
    if draft is not None:
        mark_chapter_draft_saved(db, draft, chapter_id)
    command.finish()
    data = result.data
    if (
        cataloging_mode == "save_and_catalog"
        and data.get("cataloging_impact") != "style_only"
    ):
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
    required = cataloging_required_suffix_ids(db, project_id, chapter_id)
    if required:
        data["recatalog_required_chapter_ids"] = required
    data = _start_chapter_cataloging(
        db,
        project_id,
        data,
        model=payload.model if payload else None,
    )
    if not data.get("cataloging_job"):
        raise ValidationError("章节正文为空，或当前章节不需要重新建档")
    launch = data["cataloging_job"]
    if not launch.get("started") and not launch.get("idempotent_reuse"):
        raise ValidationError(
            str(launch.get("error") or "章节建档启动失败")
        )
    if launch.get("idempotent_reuse"):
        message = (
            "当前章节版本已完成建档，已复用现有结果"
            if launch.get("next_action") == "already_cataloged"
            else "章节建档已启动或正在运行"
        )
    else:
        message = _save_message(data)
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
    """Return a manual quality review without changing the saved chapter."""
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
    return ApiResponse.success(
        data=data,
        message="草稿去除 AI 味候选已生成；尚未保存",
    )


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
    return ApiResponse.success(
        data=data,
        message="草稿质量评分已完成；草稿仍未保存",
    )


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
    data = result.data or {}
    recatalog_count = len(data.get("recatalog_required_chapter_ids") or [])
    warnings = (data.get("cataloging_rollback") or {}).get("warnings") or []
    message = "章节已删除，并已回退该章建档产生的系统状态"
    if recatalog_count:
        message += f"；后续 {recatalog_count} 章需要重新建档"
    if warnings:
        message += f"；{len(warnings)} 项作者后续使用的数据已保留，请复核"
    return ApiResponse.success(data=data, message=message)


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
            project_id,
            chapter_id,
            from_snapshot_id,
            to_snapshot_id,
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
    data = result.data or {}
    count = len(data.get("recatalog_required_chapter_ids") or [])
    message = "章节已恢复；旧建档投影已回退"
    if count:
        message += f"，当前章及后续 {max(count - 1, 0)} 章需要重新建档"
    return ApiResponse.success(data=data, message=message)
