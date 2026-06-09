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


async def review_novel_blueprint(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Review novel blueprints with internal or external model support.

    Internal mode may call Moshu API. External mode returns review prompt
    and rubric for external agent to fill.
    """
    from app.database.models import NovelCreationSession

    session_id = str(args.get("session_id") or "").strip()
    execution_mode = str(args.get("execution_mode") or "external_agent").strip()
    blueprint_json = args.get("blueprint")

    if not session_id:
        return {
            "tool": "review_novel_blueprint",
            "status": "skipped",
            "detail": "session_id is required",
            "data": None,
        }

    session = db.query(NovelCreationSession).filter(
        NovelCreationSession.id == session_id,
    ).first()
    if not session:
        return {
            "tool": "review_novel_blueprint",
            "status": "skipped",
            "detail": "Session not found",
            "data": None,
        }

    # Save blueprint to session if provided
    if blueprint_json and isinstance(blueprint_json, (dict, list)):
        session.blueprint_json = blueprint_json
        db.commit()

    if execution_mode == "external_agent":
        return {
            "tool": "review_novel_blueprint",
            "status": "ok",
            "detail": "External agent mode: use the provided rubric to review the blueprint",
            "data": {
                "session_id": session_id,
                "execution_mode": "external_agent",
                "blueprint": session.blueprint_json,
                "review_dimensions": [
                    {"name": "premise_clarity", "description": "核心设定是否清晰", "max_score": 10},
                    {"name": "protagonist_goal", "description": "主角目标是否明确", "max_score": 10},
                    {"name": "conflict_engine", "description": "冲突驱动力是否足够", "max_score": 10},
                    {"name": "world_rules", "description": "世界观规则是否自洽", "max_score": 10},
                    {"name": "character_relationship_pressure", "description": "角色关系是否有张力", "max_score": 10},
                    {"name": "golden_three_hook", "description": "黄金三章钩子是否足够", "max_score": 10},
                    {"name": "thirty_chapter_runway", "description": "30章剧情跑道是否充足", "max_score": 10},
                    {"name": "trope_freshness", "description": "套路是否有新意", "max_score": 10},
                ],
                "output_schema": {
                    "scores": {"dimension_name": 0},
                    "total_score": 0,
                    "pass": True,
                    "issues": ["Issue description"],
                    "suggestions": ["Suggestion description"],
                },
                "next_tool": "apply_novel_blueprint",
            },
        }
    else:
        return {
            "tool": "review_novel_blueprint",
            "status": "skipped",
            "detail": "Internal LLM mode requires Moshu API key. Use external_agent mode instead.",
            "data": {
                "session_id": session_id,
                "execution_mode": "internal_llm",
                "hint": "Set execution_mode='external_agent' or configure a Moshu API key",
            },
        }


async def apply_novel_blueprint(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """Apply a confirmed blueprint to create a real Moshu project.

    Creates project, worldbuilding, characters, relationships, outline,
    skills, and memories from the blueprint.
    """
    from app.database.models import (
        NovelCreationSession, Project,
        Character, WorldbuildingEntry, OutlineNode, CharacterRelationship,
    )

    session_id = str(args.get("session_id") or "").strip()
    blueprint_index = int(args.get("blueprint_index", 0))
    mode = str(args.get("mode") or "auto").strip()

    if not session_id:
        return {
            "tool": "apply_novel_blueprint",
            "status": "skipped",
            "detail": "session_id is required",
            "data": None,
        }

    session = db.query(NovelCreationSession).filter(
        NovelCreationSession.id == session_id,
    ).first()
    if not session:
        return {
            "tool": "apply_novel_blueprint",
            "status": "skipped",
            "detail": "Session not found",
            "data": None,
        }

    blueprints = session.blueprint_json
    if not blueprints:
        return {
            "tool": "apply_novel_blueprint",
            "status": "skipped",
            "detail": "No blueprint found. Call draft_novel_blueprint first.",
            "data": None,
        }

    # Handle both single blueprint and list
    if isinstance(blueprints, list):
        if blueprint_index >= len(blueprints):
            return {
                "tool": "apply_novel_blueprint",
                "status": "skipped",
                "detail": f"Blueprint index {blueprint_index} out of range ({len(blueprints)} blueprints)",
                "data": None,
            }
        blueprint = blueprints[blueprint_index]
    else:
        blueprint = blueprints

    if mode == "manual":
        # Return candidates without applying
        return {
            "tool": "apply_novel_blueprint",
            "status": "ok",
            "detail": "Manual mode: review candidates before applying",
            "data": {
                "session_id": session_id,
                "mode": "manual",
                "candidates": _build_blueprint_candidates(blueprint),
            },
        }

    # Auto mode: create the project
    try:
        # Create project
        title = blueprint.get("title", "Untitled Novel")
        project = Project(
            title=title,
            description=blueprint.get("premise", ""),
            writing_style=blueprint.get("writing_style", "natural"),
        )
        db.add(project)
        db.flush()  # Get the ID

        created_items = {
            "project_id": project.id,
            "characters": [],
            "worldbuilding": [],
            "outline": [],
            "relationships": [],
        }

        # Create protagonist
        protagonist = blueprint.get("protagonist", {})
        if protagonist.get("name"):
            char = Character(
                project_id=project.id,
                name=protagonist["name"],
                personality=protagonist.get("personality", ""),
                background=protagonist.get("background", ""),
                role_type="protagonist",
                current_goal=protagonist.get("goal", ""),
                active_conflict=protagonist.get("conflict", ""),
            )
            db.add(char)
            created_items["characters"].append(protagonist["name"])

        # Create other characters
        for char_data in blueprint.get("characters", []):
            if isinstance(char_data, dict) and char_data.get("name"):
                char = Character(
                    project_id=project.id,
                    name=char_data["name"],
                    personality=char_data.get("personality", ""),
                    background=char_data.get("background", ""),
                    role_type=char_data.get("role_type", "supporting"),
                )
                db.add(char)
                created_items["characters"].append(char_data["name"])

        # Create worldbuilding entries
        for wb_data in blueprint.get("worldbuilding", []):
            if isinstance(wb_data, dict) and wb_data.get("title"):
                entry = WorldbuildingEntry(
                    project_id=project.id,
                    title=wb_data["title"],
                    content=wb_data.get("content", ""),
                    dimension=wb_data.get("dimension", "culture"),
                )
                db.add(entry)
                created_items["worldbuilding"].append(wb_data["title"])

        # Create outline nodes (first 10 chapters)
        outline_data = blueprint.get("outline", [])
        for i, node_data in enumerate(outline_data[:10]):
            if isinstance(node_data, dict) and node_data.get("title"):
                node = OutlineNode(
                    project_id=project.id,
                    title=node_data["title"],
                    summary=node_data.get("summary", ""),
                    node_type=node_data.get("node_type", "chapter"),
                    sort_order=i,
                )
                db.add(node)
                created_items["outline"].append(node_data["title"])

        # Update session
        session.created_project_id = project.id
        session.status = "completed"
        session.completed_at = __import__("datetime").datetime.utcnow()

        db.commit()

        return {
            "tool": "apply_novel_blueprint",
            "status": "ok",
            "detail": f"Project created: {title} ({len(created_items['characters'])} characters, {len(created_items['outline'])} outline nodes)",
            "data": created_items,
        }

    except Exception as exc:
        db.rollback()
        session.status = "failed"
        db.commit()
        return {
            "tool": "apply_novel_blueprint",
            "status": "error",
            "detail": f"Failed to apply blueprint: {exc}",
            "data": None,
        }


def _build_blueprint_candidates(blueprint: dict) -> list[dict]:
    """Build candidate list from blueprint for manual review."""
    candidates = []

    # Project
    candidates.append({
        "type": "project",
        "action": "create",
        "title": blueprint.get("title", "Untitled"),
        "description": blueprint.get("premise", ""),
    })

    # Characters
    protagonist = blueprint.get("protagonist", {})
    if protagonist.get("name"):
        candidates.append({
            "type": "character",
            "action": "create",
            "name": protagonist["name"],
            "role_type": "protagonist",
        })
    for char in blueprint.get("characters", []):
        if isinstance(char, dict) and char.get("name"):
            candidates.append({
                "type": "character",
                "action": "create",
                "name": char["name"],
                "role_type": char.get("role_type", "supporting"),
            })

    # Worldbuilding
    for wb in blueprint.get("worldbuilding", []):
        if isinstance(wb, dict) and wb.get("title"):
            candidates.append({
                "type": "worldbuilding",
                "action": "create",
                "title": wb["title"],
                "dimension": wb.get("dimension", "culture"),
            })

    # Outline
    for node in blueprint.get("outline", []):
        if isinstance(node, dict) and node.get("title"):
            candidates.append({
                "type": "outline",
                "action": "create",
                "title": node["title"],
            })

    return candidates


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
