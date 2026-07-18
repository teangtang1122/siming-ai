"""Character CRUD, version history, relationship network, and AI suggestion endpoints."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..core.db_helpers import get_character_or_404, get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.session import get_db
from ..modules.story.application.commands import StoryCommandContext
from ..modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ..modules.story.interfaces.dependencies import get_story_command
from ..modules.story.interfaces.character_dependencies import character_workspace
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
    characters = character_workspace(db).list_characters(project_id, q)
    items = [character_to_dict(character) for character in characters]
    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.post("/projects/{project_id}/characters")
def create_character(
    project_id: str,
    payload: CharacterCreate,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Create a character."""
    db = command.session
    get_project_or_404(db, project_id)
    character = character_workspace(db).create_character(
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
        profile_json=payload.profile,
        is_evolution_tracked=payload.is_evolution_tracked,
    )
    db.flush()
    sync_character_aliases(db, character, payload.aliases)
    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.CHARACTER,
            entity_id=character.id,
        ),
    )
    command.finish()
    db.refresh(character)
    return ApiResponse.success(data=character_to_dict(character), message="角色创建成功")


@router.get("/projects/{project_id}/characters/relationships")
def get_relationship_network(project_id: str, db: Session = Depends(get_db)):
    """Get all character relationship network data for a project."""
    get_project_or_404(db, project_id)
    characters, relationships = character_workspace(db).relationship_network(project_id)
    visible_ids = {character.id for character in characters}
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
    command: StoryCommandContext = Depends(get_story_command),
):
    """Merge a duplicate character into a primary character card."""
    db = command.session
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
    for character_id in (payload.primary_id, payload.secondary_id):
        command.queue(
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHARACTER,
                entity_id=character_id,
            ),
        )
    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.CHARACTER_RELATIONSHIPS,
        ),
    )
    command.finish()
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
    command: StoryCommandContext = Depends(get_story_command),
):
    """Update character fields and create a version snapshot."""
    db = command.session
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
        elif field == "profile":
            character.profile_json = value
        else:
            setattr(character, field, value)

    character.current_version = (character.current_version or 1) + 1
    db.flush()
    character_workspace(db).create_version(
        character_id=character.id,
        version_number=character.current_version,
        snapshot_data=json.dumps(snapshot_character(character), ensure_ascii=False),
        change_summary=change_summary or "手动更新角色档案",
    )
    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.CHARACTER,
            entity_id=character.id,
        ),
    )
    command.finish()
    db.refresh(character)
    return ApiResponse.success(data=character_to_dict(character), message="角色更新成功")


@router.delete("/projects/{project_id}/characters/{character_id}")
def delete_character(
    project_id: str,
    character_id: str,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Delete a character and its relationships."""
    db = command.session
    project = get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    content_file_path = character.content_file_path
    workspace = character_workspace(db)
    workspace.delete_relationships(project_id, character.id)
    workspace.delete(character)
    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.FILE_DELETE,
            entity_id=character_id,
            payload={
                "folder_path": project.folder_path,
                "relative_path": content_file_path,
            },
        ),
    )
    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.CHARACTER_RELATIONSHIPS,
        ),
    )
    command.finish()
    return ApiResponse.success(message="角色已删除")


@router.get("/projects/{project_id}/characters/{character_id}/versions")
def list_character_versions(project_id: str, character_id: str, db: Session = Depends(get_db)):
    """Get character version history."""
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    versions = character_workspace(db).versions(character.id)
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
    version = character_workspace(db).version(character.id, version_id)
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
    command: StoryCommandContext = Depends(get_story_command),
):
    """Replace all relationships connected to the current character."""
    db = command.session
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    target_ids = {item.target_character_id for item in payload.relationships}
    if character.id in target_ids:
        raise ValidationError("角色不能与自身建立关系")

    if target_ids:
        if not character_workspace(db).targets_exist(project_id, target_ids):
            raise ValidationError("关系目标角色必须属于当前作品")

    character_workspace(db).replace_relationships(
        project_id,
        character.id,
        payload.relationships,
    )

    command.queue(
        ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.CHARACTER_RELATIONSHIPS,
        ),
    )
    command.finish()
    return get_relationship_network(project_id, db)


@router.get("/projects/{project_id}/characters/{character_id}/ai-config")
def get_character_ai_config(
    project_id: str,
    character_id: str,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Get a character's AI dialogue configuration."""
    db = command.session
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    config = character_workspace(db).ensure_ai_config(character)
    if config.id is None:
        command.finish()
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
    command: StoryCommandContext = Depends(get_story_command),
):
    """Update a character's AI dialogue configuration."""
    db = command.session
    get_project_or_404(db, project_id)
    character = get_character_or_404(db, project_id, character_id)
    config = character_workspace(db).ensure_ai_config(character)
    if config.id is None:
        db.flush()

    update_data = payload.model_dump(exclude_unset=True)
    if "catchphrases" in update_data:
        config.catchphrases = dumps_list(update_data.pop("catchphrases"))
    for field, value in update_data.items():
        setattr(config, field, value)

    command.finish()
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
    workspace = character_workspace(db)
    logs = workspace.change_logs(
        project_id,
        chapter_id=chapter_id,
        character_id=character_id,
        confirmed=confirmed,
        limit=200,
    )
    items = workspace.serialize_change_logs(logs)

    return ApiResponse.success(data={"items": items, "total": len(items)})


