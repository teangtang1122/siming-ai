"""Project HTTP interface."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..core.response import ApiResponse
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.application.projects import ProjectWorkspace
from ..modules.story.interfaces.dependencies import get_story_command
from ..modules.story.interfaces.project_dependencies import get_project_workspace
from ..schemas.project import (
    ProjectCreate,
    ProjectListData,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(tags=["projects"])


class ProjectStorageRepairRequest(BaseModel):
    action: Literal["import_orphans", "refresh_mirror"] = Field(...)


@router.get("/projects", response_model=ApiResponse[ProjectListData])
def list_projects(
    workspace: Annotated[ProjectWorkspace, Depends(get_project_workspace)],
    q: str | None = Query(None, description="Search keyword for title or description"),
):
    return ApiResponse.success(data=workspace.list(q))


@router.post("/projects", response_model=ApiResponse[ProjectResponse])
def create_project(
    payload: ProjectCreate,
    workspace: Annotated[ProjectWorkspace, Depends(get_project_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.create(payload.model_dump())
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="作品创建成功")


@router.get("/projects/{project_id}", response_model=ApiResponse[ProjectResponse])
def get_project(
    project_id: str,
    workspace: Annotated[ProjectWorkspace, Depends(get_project_workspace)],
):
    return ApiResponse.success(data=workspace.get(project_id))


@router.put("/projects/{project_id}", response_model=ApiResponse[ProjectResponse])
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    workspace: Annotated[ProjectWorkspace, Depends(get_project_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.update(project_id, payload.model_dump(exclude_unset=True))
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="作品更新成功")


@router.get("/projects/{project_id}/storage/health")
def get_project_storage_health(
    project_id: str,
    workspace: Annotated[ProjectWorkspace, Depends(get_project_workspace)],
):
    return ApiResponse.success(data=workspace.storage_health(project_id))


@router.post("/projects/{project_id}/storage/repair")
async def repair_project_storage(
    project_id: str,
    payload: ProjectStorageRepairRequest,
    workspace: Annotated[ProjectWorkspace, Depends(get_project_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    data = await workspace.repair_storage(project_id, payload.action)
    if data.get("tool_status") == "ok":
        command.finish()
    else:
        command.rollback()
    data["storage_health"] = workspace.storage_health(project_id)
    return ApiResponse.success(data=data, message=str(data.get("tool_detail") or "success"))


@router.delete("/projects/{project_id}", response_model=ApiResponse[None])
def delete_project(
    project_id: str,
    workspace: Annotated[ProjectWorkspace, Depends(get_project_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.delete(project_id)
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(message="作品已删除")
