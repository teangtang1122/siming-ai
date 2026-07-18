"""Outline tree HTTP interface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..core.response import ApiResponse
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.application.outline import OutlineWorkspace
from ..modules.story.interfaces.dependencies import get_story_command
from ..modules.story.interfaces.outline_dependencies import get_outline_workspace
from ..schemas.outline import (
    OutlineNodeCreate,
    OutlineNodeUpdate,
    OutlineReorderItem,
    OutlineReorderRequest,
)

router = APIRouter(tags=["outline"])


def _normalize_reorder_items(payload: OutlineReorderRequest) -> list[OutlineReorderItem]:
    if payload.sort_order is not None:
        return [
            OutlineReorderItem(id=node_id, parent_id=payload.parent_id, sort_order=index)
            for index, node_id in enumerate(payload.sort_order)
        ]
    return payload.items


@router.get("/projects/{project_id}/outline")
def get_outline(
    project_id: str,
    workspace: Annotated[OutlineWorkspace, Depends(get_outline_workspace)],
):
    return ApiResponse.success(data=workspace.read(project_id))


@router.post("/projects/{project_id}/outline")
def create_outline_node(
    project_id: str,
    payload: OutlineNodeCreate,
    workspace: Annotated[OutlineWorkspace, Depends(get_outline_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.create(project_id, payload.model_dump())
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="大纲节点已创建")


@router.put("/projects/{project_id}/outline/reorder")
def reorder_outline(
    project_id: str,
    payload: OutlineReorderRequest,
    workspace: Annotated[OutlineWorkspace, Depends(get_outline_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    items = [item.model_dump() for item in _normalize_reorder_items(payload)]
    result = workspace.reorder(project_id, items)
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="大纲排序已更新")


@router.put("/projects/{project_id}/outline/{node_id}")
def update_outline_node(
    project_id: str,
    node_id: str,
    payload: OutlineNodeUpdate,
    workspace: Annotated[OutlineWorkspace, Depends(get_outline_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.update(
        project_id, node_id, payload.model_dump(exclude_unset=True)
    )
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(data=result.data, message="大纲节点已更新")


@router.delete("/projects/{project_id}/outline/{node_id}")
def delete_outline_node(
    project_id: str,
    node_id: str,
    workspace: Annotated[OutlineWorkspace, Depends(get_outline_workspace)],
    command: Annotated[StoryCommandContext, Depends(get_story_command)],
):
    result = workspace.delete(project_id, node_id)
    command.queue_all(result.sync_intents)
    command.finish()
    return ApiResponse.success(message="大纲节点已删除")
