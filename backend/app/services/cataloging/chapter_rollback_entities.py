"""Restore cataloging-mutated entities from durable apply logs."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.modules.continuity.infrastructure.models import (
    CatalogingApplyLog,
    CatalogingCandidate,
    CausalEdge,
    CharacterChangeLog,
    CharacterNarrativeState,
    CharacterTimeline,
    WorldbuildingTimeline,
    WorldbuildingVersion,
)
from app.modules.story.infrastructure.entities import (
    ChapterCharacter,
    ChapterWorldbuilding,
    Character,
    CharacterAIConfig,
    CharacterAlias,
    CharacterRelationship,
    CharacterVersion,
    OutlineNode,
    OutlineNodeCharacter,
    WorldbuildingEntry,
    WorldbuildingRelation,
)

from .chapter_rollback_common import (
    json_value,
    reset_chapter_outline_projection,
    same_projection,
)
from .snapshots import character_snapshot, outline_snapshot, worldbuilding_snapshot

_UNSUPPORTED = object()


def _restore_character(
    db: Session,
    character: Character,
    snapshot: dict[str, Any],
    affected_ids: set[str],
) -> None:
    for field in (
        "name",
        "appearance",
        "personality",
        "background",
        "role_type",
        "age",
        "life_status",
        "current_location",
        "realm_or_level",
        "physical_state",
        "mental_state",
        "current_goal",
        "active_conflict",
        "abilities_state",
        "items_or_assets",
    ):
        if field in snapshot:
            setattr(character, field, snapshot.get(field))

    abilities = snapshot.get("abilities")
    if isinstance(abilities, list):
        character.abilities = json.dumps(
            [str(item) for item in abilities], ensure_ascii=False
        )
    elif abilities is not None:
        character.abilities = str(abilities)

    profile = snapshot.get("profile")
    if isinstance(profile, dict):
        character.profile_json = dict(profile)

    desired_aliases = {
        str(item).strip()
        for item in (snapshot.get("aliases") or [])
        if str(item).strip() and str(item).strip() != character.name
    }
    existing = list(
        db.query(CharacterAlias)
        .filter(CharacterAlias.character_id == character.id)
        .all()
    )
    existing_by_name = {row.alias: row for row in existing}
    for row in existing:
        if row.alias not in desired_aliases and row.source_chapter_id in affected_ids:
            db.delete(row)
    for alias in desired_aliases:
        if alias not in existing_by_name:
            db.add(
                CharacterAlias(
                    project_id=character.project_id,
                    character_id=character.id,
                    alias=alias[:200],
                    alias_type="alias",
                )
            )

    ai_snapshot = snapshot.get("ai_config")
    config = character.ai_config or (
        db.query(CharacterAIConfig)
        .filter(CharacterAIConfig.character_id == character.id)
        .first()
    )
    if isinstance(ai_snapshot, dict):
        if config is None:
            config = CharacterAIConfig(character_id=character.id)
            db.add(config)
            character.ai_config = config
        config.tone_style = str(ai_snapshot.get("tone_style") or "neutral")[:100]
        config.catchphrases = json.dumps(
            [str(item) for item in (ai_snapshot.get("catchphrases") or [])],
            ensure_ascii=False,
        )
        config.verbosity = str(ai_snapshot.get("verbosity") or "moderate")[:50]
        config.emotion_tendency = str(
            ai_snapshot.get("emotion_tendency") or "neutral"
        )[:100]
        config.model_override = (
            str(ai_snapshot.get("model_override") or "")[:200] or None
        )
        config.custom_system_prompt = (
            str(ai_snapshot.get("custom_system_prompt") or "")[:12000] or None
        )
    elif config is not None:
        db.delete(config)
    character.updated_at = datetime.utcnow()


def _restore_worldbuilding(
    entry: WorldbuildingEntry,
    snapshot: dict[str, Any],
) -> None:
    for field in ("dimension", "title", "content", "status", "confidence"):
        if field in snapshot:
            setattr(entry, field, snapshot.get(field))
    entry.updated_at = datetime.utcnow()


def _restore_outline(node: OutlineNode, snapshot: dict[str, Any]) -> None:
    for field in (
        "title",
        "node_type",
        "parent_id",
        "summary",
        "status",
        "source_chapter_id",
        "actual_summary",
        "planned_summary",
        "cataloging_status",
    ):
        if field in snapshot:
            setattr(node, field, snapshot.get(field))
    if "cataloging_status" not in snapshot and not node.source_chapter_id:
        node.cataloging_status = None
    node.updated_at = datetime.utcnow()


def _relationship_snapshot(
    db: Session,
    row: CharacterRelationship,
) -> dict[str, Any]:
    source = db.get(Character, row.character_a_id)
    target = db.get(Character, row.character_b_id)
    return {
        "source_name": source.name if source else row.character_a_id,
        "target_name": target.name if target else row.character_b_id,
        "relationship_type": row.relationship_type,
        "description": row.description,
    }


def _timeline_snapshot(row: Any) -> dict[str, Any]:
    if isinstance(row, CharacterTimeline):
        return {
            "event_description": row.event_description,
            "event_type": row.event_type,
            "emotional_state_change": row.emotional_state_change,
            "sort_order": row.sort_order,
        }
    return {
        "event_description": row.event_description,
        "event_type": row.event_type,
        "evidence": row.evidence,
        "sort_order": row.sort_order,
    }


def _current_projection(
    db: Session,
    target_type: str,
    target_id: str,
    expected_new: Any,
) -> Any:
    if target_type == "character":
        if isinstance(expected_new, dict) and {
            "primary",
            "secondary",
        } <= set(expected_new):
            primary_id = str(
                (expected_new.get("primary") or {}).get("id") or target_id
            )
            secondary_id = str(
                (expected_new.get("secondary") or {}).get("id") or ""
            )
            primary = db.get(Character, primary_id)
            secondary = db.get(Character, secondary_id) if secondary_id else None
            return {
                "primary": character_snapshot(primary),
                "secondary": character_snapshot(secondary),
            }
        return character_snapshot(db.get(Character, target_id))
    if target_type == "worldbuilding":
        return worldbuilding_snapshot(db.get(WorldbuildingEntry, target_id))
    if target_type == "outline_node":
        return outline_snapshot(db.get(OutlineNode, target_id))
    if target_type == "character_relationship":
        row = db.get(CharacterRelationship, target_id)
        return _relationship_snapshot(db, row) if row else None
    if target_type in {"character_timeline", "worldbuilding_timeline"}:
        model = (
            CharacterTimeline
            if target_type == "character_timeline"
            else WorldbuildingTimeline
        )
        row = db.get(model, target_id)
        return _timeline_snapshot(row) if row else None
    return _UNSUPPORTED


def _character_has_external_ownership(
    db: Session,
    character: Character,
    affected_ids: set[str],
    affected_outline_ids: set[str],
    rollback_relationship_ids: set[str],
) -> bool:
    character_id = character.id
    if (
        db.query(ChapterCharacter)
        .filter(
            ChapterCharacter.character_id == character_id,
            ChapterCharacter.chapter_id.notin_(affected_ids),
        )
        .first()
    ):
        return True
    if (
        db.query(CharacterVersion)
        .filter(
            CharacterVersion.character_id == character_id,
            or_(
                CharacterVersion.source_chapter_id.is_(None),
                CharacterVersion.source_chapter_id.notin_(affected_ids),
            ),
        )
        .first()
    ):
        return True
    if (
        db.query(CharacterAlias)
        .filter(
            CharacterAlias.character_id == character_id,
            or_(
                CharacterAlias.source_chapter_id.is_(None),
                CharacterAlias.source_chapter_id.notin_(affected_ids),
            ),
        )
        .first()
    ):
        return True
    relationships = (
        db.query(CharacterRelationship)
        .filter(
            or_(
                CharacterRelationship.character_a_id == character_id,
                CharacterRelationship.character_b_id == character_id,
            )
        )
        .all()
    )
    if any(row.id not in rollback_relationship_ids for row in relationships):
        return True
    links = (
        db.query(OutlineNodeCharacter)
        .filter(OutlineNodeCharacter.character_id == character_id)
        .all()
    )
    if any(
        not (
            row.role_in_scene == "建档关联"
            and row.outline_node_id in affected_outline_ids
        )
        for row in links
    ):
        return True
    if (
        db.query(CharacterNarrativeState)
        .filter(
            CharacterNarrativeState.character_id == character_id,
            or_(
                CharacterNarrativeState.chapter_id.is_(None),
                CharacterNarrativeState.chapter_id.notin_(affected_ids),
            ),
        )
        .first()
    ):
        return True
    if (
        db.query(CharacterTimeline)
        .filter(
            CharacterTimeline.character_id == character_id,
            CharacterTimeline.chapter_id.notin_(affected_ids),
        )
        .first()
    ):
        return True
    for edge in (
        db.query(CausalEdge)
        .filter(CausalEdge.project_id == character.project_id)
        .all()
    ):
        if (
            character_id in {str(item) for item in (edge.character_ids or [])}
            and edge.source_chapter_id not in affected_ids
        ):
            return True
    return False


def _world_has_external_ownership(
    db: Session,
    entry: WorldbuildingEntry,
    affected_ids: set[str],
) -> bool:
    entry_id = entry.id
    if (
        db.query(ChapterWorldbuilding)
        .filter(
            ChapterWorldbuilding.worldbuilding_entry_id == entry_id,
            ChapterWorldbuilding.chapter_id.notin_(affected_ids),
        )
        .first()
    ):
        return True
    if (
        db.query(WorldbuildingVersion)
        .filter(
            WorldbuildingVersion.entry_id == entry_id,
            or_(
                WorldbuildingVersion.source_chapter_id.is_(None),
                WorldbuildingVersion.source_chapter_id.notin_(affected_ids),
            ),
        )
        .first()
    ):
        return True
    if (
        db.query(WorldbuildingTimeline)
        .filter(
            WorldbuildingTimeline.entry_id == entry_id,
            WorldbuildingTimeline.chapter_id.notin_(affected_ids),
        )
        .first()
    ):
        return True
    return bool(
        db.query(WorldbuildingRelation)
        .filter(
            or_(
                WorldbuildingRelation.source_entry_id == entry_id,
                WorldbuildingRelation.target_entry_id == entry_id,
            )
        )
        .first()
    )


def _undo_apply_log(
    db: Session,
    log: CatalogingApplyLog,
    candidate: CatalogingCandidate,
    old_value: Any,
    affected_ids: set[str],
    affected_outline_ids: set[str],
    preserved_outline_ids: set[str],
    rollback_relationship_ids: set[str],
    result: dict[str, Any],
) -> None:
    target_type = str(log.target_type or candidate.target_type or "")
    target_id = str(log.target_id or candidate.target_id or "")
    if not target_id:
        return

    if target_type == "character":
        if isinstance(old_value, dict) and {
            "primary",
            "secondary",
        } <= set(old_value):
            for key in ("primary", "secondary"):
                snapshot = old_value.get(key)
                if not isinstance(snapshot, dict):
                    continue
                character = db.get(Character, str(snapshot.get("id") or ""))
                if character:
                    _restore_character(db, character, snapshot, affected_ids)
                    result["restored_characters"] += 1
            result["warnings"].append(
                "角色合并已恢复角色卡字段；旧应用日志不含合并前全部关联归属，相关角色关系需复核"
            )
            return
        character = db.get(Character, target_id)
        if old_value is None:
            if character is None:
                return
            if _character_has_external_ownership(
                db,
                character,
                affected_ids,
                affected_outline_ids,
                rollback_relationship_ids,
            ):
                result["warnings"].append(
                    f"新角色 {character.name} 已被建档范围外的数据使用，未自动删除"
                )
                result["preserved_entities"].append(target_id)
                return
            db.delete(character)
            result["deleted_characters"] += 1
            result["deleted_character_ids"].append(target_id)
            return
        if character and isinstance(old_value, dict):
            _restore_character(db, character, old_value, affected_ids)
            result["restored_characters"] += 1
        return

    if target_type == "worldbuilding":
        entry = db.get(WorldbuildingEntry, target_id)
        if old_value is None:
            if entry is None:
                return
            if _world_has_external_ownership(db, entry, affected_ids):
                result["warnings"].append(
                    f"新世界观“{entry.title}”已被建档范围外的数据使用，未自动删除"
                )
                result["preserved_entities"].append(target_id)
                return
            db.delete(entry)
            result["deleted_worldbuilding"] += 1
            result["deleted_worldbuilding_ids"].append(target_id)
            return
        if entry and isinstance(old_value, dict):
            _restore_worldbuilding(entry, old_value)
            result["restored_worldbuilding"] += 1
        return

    if target_type == "character_relationship":
        row = db.get(CharacterRelationship, target_id)
        if old_value is None:
            if row:
                db.delete(row)
                result["deleted_relationships"] += 1
        elif row and isinstance(old_value, dict):
            row.relationship_type = str(
                old_value.get("relationship_type") or row.relationship_type
            )[:100]
            row.description = old_value.get("description")
            result["restored_relationships"] += 1
        return

    if target_type in {"character_timeline", "worldbuilding_timeline"}:
        model = (
            CharacterTimeline
            if target_type == "character_timeline"
            else WorldbuildingTimeline
        )
        row = db.get(model, target_id)
        if old_value is None:
            if row:
                db.delete(row)
        elif row and isinstance(old_value, dict):
            row.event_description = str(old_value.get("event_description") or "")
            row.event_type = str(
                old_value.get("event_type") or row.event_type
            )[:50]
            row.sort_order = int(old_value.get("sort_order") or 0)
            if isinstance(row, CharacterTimeline):
                row.emotional_state_change = old_value.get("emotional_state_change")
            else:
                row.evidence = old_value.get("evidence")
        result["restored_timeline_rows"] += 1
        return

    if target_type == "outline_node":
        node = db.get(OutlineNode, target_id)
        if old_value is None:
            if (
                node
                and node.source_chapter_id in affected_ids
                and node.cataloging_status == "cataloged"
            ):
                if node.id in preserved_outline_ids and node.node_type == "chapter":
                    reset_chapter_outline_projection(node)
                    result["preserved_entities"].append(node.id)
                else:
                    db.delete(node)
                    result["deleted_outline_nodes"] += 1
        elif node and isinstance(old_value, dict):
            _restore_outline(node, old_value)
            result["restored_outline_nodes"] += 1


def rollback_apply_logs(
    db: Session,
    project_id: str,
    affected_ids: set[str],
    affected_outline_ids: set[str],
    preserved_outline_ids: set[str],
    result: dict[str, Any],
) -> None:
    rows = (
        db.query(CatalogingApplyLog, CatalogingCandidate)
        .join(
            CatalogingCandidate,
            CatalogingCandidate.id == CatalogingApplyLog.candidate_id,
        )
        .filter(
            CatalogingCandidate.project_id == project_id,
            CatalogingCandidate.chapter_id.in_(affected_ids),
        )
        .order_by(CatalogingApplyLog.applied_at.asc(), CatalogingApplyLog.id.asc())
        .all()
    )
    groups: dict[
        tuple[str, str],
        list[tuple[CatalogingApplyLog, CatalogingCandidate]],
    ] = defaultdict(list)
    for log, candidate in rows:
        target_type = str(log.target_type or candidate.target_type or "")
        target_id = str(log.target_id or candidate.target_id or "")
        if target_type and target_id:
            groups[(target_type, target_id)].append((log, candidate))

    rollback_relationship_ids = {
        target_id
        for target_type, target_id in groups
        if target_type == "character_relationship"
    }
    priority = {
        "character_relationship": 0,
        "character_timeline": 0,
        "worldbuilding_timeline": 0,
        "outline_node": 1,
        "character": 2,
        "worldbuilding": 2,
    }
    for (target_type, target_id), group in sorted(
        groups.items(),
        key=lambda item: priority.get(item[0][0], 1),
    ):
        latest_log, _latest_candidate = group[-1]
        latest_new = json_value(latest_log.new_value)
        current = _current_projection(db, target_type, target_id, latest_new)
        if current is _UNSUPPORTED:
            continue
        if not same_projection(current, latest_new):
            result["warnings"].append(
                f"{target_type}:{target_id} 在建档后又被作者或其他流程修改，已保留当前值"
            )
            result["preserved_entities"].append(target_id)
            continue
        for log, candidate in reversed(group):
            _undo_apply_log(
                db,
                log,
                candidate,
                json_value(log.old_value),
                affected_ids,
                affected_outline_ids,
                preserved_outline_ids,
                rollback_relationship_ids,
                result,
            )
            result["rolled_back_apply_logs"] += 1


def rollback_legacy_character_changes(
    db: Session,
    affected_ids: set[str],
    result: dict[str, Any],
) -> None:
    rows = (
        db.query(CharacterChangeLog)
        .filter(
            CharacterChangeLog.chapter_id.in_(affected_ids),
            CharacterChangeLog.confirmed.is_(True),
        )
        .order_by(CharacterChangeLog.created_at.desc(), CharacterChangeLog.id.desc())
        .all()
    )
    supported = {"appearance", "personality", "background", "abilities"}
    for row in rows:
        if row.field_name not in supported:
            continue
        character = db.get(Character, row.character_id)
        if not character:
            continue
        current = getattr(character, row.field_name)
        if row.new_value is not None and str(current or "") != str(row.new_value or ""):
            continue
        setattr(character, row.field_name, row.old_value)
        result["legacy_character_changes_reverted"] += 1


__all__ = ["rollback_apply_logs", "rollback_legacy_character_changes"]
