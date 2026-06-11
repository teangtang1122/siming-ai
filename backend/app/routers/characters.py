"""Character CRUD, version history, relationship network, and AI suggestion endpoints."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.db_helpers import get_character_or_404, get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import (
    Chapter,
    Character,
    CharacterAlias,
    CharacterChangeLog,
    CharacterRelationship,
    CharacterVersion,
)
from ..database.session import get_db
from ..schemas.character import (
    CharacterAIConfigUpdate,
    CharacterCreate,
    CharacterMergeRequest,
    CharacterUpdate,
    CharacterVersionItem,
    RelationshipUpdate,
)
from ..services.character_service import (
    apply_change_log_to_character,
    character_to_dict,
    create_character_version,
    dumps_list,
    get_appearances,
    loads_list,
    snapshot_character,
    sync_character_aliases,
)
from ..services.character_merge_service import (
    build_character_merge_preview,
    find_duplicate_character_candidates,
    merge_characters,
)

router = APIRouter(tags=["characters"])


@router.get("/projects/{project_id}/characters")
def list_characters(project_id: str, q: Optional[str] = None, db: Session = Depends(get_db)):
    """Get project character list."""
    get_project_or_404(db, project_id)
    query = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .filter(or_(Character.role_type.is_(None), Character.role_type != "merged_alias"))
    )
    if q:
        keyword = f"%{q}%"
        alias_ids = [
            row.character_id
            for row in db.query(CharacterAlias.character_id)
            .filter(CharacterAlias.project_id == project_id, CharacterAlias.alias.like(keyword))
            .all()
        ]
        query = query.filter(
            or_(
                Character.name.like(keyword),
                Character.appearance.like(keyword),
                Character.personality.like(keyword),
                Character.background.like(keyword),
                Character.role_type.like(keyword),
                Character.id.in_(alias_ids) if alias_ids else False,
            )
        )
    characters = query.order_by(Character.updated_at.desc()).all()
    items = [character_to_dict(character) for character in characters]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.post("/projects/{project_id}/characters")
def create_character(project_id: str, payload: CharacterCreate, db: Session = Depends(get_db)):
    """Create a character."""
    get_project_or_404(db, project_id)
    character = Character(
        project_id=project_id,
        name=payload.name,
        appearance=payload.appearance,
        personality=payload.personality,
        background=payload.background,
        abilities=dumps_list(payload.abilities),
        role_type=payload.role_type,
        age=payload.age,
        life_status=payload.life_status,
        current_location=payload.current_location,
        realm_or_level=payload.realm_or_level,
        physical_state=payload.physical_state,
        mental_state=payload.mental_state,
        current_goal=payload.current_goal,
        active_conflict=payload.active_conflict,
        abilities_state=payload.abilities_state,
        items_or_assets=payload.items_or_assets,
        is_evolution_tracked=payload.is_evolution_tracked,
    )
    db.add(character)
    db.flush()
    sync_character_aliases(db, character, payload.aliases)
    db.commit()
    db.refresh(character)
    return ApiResponse.success(data=character_to_dict(character), message="角色创建成功")


@router.get("/projects/{project_id}/characters/relationships")
def get_relationship_network(project_id: str, db: Session = Depends(get_db)):
    """Get all character relationship network data for a project."""
    get_project_or_404(db, project_id)
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id)
        .filter(or_(Character.role_type.is_(None), Character.role_type != "merged_alias"))
        .all()
    )
    visible_ids = {character.id for character in characters}
    relationships = (
        db.query(CharacterRelationship)
        .filter(CharacterRelationship.project_id == project_id)
        .order_by(CharacterRelationship.created_at.asc())
        .all()
    )
    nodes = [
        {
            "id": character.id,
            "name": character.name,
            "role_type": character.role_type,
            "current_version": character.current_version,
        }
        for character in characters
    ]
    edges = [
        {
            "id": relationship.id,
            "from": relationship.character_a_id,
            "to": relationship.character_b_id,
            "relationship_type": relationship.relationship_type,
            "description": relationship.description,
            "created_at": relationship.created_at.isoformat() if relationship.created_at else None,
        }
        for relationship in relationships
        if relationship.character_a_id in visible_ids and relationship.character_b_id in visible_ids
    ]
    return ApiResponse.success(data={"nodes": nodes, "edges": edges, "total": len(edges)})


@router.get("/projects/{project_id}/characters/duplicates")
def list_duplicate_character_candidates(project_id: str, db: Session = Depends(get_db)):
    """Find likely duplicate character cards for manual review."""
    get_project_or_404(db, project_id)
    return ApiResponse.success(data={"items": find_duplicate_character_candidates(db, project_id)})


@router.post("/projects/{project_id}/characters/merge-preview")
def preview_character_merge(
    project_id: str,
    payload: CharacterMergeRequest,
    db: Session = Depends(get_db),
):
    """Preview how two character cards will be merged."""
    get_project_or_404(db, project_id)
    try:
        data = build_character_merge_preview(
            db,
            project_id,
            payload.primary_id,
            payload.secondary_id,
            payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return ApiResponse.success(data=data)


@router.post("/projects/{project_id}/characters/merge")
def merge_duplicate_characters(
    project_id: str,
    payload: CharacterMergeRequest,
    db: Session = Depends(get_db),
):
    """Merge a duplicate character into a primary character card."""
    get_project_or_404(db, project_id)
    try:
        result = merge_characters(
            db,
            project_id,
            payload.primary_id,
            payload.secondary_id,
            payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    db.commit()
    return ApiResponse.success(data=result, message="角色已合并")


@router.get("/projects/{project_id}/characters/{character_id}")
def get_character_detail(project_id: str, character_id: str, db: Session = Depends(get_db)):
    """Get character detail with current version and appearance records."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    data = character_to_dict(character)
    data["appearances"] = get_appearances(db, character.id)
    return ApiResponse.success(data=data)