@router.put("/projects/{project_id}/characters/change-logs/{log_id}/confirm")
def confirm_change_log(
    project_id: str,
    log_id: str,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Confirm a detected character change and apply it to the character."""
    db = command.session
    get_project_or_404(db, project_id)
    workspace = character_workspace(db)
    log = workspace.change_log(project_id, log_id)
    if not log:
        raise NotFoundError("变更记录不存在")
    if log.confirmed:
        raise ValidationError("该变更已确认")

    character = workspace.character(log.character_id)
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
        command.queue(
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHARACTER,
                entity_id=character.id,
            ),
        )

    command.finish()
    return ApiResponse.success(message="变更已确认并应用")


@router.delete("/projects/{project_id}/characters/change-logs/{log_id}")
def reject_change_log(
    project_id: str,
    log_id: str,
    command: StoryCommandContext = Depends(get_story_command),
):
    """Reject (delete) a detected character change."""
    db = command.session
    get_project_or_404(db, project_id)
    workspace = character_workspace(db)
    log = workspace.change_log(project_id, log_id)
    if not log:
        raise NotFoundError("变更记录不存在")
    if log.confirmed:
        raise ValidationError("已确认的变更不可删除，请通过角色编辑撤销")

    workspace.delete(log)
    command.finish()
    return ApiResponse.success(message="变更已拒绝")


@router.post("/projects/{project_id}/characters/change-logs/batch")
def batch_confirm_change_logs(
    project_id: str,
    chapter_id: Optional[str] = None,
    character_id: Optional[str] = None,
    action: str = Query("confirm", description="confirm or reject"),
    command: StoryCommandContext = Depends(get_story_command),
):
    """Batch confirm or reject all unconfirmed change logs matching the filters."""
    db = command.session
    get_project_or_404(db, project_id)
    if action not in ("confirm", "reject"):
        raise ValidationError("action must be 'confirm' or 'reject'")

    workspace = character_workspace(db)
    logs = workspace.pending_change_logs(
        project_id,
        chapter_id=chapter_id,
        character_id=character_id,
    )
    changed_character_ids: set[str] = set()
    if action == "confirm":
        for log in logs:
            log.confirmed = True
            character = workspace.character(log.character_id)
            if character and apply_change_log_to_character(character, log):
                changed_character_ids.add(character.id)
                create_character_version(
                    db,
                    character,
                    f"确认角色变化：{log.change_type}",
                    source_chapter_id=log.chapter_id,
                )

    for changed_character_id in changed_character_ids:
        command.queue(
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHARACTER,
                entity_id=changed_character_id,
            ),
        )
    command.finish()
    return ApiResponse.success(message=f"已{ '确认' if action == 'confirm' else '拒绝' } {len(logs)} 条变更记录")
