"""Chapter CRUD, version, diff and restore HTTP interface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..core.response import ApiResponse
from ..modules.story.application.chapters import ChapterWorkspace
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.interfaces.chapter_dependencies import get_chapter_workspace
from ..modules.story.interfaces.dependencies import get_story_command
from ..schemas.chapter import ChapterCreate, ChapterUpdate

router = APIRouter(tags=["chapters"])


@router.get("/projects/{project_id}/chapters")
def list_chapters(
    project_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
):
    return ApiResponse.success(data=workspace.list(project_id))


@router.post("/projects/{project_id}/chapters")
def create_chapter(
    project_id: str,
    payload: ChapterCreate,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.create(project_id, payload.model_dump())
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="章节已创建")


@router.get("/projects/{project_id}/chapters/{chapter_id}")
def get_chapter_detail(
    project_id: str,
    chapter_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
):
    return ApiResponse.success(data=workspace.detail(project_id, chapter_id))


@router.put("/projects/{project_id}/chapters/{chapter_id}")
def save_chapter(
    project_id: str,
    chapter_id: str,
    payload: ChapterUpdate,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.save(
        project_id, chapter_id, payload.model_dump(exclude_unset=True)
    )
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="章节已保存")


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
def restore_chapter_snapshot(
    project_id: str,
    chapter_id: str,
    snapshot_id: str,
    workspace: Annotated[ChapterWorkspace, Depends(get_chapter_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.restore(project_id, chapter_id, snapshot_id)
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="章节已恢复")
