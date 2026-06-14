"""Project CRUD API endpoints."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import Project
from ..database.session import get_db
from ..schemas.project import ProjectCreate, ProjectListItem, ProjectResponse, ProjectUpdate
from ..services.content_store import (
    delete_project_folder,
    ensure_project_folder,
    write_project_manifest,
)

router = APIRouter(tags=["projects"])


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
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    """Create a new project and initialize its folder-backed content store."""
    data = payload.model_dump()
    if data.get("tags") is not None:
        data["tags"] = json.dumps(data["tags"], ensure_ascii=False)

    project = Project(**data)
    db.add(project)
    db.commit()
    db.refresh(project)
    ensure_project_folder(db, project)
    write_project_manifest(db, project)
    db.commit()
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
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)):
    """Update project information and its project manifest."""
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

    db.commit()
    db.refresh(project)
    write_project_manifest(db, project)
    db.commit()
    db.refresh(project)
    return ApiResponse.success(
        data=ProjectResponse.model_validate(project).model_dump(),
        message="作品更新成功",
    )


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db)):
    """Delete a project and all associated database state."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("作品不存在")

    delete_project_folder(project)
    db.delete(project)
    db.commit()
    return ApiResponse.success(message="作品已删除")
