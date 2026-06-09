"""Novel creation tools — API-free tools for starting new novels.

These tools work without any Moshu model API configured. They help
external agents and the project assistant gather user requirements,
generate blueprints, and apply them to create new projects.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session


async def start_novel_creation_session(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Start a new novel creation session.

    API-free: creates or resumes a session and returns the interview checklist.
    Does not call LLM.
    """
    from app.database.models import NovelCreationSession
    from app.services.prompt_packs.seed import ensure_builtin_packs

    ensure_builtin_packs(db)

    mode = str(args.get("mode") or "internal_llm").strip()
    user_brief = str(args.get("user_brief") or "").strip()
    target_audience = str(args.get("target_audience") or "").strip()
    genre = str(args.get("genre") or "").strip()
    platform = str(args.get("platform") or "").strip()

    # Create new session
    session = NovelCreationSession(
        source_project_id=project_id if project_id else None,
        mode=mode,
        user_brief=user_brief or None,
        target_audience=target_audience or None,
        genre=genre or None,
        platform=platform or None,
        status="drafting",
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # Build interview checklist
    checklist = _build_interview_checklist(user_brief, genre, target_audience, platform)

    # Get the prompt pack for novel creation
    from app.database.models import PublicPromptPack
    pack = db.query(PublicPromptPack).filter(
        PublicPromptPack.pack_id == "new_project_setup",
        PublicPromptPack.enabled == True,
    ).first()

    prompt_pack_data = None
    if pack:
        prompt_pack_data = {
            "pack_id": pack.pack_id,
            "version": pack.version,
            "title": pack.title,
            "system_prompt": pack.system_prompt,
            "workflow": pack.workflow_json,
        }

    return {
        "tool": "start_novel_creation_session",
        "status": "ok",
        "detail": f"Session created: {session.id}",
        "data": {
            "session_id": session.id,
            "mode": mode,
            "status": session.status,
            "checklist": checklist,
            "prompt_pack": prompt_pack_data,
            "missing_fields": checklist["missing"],
        },
    }


def _build_interview_checklist(
    user_brief: str,
    genre: str,
    target_audience: str,
    platform: str,
) -> dict:
    """Build an interview checklist based on provided fields."""
    fields = {
        "genre": {
            "label": "小说类型",
            "description": "如：仙侠、都市、科幻、悬疑、言情、历史",
            "provided": bool(genre),
            "value": genre or None,
        },
        "target_audience": {
            "label": "目标读者",
            "description": "如：男频读者、女频读者、青少年、全年龄",
            "provided": bool(target_audience),
            "value": target_audience or None,
        },
        "platform": {
            "label": "发布平台",
            "description": "如：起点、番茄、晋江、知乎、自出版",
            "provided": bool(platform),
            "value": platform or None,
        },
        "user_brief": {
            "label": "创作方向",
            "description": "你想写什么样的故事？核心卖点是什么？",
            "provided": bool(user_brief),
            "value": user_brief or None,
        },
    }

    missing = [k for k, v in fields.items() if not v["provided"]]

    return {
        "fields": fields,
        "missing": missing,
        "complete": len(missing) == 0,
        "next_action": "ask_user" if missing else "draft_blueprints",
    }
