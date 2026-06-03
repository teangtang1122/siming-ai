"""Skill CRUD endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..schemas.skill import (
    SkillCreate,
    SkillDraftRequest,
    SkillMatchPreviewRequest,
    SkillUpdate,
)
from ..services.skills.service import (
    build_skill_draft,
    create_skill,
    delete_skill,
    list_skill_templates,
    list_skill_tools,
    list_skill_versions,
    list_skills,
    preview_skill_match,
    update_skill,
)

router = APIRouter(tags=["skills"])


@router.get("/projects/{project_id}/skills")
def list_project_skills(project_id: str, db: Session = Depends(get_db)):
    """List all skills for a project (auto-seeds built-ins if empty)."""
    get_project_or_404(db, project_id)
    skills = list_skills(db, project_id)
    return ApiResponse.success(data={"items": skills, "total": len(skills)})


@router.post("/projects/{project_id}/skills")
def create_project_skill(
    project_id: str,
    payload: SkillCreate,
    db: Session = Depends(get_db),
):
    """Create a new skill."""
    get_project_or_404(db, project_id)
    skill = create_skill(db, project_id, payload.model_dump())
    return ApiResponse.success(data=skill, message="技能创建成功")


@router.get("/projects/{project_id}/skills/templates")
def list_project_skill_templates(project_id: str, db: Session = Depends(get_db)):
    """List built-in skill templates for assisted skill creation."""
    get_project_or_404(db, project_id)
    templates = list_skill_templates()
    return ApiResponse.success(data={"items": templates, "total": len(templates)})


@router.get("/projects/{project_id}/skills/tools")
def list_project_skill_tools(project_id: str, db: Session = Depends(get_db)):
    """List workspace tools that can be referenced by skills."""
    get_project_or_404(db, project_id)
    tools = list_skill_tools()
    return ApiResponse.success(data={"items": tools, "total": len(tools)})


@router.post("/projects/{project_id}/skills/draft")
def draft_project_skill(
    project_id: str,
    payload: SkillDraftRequest,
    db: Session = Depends(get_db),
):
    """Build a deterministic skill draft from user requirements and a template."""
    get_project_or_404(db, project_id)
    draft = build_skill_draft(
        payload.requirements,
        template_key=payload.template_key,
        scope=payload.scope,
    )
    return ApiResponse.success(data=draft, message="技能草案已生成")


@router.post("/projects/{project_id}/skills/preview-match")
def preview_project_skill_match(
    project_id: str,
    payload: SkillMatchPreviewRequest,
    db: Session = Depends(get_db),
):
    """Preview which skills would be selected for a user message."""
    get_project_or_404(db, project_id)
    candidate = payload.candidate.model_dump() if payload.candidate else None
    preview = preview_skill_match(
        db,
        project_id,
        message=payload.message,
        scope=payload.scope,
        candidate=candidate,
    )
    return ApiResponse.success(data=preview)


@router.get("/projects/{project_id}/skills/{skill_id}/versions")
def list_project_skill_versions(
    project_id: str,
    skill_id: str,
    db: Session = Depends(get_db),
):
    """List version snapshots for a skill."""
    get_project_or_404(db, project_id)
    versions = list_skill_versions(db, project_id, skill_id)
    return ApiResponse.success(data={"items": versions, "total": len(versions)})


@router.put("/projects/{project_id}/skills/{skill_id}")
def update_project_skill(
    project_id: str,
    skill_id: str,
    payload: SkillUpdate,
    db: Session = Depends(get_db),
):
    """Update a skill."""
    get_project_or_404(db, project_id)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise ValidationError("未提供任何更新字段")
    skill = update_skill(db, project_id, skill_id, update_data)
    return ApiResponse.success(data=skill, message="技能更新成功")


@router.delete("/projects/{project_id}/skills/{skill_id}")
def delete_project_skill(
    project_id: str,
    skill_id: str,
    db: Session = Depends(get_db),
):
    """Delete a skill (built-in skills cannot be deleted)."""
    get_project_or_404(db, project_id)
    delete_skill(db, project_id, skill_id)
    return ApiResponse.success(message="技能已删除")
