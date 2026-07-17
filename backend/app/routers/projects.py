"""Project CRUD API endpoints."""
from __future__ import annotations

import json
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import Project
from ..database.session import get_db
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ..modules.story.interfaces.dependencies import get_story_command
from ..schemas.project import ProjectCreate, ProjectListItem, ProjectResponse, ProjectUpdate
from ..services.storage_contract import storage_health
from ..services.workspace import execute_workspace_action

router = APIRouter(tags=["projects"])


class ProjectStorageRepairRequest(BaseModel):
    """Explicit storage repair action for database-authoritative project mirrors."""

    action: Literal["import_orphans", "refresh_mirror"] = Field(
        ...,
        description="import_orphans imports mirror files into DB; refresh_mirror rewrites files from DB",
    )


@router.get("/projects")
def list_projects(
    q: Optional[str] = Query(None, description="Search keyword for title or description"),
    db: Session = Depends(get_db),
):
    """Get project list with optional search."""
    query = db.query(Project)
    if q:
        keyword = f"%{q}%"
        query = query.filter(or_(Project.title.like(keyword), Project.description.like(keyword)))
    projects = query.order_by(Project.updated_at.desc()).all()
    items = [ProjectListItem.model_validate(project) for project in projects]
    return ApiResponse.success(data={"items": [item.model_dump() for item in items], "total": len(items)})


@router.post("/projects")
def create_project(
    payload: ProjectCreate,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Create a new project and initialize its folder-backed content store."""
    db = command.session
    data = payload.model_dump()
    if data.get("tags") is not None:
        data["tags"] = json.dumps(data["tags"], ensure_ascii=False)

    project = Project(**data)
    db.add(project)
    db.flush()
    command.queue(
        ContentSyncIntent(
            project_id=project.id,
            target=ContentSyncTarget.PROJECT,
            entity_id=project.id,
        ),
    )
    command.finish()
    db.refresh(project)
    return ApiResponse.success(
        data=ProjectResponse.model_validate(project).model_dump(),
        message="作品创建成功",
    )


@router.get("/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    """Get project details by ID."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")
    return ApiResponse.success(data=ProjectResponse.model_validate(project).model_dump())


@router.put("/projects/{project_id}")
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Update project information and its project manifest."""
    db = command.session
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise ValidationError("未提供任何更新字段")

    if "tags" in update_data and update_data["tags"] is not None:
        update_data["tags"] = json.dumps(update_data["tags"], ensure_ascii=False)

    for field, value in update_data.items():
        setattr(project, field, value)

    command.queue(
        ContentSyncIntent(
            project_id=project.id,
            target=ContentSyncTarget.PROJECT_MANIFEST,
            entity_id=project.id,
        ),
    )
    command.finish()
    db.refresh(project)
    return ApiResponse.success(
        data=ProjectResponse.model_validate(project).model_dump(),
        message="作品更新成功",
    )


@router.get("/projects/{project_id}/storage/health")
def get_project_storage_health(project_id: str, db: Session = Depends(get_db)):
    """Inspect the database-authoritative project mirror for orphan files."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")
    return ApiResponse.success(data=storage_health(db, project))


@router.post("/projects/{project_id}/storage/repair")
async def repair_project_storage(
    project_id: str,
    payload: ProjectStorageRepairRequest,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Run an explicit repair path through the workspace tool contract."""
    db = command.session
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")

    if payload.action == "import_orphans":
        arguments = {"direction": "import", "confirm_import_from_files": True}
    else:
        arguments = {"direction": "db_to_files"}

    result = await execute_workspace_action(
        db,
        project_id,
        {"tool": "sync_project_files", "arguments": arguments},
    )
    if result.get("status") == "ok":
        command.finish()
        db.refresh(project)
    else:
        command.rollback()
        project = db.query(Project).filter(Project.id == project_id).first()

    data = dict(result.get("data") or {})
    if project:
        data["storage_health"] = storage_health(db, project)
    data["tool_status"] = result.get("status")
    data["tool_detail"] = result.get("detail")
    return ApiResponse.success(data=data, message=str(result.get("detail") or "success"))


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Delete a project and all associated database state."""
    db = command.session
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")

    folder_path = project.folder_path
    db.delete(project)
    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.PROJECT_DELETE,
            entity_id=project_id,
            payload={"folder_path": folder_path},
        ),
    )
    command.finish()
    return ApiResponse.success(message="作品已删除")
