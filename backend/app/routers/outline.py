"""Outline tree HTTP interface."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.application.outline import OutlineWorkspace
from ..modules.story.interfaces.dependencies import get_story_command
from ..modules.story.interfaces.outline_dependencies import get_outline_workspace
from ..schemas.outline import (
    OutlineDraftConfirmRequest,
    OutlineDraftUpdate,
    OutlineNodeCreate,
    OutlineNodeUpdate,
    OutlineReorderItem,
    OutlineReorderRequest,
)
from ..services.workspace.outline_drafts import (
    confirm_outline_draft,
    discard_outline_draft,
    find_outline_draft,
    latest_pending_outline_draft,
    outline_draft_result_data,
    supersede_outline_draft,
    update_outline_draft,
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


@router.get("/projects/{project_id}/outline-drafts/pending")
def get_pending_outline_draft(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
):
    """Restore the author-visible unsaved proposal after reload."""
    draft = latest_pending_outline_draft(db, project_id)
    return ApiResponse.success(data=outline_draft_result_data(draft) if draft else None)


@router.put("/projects/{project_id}/outline-drafts/{draft_id}")
def edit_outline_draft(
    project_id: str,
    draft_id: str,
    payload: OutlineDraftUpdate,
    db: Annotated[Session, Depends(get_db)],
):
    draft = update_outline_draft(
        db,
        project_id,
        draft_id,
        nodes=[node.model_dump(exclude_none=True) for node in payload.nodes],
        design_notes=payload.design_notes,
    )
    return ApiResponse.success(data=outline_draft_result_data(draft), message="大纲草稿已更新")


@router.post("/projects/{project_id}/outline-drafts/{draft_id}/confirm")
async def confirm_generated_outline_draft(
    project_id: str,
    draft_id: str,
    payload: OutlineDraftConfirmRequest,
    db: Annotated[Session, Depends(get_db)],
):
    data = await confirm_outline_draft(db, project_id, draft_id)
    chapter_ids = [str(value) for value in data.get("chapter_outline_node_ids") or []]
    if payload.write_after_confirm:
        if not chapter_ids:
            data["next_author_request"] = None
            data["write_blocked_reason"] = "确认结果中没有章级节点，不能发起写章"
        else:
            outline_node_id = chapter_ids[0]
            data["next_author_request"] = {
                "requires_new_agent_turn": True,
                "outline_node_id": outline_node_id,
                "message": f"请根据刚确认的章级大纲（ID：{outline_node_id}）写这一章。",
            }
    return ApiResponse.success(
        data=data,
        message="大纲已确认；写章需在新的作者授权 Agent 轮执行"
        if payload.write_after_confirm
        else "大纲已确认",
    )


@router.post("/projects/{project_id}/outline-drafts/{draft_id}/regenerate")
def regenerate_outline_draft_request(
    project_id: str,
    draft_id: str,
    db: Annotated[Session, Depends(get_db)],
):
    draft = find_outline_draft(db, project_id, draft_id)
    if draft is None:
        from ..core.exceptions import ValidationError

        raise ValidationError("大纲草稿不存在")
    parent_id = str(draft.parent_id or "") or None
    insert_after_id = str(draft.insert_after_id or "") or None
    supersede_outline_draft(db, project_id, draft_id)
    return ApiResponse.success(
        data={
            "superseded_draft_id": draft_id,
            "next_author_request": {
                "requires_new_agent_turn": True,
                "parent_id": parent_id,
                "insert_after_id": insert_after_id,
                "message": "请重新规划刚才的大纲草稿，保留作者已指定的插入位置。",
            },
        },
        message="旧草稿已归档；重新规划需在新的作者授权 Agent 轮执行",
    )


@router.delete("/projects/{project_id}/outline-drafts/{draft_id}")
def discard_generated_outline_draft(
    project_id: str,
    draft_id: str,
    db: Annotated[Session, Depends(get_db)],
):
    draft = discard_outline_draft(db, project_id, draft_id)
    return ApiResponse.success(data=outline_draft_result_data(draft), message="大纲草稿已丢弃")


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
