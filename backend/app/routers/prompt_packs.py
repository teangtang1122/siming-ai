"""Prompt-pack browsing and contribution export endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError
from ..core.response import ApiResponse
from ..database.models import PublicPromptPack
from ..database.session import get_db
from ..schemas.prompt_contribution import PromptContributionCreate
from ..services.prompt_contributions import build_prompt_contribution_package
from ..services.prompt_packs.seed import ensure_builtin_packs
from ..services.workspace.tools.prompt_packs import get_prompt_pack

router = APIRouter(tags=["prompt-packs"])


@router.get("/projects/{project_id}/prompt-packs")
def list_project_prompt_packs(project_id: str, db: Session = Depends(get_db)):
    """List global and project-scoped prompt packs for GUI editing/contribution."""
    get_project_or_404(db, project_id)
    ensure_builtin_packs(db)
    packs = (
        db.query(PublicPromptPack)
        .filter(
            PublicPromptPack.enabled == True,  # noqa: E712
            or_(PublicPromptPack.project_id == None, PublicPromptPack.project_id == project_id),  # noqa: E711
        )
        .order_by(PublicPromptPack.scope, PublicPromptPack.pack_id)
        .all()
    )
    return ApiResponse.success(data={
        "items": [
            {
                "id": pack.id,
                "project_id": pack.project_id,
                "pack_id": pack.pack_id,
                "version": pack.version,
                "scope": pack.scope,
                "title": pack.title,
                "summary": pack.summary,
                "is_builtin": pack.is_builtin,
                "enabled": pack.enabled,
                "updated_at": pack.updated_at.isoformat() if pack.updated_at else None,
            }
            for pack in packs
        ],
        "total": len(packs),
    })


@router.get("/projects/{project_id}/prompt-packs/{pack_id}")
async def get_project_prompt_pack(project_id: str, pack_id: str, db: Session = Depends(get_db)):
    """Return the effective prompt pack detail used by Siming workflows."""
    get_project_or_404(db, project_id)
    result = await get_prompt_pack(db, project_id, {"pack_id": pack_id})
    if result.get("status") != "ok" or not result.get("data"):
        raise NotFoundError(f"提示词包不存在：{pack_id}")
    return ApiResponse.success(data=result["data"])


@router.post("/projects/{project_id}/prompt-contributions/export")
async def export_prompt_contribution(
    project_id: str,
    payload: PromptContributionCreate,
    db: Session = Depends(get_db),
):
    """Create a local prompt contribution package and a prefilled GitHub issue URL."""
    project = get_project_or_404(db, project_id)
    result = await get_prompt_pack(db, project_id, {"pack_id": payload.pack_id})
    if result.get("status") != "ok" or not result.get("data"):
        raise NotFoundError(f"提示词包不存在：{payload.pack_id}")

    data = build_prompt_contribution_package(
        db,
        project,
        pack_detail=result["data"],
        edited_system_prompt=payload.edited_system_prompt,
        change_summary=payload.change_summary,
        expected_effect=payload.expected_effect,
        test_notes=payload.test_notes,
        contributor_name=payload.contributor_name,
        contact=payload.contact,
    )
    return ApiResponse.success(data=data, message="提示词投稿包已生成")
