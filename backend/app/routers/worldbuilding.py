"""Worldbuilding HTTP interface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..core.response import ApiResponse
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.application.worldbuilding import WorldbuildingWorkspace
from ..modules.story.interfaces.dependencies import get_story_command
from ..modules.story.interfaces.worldbuilding_dependencies import (
    get_worldbuilding_workspace,
)
from ..schemas.worldbuilding import (
    WorldbuildingDimension,
    WorldbuildingEntryCreate,
    WorldbuildingEntryUpdate,
)

router = APIRouter(tags=["worldbuilding"])


@router.get("/projects/{project_id}/worldbuilding")
def list_worldbuilding_entries(
    project_id: str,
    workspace: Annotated[
        WorldbuildingWorkspace, Depends(get_worldbuilding_workspace)
    ],
    dimension: Annotated[
        WorldbuildingDimension | None, Query(description="按维度过滤")
    ] = None,
):
    return ApiResponse.success(data=workspace.list(project_id, dimension))


@router.post("/projects/{project_id}/worldbuilding")
def create_worldbuilding_entry(
    project_id: str,
    payload: WorldbuildingEntryCreate,
    workspace: Annotated[
        WorldbuildingWorkspace, Depends(get_worldbuilding_workspace)
    ],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.create(project_id, payload.model_dump())
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="世界观条目创建成功")


@router.put("/projects/{project_id}/worldbuilding/{entry_id}")
def update_worldbuilding_entry(
    project_id: str,
    entry_id: str,
    payload: WorldbuildingEntryUpdate,
    workspace: Annotated[
        WorldbuildingWorkspace, Depends(get_worldbuilding_workspace)
    ],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.update(
        project_id, entry_id, payload.model_dump(exclude_unset=True)
    )
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="世界观条目更新成功")


@router.delete("/projects/{project_id}/worldbuilding/{entry_id}")
def delete_worldbuilding_entry(
    project_id: str,
    entry_id: str,
    workspace: Annotated[
        WorldbuildingWorkspace, Depends(get_worldbuilding_workspace)
    ],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.delete(project_id, entry_id)
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(message="世界观条目已删除")


@router.get("/projects/{project_id}/worldbuilding/{entry_id}/versions")
def list_worldbuilding_versions(
    project_id: str,
    entry_id: str,
    workspace: Annotated[
        WorldbuildingWorkspace, Depends(get_worldbuilding_workspace)
    ],
):
    return ApiResponse.success(data=workspace.versions(project_id, entry_id))


@router.get("/projects/{project_id}/worldbuilding/{entry_id}/timeline")
def list_worldbuilding_timeline(
    project_id: str,
    entry_id: str,
    workspace: Annotated[
        WorldbuildingWorkspace, Depends(get_worldbuilding_workspace)
    ],
):
    return ApiResponse.success(data=workspace.timeline(project_id, entry_id))