@router.put("/projects/{project_id}/characters/{character_id}")
def update_character(
    project_id: str,
    character_id: str,
    payload: CharacterUpdate,
    db: Session = Depends(get_db),
):
    """Update character fields and create a version snapshot."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    update_data = payload.model_dump(exclude_unset=True)
    change_summary = update_data.pop("change_summary", None)
    if not update_data:
        raise ValidationError("未提供任何更新字段")

    for field, value in update_data.items():
        if field == "abilities":
            character.abilities = dumps_list(value)
        elif field == "aliases":
            sync_character_aliases(db, character, value)
        else:
            setattr(character, field, value)

    character.current_version = (character.current_version or 1) + 1
    db.flush()
    snapshot = CharacterVersion(
        character_id=character.id,
        version_number=character.current_version,
        snapshot_data=json.dumps(snapshot_character(character), ensure_ascii=False),
        change_summary=change_summary or "手动更新角色档案",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(character)
    return ApiResponse.success(data=character_to_dict(character), message="角色更新成功")


@router.delete("/projects/{project_id}/characters/{character_id}")
def delete_character(project_id: str, character_id: str, db: Session = Depends(get_db)):
    """Delete a character and its relationships."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    db.query(CharacterRelationship).filter(
        CharacterRelationship.project_id == project_id,
        or_(
            CharacterRelationship.character_a_id == character.id,
            CharacterRelationship.character_b_id == character.id,
        ),
    ).delete(synchronize_session=False)
    db.delete(character)
    db.commit()
    return ApiResponse.success(message="角色已删除")


@router.get("/projects/{project_id}/characters/{character_id}/versions")
def list_character_versions(project_id: str, character_id: str, db: Session = Depends(get_db)):
    """Get character version history."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    versions = (
        db.query(CharacterVersion)
        .filter(CharacterVersion.character_id == character.id)
        .order_by(CharacterVersion.version_number.desc())
        .all()
    )
    items = [
        CharacterVersionItem.model_validate(version).model_dump(mode="json")
        for version in versions
    ]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.get("/projects/{project_id}/characters/{character_id}/versions/{version_id}")
def get_character_version(
    project_id: str,
    character_id: str,
    version_id: str,
    db: Session = Depends(get_db),
):
    """Get a historical character version detail."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    version = (
        db.query(CharacterVersion)
        .filter(CharacterVersion.id == version_id, CharacterVersion.character_id == character.id)
        .first()
    )
    if not version:
        raise NotFoundError("角色版本不存在")
    data = CharacterVersionItem.model_validate(version).model_dump(mode="json")
    data["snapshot_data"] = json.loads(version.snapshot_data)
    return ApiResponse.success(data=data)


