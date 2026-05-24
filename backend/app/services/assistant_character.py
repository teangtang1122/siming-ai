"""Assistant character helpers — resolution and roleplay."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..ai.gateway import LLMGateway
from ..core.db_helpers import get_project_or_404
from ..core.json_repair import parse_json_object
from ..database.models import Character, OutlineNode, OutlineNodeCharacter
from ..prompts.character_prompts import build_roleplay_decision_system
from ..services.context_builders import (
    _build_character_ai_context,
    _build_character_context,
    _build_character_relationships,
    _build_character_timeline,
)
from ..prompts.style_prompts import build_style_context


def _resolve_assistant_characters(
    db: Session,
    project_id: str,
    names: list[str],
    outline_node_id: Optional[str],
    limit: int = 4,
) -> list[Character]:
    resolved: list[Character] = []
    seen: set[str] = set()
    clean_names = {name.strip() for name in names if name.strip()}
    if clean_names:
        characters = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.name.in_(clean_names))
            .all()
        )
        for character in characters:
            resolved.append(character)
            seen.add(character.id)
    if outline_node_id and len(resolved) < limit:
        links = (
            db.query(OutlineNodeCharacter)
            .join(OutlineNode, OutlineNode.id == OutlineNodeCharacter.outline_node_id)
            .filter(OutlineNode.project_id == project_id, OutlineNodeCharacter.outline_node_id == outline_node_id)
            .all()
        )
        for link in links:
            if link.character and link.character.id not in seen:
                resolved.append(link.character)
                seen.add(link.character.id)
            if len(resolved) >= limit:
                break
    if len(resolved) < limit:
        extras = (
            db.query(Character)
            .filter(Character.project_id == project_id)
            .order_by(Character.role_type.asc(), Character.updated_at.desc())
            .limit(limit * 2)
            .all()
        )
        for character in extras:
            if character.id not in seen:
                resolved.append(character)
                seen.add(character.id)
            if len(resolved) >= limit:
                break
    return resolved[:limit]


async def _assistant_character_roleplay(
    db: Session,
    project_id: str,
    character: Character,
    user_message: str,
    outline_ctx: str,
    summaries: str,
    model: Optional[str],
) -> dict:
    project = get_project_or_404(db, project_id)
    system_content = build_roleplay_decision_system(
        character_name=character.name,
        character_context=_build_character_context(character),
        ai_context=_build_character_ai_context(character),
        relationships=_build_character_relationships(db, project_id, character.id),
        timeline=_build_character_timeline(db, character.id),
        style_ctx=build_style_context(project),
        outline_ctx=outline_ctx,
        summaries=summaries,
    )
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_message},
    ]
    result = await LLMGateway.chat_completion(messages=messages, model=model, temperature=0.6, max_tokens=1200)
    parsed = parse_json_object(result.get("content", ""))
    if not parsed:
        parsed = {
            "should_act": False,
            "action_type": "none",
            "content": "",
            "rationale": result.get("content", "")[:500],
        }
    return {
        "character_id": character.id,
        "character_name": character.name,
        "should_act": bool(parsed.get("should_act")),
        "action_type": str(parsed.get("action_type") or "none")[:50],
        "content": str(parsed.get("content") or "")[:4000],
        "rationale": str(parsed.get("rationale") or "")[:1000],
    }
