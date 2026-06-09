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


async def draft_novel_blueprint(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Draft novel blueprints for a creation session.

    Supports two modes:
    - internal_llm: calls Moshu API if configured
    - external_agent: returns prompt/context/output schema for external agent to fill
    """
    from app.database.models import NovelCreationSession, PublicPromptPack

    session_id = str(args.get("session_id") or "").strip()
    execution_mode = str(args.get("execution_mode") or "external_agent").strip()
    user_brief = str(args.get("user_brief") or "").strip()

    if not session_id:
        return {
            "tool": "draft_novel_blueprint",
            "status": "skipped",
            "detail": "session_id is required",
            "data": None,
        }

    session = db.query(NovelCreationSession).filter(
        NovelCreationSession.id == session_id,
    ).first()
    if not session:
        return {
            "tool": "draft_novel_blueprint",
            "status": "skipped",
            "detail": "Session not found",
            "data": None,
        }

    # Get the prompt pack
    pack = db.query(PublicPromptPack).filter(
        PublicPromptPack.pack_id == "new_project_setup",
        PublicPromptPack.enabled == True,
    ).first()

    if execution_mode == "external_agent":
        # Return prompt and context for external agent to fill
        return {
            "tool": "draft_novel_blueprint",
            "status": "ok",
            "detail": "External agent mode: use the provided prompt to generate blueprints",
            "data": {
                "session_id": session_id,
                "execution_mode": "external_agent",
                "prompt_pack": {
                    "pack_id": pack.pack_id,
                    "system_prompt": pack.system_prompt,
                    "workflow": pack.workflow_json,
                } if pack else None,
                "user_brief": session.user_brief or user_brief,
                "genre": session.genre,
                "target_audience": session.target_audience,
                "platform": session.platform,
                "output_schema": {
                    "blueprints": [
                        {
                            "title": "Blueprint title",
                            "premise": "Core premise in 2-3 sentences",
                            "protagonist": {
                                "name": "Character name",
                                "goal": "What they want",
                                "conflict": "What blocks them",
                            },
                            "world_hook": "Unique world element",
                            "opening_scene": "First scene description",
                            "estimated_chapters": 30,
                        }
                    ],
                    "recommendation": "Which blueprint to pursue and why",
                },
                "next_tool": "review_novel_blueprint",
            },
        }
    else:
        # Internal LLM mode — would call LLM here
        # For now, return a placeholder since this requires API
        return {
            "tool": "draft_novel_blueprint",
            "status": "skipped",
            "detail": "Internal LLM mode requires Moshu API key. Use external_agent mode instead.",
            "data": {
                "session_id": session_id,
                "execution_mode": "internal_llm",
                "hint": "Set execution_mode='external_agent' or configure a Moshu API key",
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