@router.put("/projects/{project_id}/characters/{character_id}/relationships")
def update_character_relationships(
    project_id: str,
    character_id: str,
    payload: RelationshipUpdate,
    db: Session = Depends(get_db),
):
    """Replace all relationships connected to the current character."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    target_ids = {item.target_character_id for item in payload.relationships}
    if character.id in target_ids:
        raise ValidationError("角色不能与自身建立关系")

    if target_ids:
        existing_count = (
            db.query(Character)
            .filter(Character.project_id == project_id, Character.id.in_(target_ids))
            .count()
        )
        if existing_count != len(target_ids):
            raise ValidationError("关系目标角色必须属于当前作品")

    db.query(CharacterRelationship).filter(
        CharacterRelationship.project_id == project_id,
        or_(
            CharacterRelationship.character_a_id == character.id,
            CharacterRelationship.character_b_id == character.id,
        ),
    ).delete(synchronize_session=False)

    for item in payload.relationships:
        relationship = CharacterRelationship(
            project_id=project_id,
            character_a_id=character.id,
            character_b_id=item.target_character_id,
            relationship_type=item.relationship_type,
            description=item.description,
        )
        db.add(relationship)

    db.commit()
    return get_relationship_network(project_id, db)


@router.get("/projects/{project_id}/characters/{character_id}/ai-config")
def get_character_ai_config(project_id: str, character_id: str, db: Session = Depends(get_db)):
    """Get a character's AI dialogue configuration."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    config = character.ai_config
    if not config:
        from ..database.models import CharacterAIConfig
        config = CharacterAIConfig(character_id=character.id)
        db.add(config)
        db.commit()
        db.refresh(config)
    return ApiResponse.success(data={
        "id": config.id,
        "character_id": config.character_id,
        "tone_style": config.tone_style or "neutral",
        "catchphrases": loads_list(config.catchphrases),
        "verbosity": config.verbosity or "moderate",
        "emotion_tendency": config.emotion_tendency or "neutral",
        "model_override": config.model_override,
        "custom_system_prompt": config.custom_system_prompt,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    })


@router.put("/projects/{project_id}/characters/{character_id}/ai-config")
def update_character_ai_config(
    project_id: str,
    character_id: str,
    payload: CharacterAIConfigUpdate,
    db: Session = Depends(get_db),
):
    """Update a character's AI dialogue configuration."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    from ..database.models import CharacterAIConfig
    config = character.ai_config
    if not config:
        config = CharacterAIConfig(character_id=character.id)
        db.add(config)
        db.flush()

    update_data = payload.model_dump(exclude_unset=True)
    if "catchphrases" in update_data:
        config.catchphrases = dumps_list(update_data.pop("catchphrases"))
    for field, value in update_data.items():
        setattr(config, field, value)

    db.commit()
    db.refresh(config)
    return ApiResponse.success(data={
        "id": config.id,
        "character_id": config.character_id,
        "tone_style": config.tone_style or "neutral",
        "catchphrases": loads_list(config.catchphrases),
        "verbosity": config.verbosity or "moderate",
        "emotion_tendency": config.emotion_tendency or "neutral",
        "model_override": config.model_override,
        "custom_system_prompt": config.custom_system_prompt,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }, message="角色AI配置已更新")


# ── Character Change Logs ───────────────────────────────────────────

@router.get("/projects/{project_id}/characters/change-logs")
def list_change_logs(
    project_id: str,
    chapter_id: Optional[str] = None,
    character_id: Optional[str] = None,
    confirmed: Optional[bool] = None,
    db: Session = Depends(get_db),
):
    """List character change logs, filterable by chapter/character/confirmed status."""
    get_project_or_404(db, project_id)
    query = (
        db.query(CharacterChangeLog)
        .join(Character, CharacterChangeLog.character_id == Character.id)
        .filter(Character.project_id == project_id)
    )
    if chapter_id:
        query = query.filter(CharacterChangeLog.chapter_id == chapter_id)
    if character_id:
        query = query.filter(CharacterChangeLog.character_id == character_id)
    if confirmed is not None:
        query = query.filter(CharacterChangeLog.confirmed == confirmed)

    logs = query.order_by(CharacterChangeLog.created_at.desc()).limit(200).all()

    items = []
    for log in logs:
        character = db.query(Character).filter(Character.id == log.character_id).first()
        chapter = db.query(Chapter).filter(Chapter.id == log.chapter_id).first()
        items.append({
            "id": log.id,
            "character_id": log.character_id,
            "character_name": character.name if character else "未知",
            "chapter_id": log.chapter_id,
            "chapter_title": chapter.title if chapter else "未知",
            "change_type": log.change_type,
            "field_name": log.field_name,
            "old_value": log.old_value,
            "new_value": log.new_value,
            "confirmed": log.confirmed,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.put("/projects/{project_id}/characters/change-logs/{log_id}/confirm")
def confirm_change_log(project_id: str, log_id: str, db: Session = Depends(get_db)):
    """Confirm a detected character change and apply it to the character."""
    get_project_or_404(db, project_id)
    log = (
        db.query(CharacterChangeLog)
        .join(Character, CharacterChangeLog.character_id == Character.id)
        .filter(CharacterChangeLog.id == log_id, Character.project_id == project_id)
        .first()
    )
    if not log:
        raise NotFoundError("变更记录不存在")
    if log.confirmed:
        raise ValidationError("该变更已确认")

    character = db.query(Character).filter(Character.id == log.character_id).first()
    if not character:
        raise NotFoundError("角色不存在")

    log.confirmed = True
    if apply_change_log_to_character(character, log):
        create_character_version(
            db,
            character,
            f"确认角色变化：{log.change_type}",
            source_chapter_id=log.chapter_id,
        )

    db.commit()
    return ApiResponse.success(message="变更已确认并应用")


@router.delete("/projects/{project_id}/characters/change-logs/{log_id}")
def reject_change_log(project_id: str, log_id: str, db: Session = Depends(get_db)):
    """Reject (delete) a detected character change."""
    get_project_or_404(db, project_id)
    log = (
        db.query(CharacterChangeLog)
        .join(Character, CharacterChangeLog.character_id == Character.id)
        .filter(CharacterChangeLog.id == log_id, Character.project_id == project_id)
        .first()
    )
    if not log:
        raise NotFoundError("变更记录不存在")
    if log.confirmed:
        raise ValidationError("已确认的变更不可删除，请通过角色编辑撤销")

    db.delete(log)
    db.commit()
    return ApiResponse.success(message="变更已拒绝")


@router.post("/projects/{project_id}/characters/change-logs/batch")
def batch_confirm_change_logs(
    project_id: str,
    chapter_id: Optional[str] = None,
    character_id: Optional[str] = None,
    action: str = Query("confirm", description="confirm or reject"),
    db: Session = Depends(get_db),
):
    """Batch confirm or reject all unconfirmed change logs matching the filters."""
    get_project_or_404(db, project_id)
    if action not in ("confirm", "reject"):
        raise ValidationError("action must be 'confirm' or 'reject'")

    query = (
        db.query(CharacterChangeLog)
        .join(Character, CharacterChangeLog.character_id == Character.id)
        .filter(Character.project_id == project_id, CharacterChangeLog.confirmed == False)
    )
    if chapter_id:
        query = query.filter(CharacterChangeLog.chapter_id == chapter_id)
    if character_id:
        query = query.filter(CharacterChangeLog.character_id == character_id)

    logs = query.all()
    if action == "confirm":
        for log in logs:
            log.confirmed = True
            character = db.query(Character).filter(Character.id == log.character_id).first()
            if character and apply_change_log_to_character(character, log):
                create_character_version(
                    db,
                    character,
                    f"确认角色变化：{log.change_type}",
                    source_chapter_id=log.chapter_id,
                )

    db.commit()
    return ApiResponse.success(message=f"已{ '确认' if action == 'confirm' else '拒绝' } {len(logs)} 条变更记录")
