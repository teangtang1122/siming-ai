"""Business logic for importing deconstruct report results into project entities."""
import json

from sqlalchemy.orm import Session

from ...core.db_helpers import get_project_or_404
from ...core.exceptions import ValidationError
from ...database.models import (
    Chapter,
    Character,
    CharacterAIConfig,
    CharacterRelationship,
    CharacterTimeline,
    CharacterVersion,
    ChapterCharacter,
    OutlineNode,
    OutlineNodeCharacter,
    WorldbuildingEntry,
)
from ...schemas.deconstruct import DeconstructImportRequest
from .constants import WORLD_DIMENSIONS
from .import_helpers import (
    arc_for_source_chapter,
    chapter_analyses_from_report,
    chapter_lookup,
    character_names_from_outline,
    character_snapshot,
    default_character_prompt,
    find_chapter_by_title,
    flatten_structure_chapters,
    get_or_create_outline_node,
    load_outline_lookup,
    merge_character_background,
    ordered_source_chapters,
    outline_summary,
    role_in_scene_for,
    summary_key_events,
    upsert_chapter_summary,
)
from .pipeline import safe_int
from .report_store import get_report_or_404, report_payload


def import_deconstruct_report(
    db: Session,
    project_id: str,
    report_id: str,
    payload: DeconstructImportRequest,
) -> dict:
    """Import outline nodes and/or characters extracted from a deconstruct report."""
    report = get_report_or_404(db, project_id, report_id)
    data = report_payload(report)
    if report.status != "completed":
        raise ValidationError("只能导入已完成的拆书报告")
    if not payload.import_outline and not payload.import_characters and not payload.import_worldbuilding:
        raise ValidationError("请选择要导入的大纲、角色或世界观")

    imported_outline = []
    imported_characters = []
    imported_worldbuilding = []
    imported_relationships = []
    imported_appearances = []
    imported_timeline_events = []
    imported_outline_links = []
    imported_chapter_summaries = []
    imported_chapter_outline_links = []
    imported_chapter_character_links = []

    existing_characters = {
        character.name: character
        for character in db.query(Character).filter(Character.project_id == project_id).all()
    }
    character_payloads = [
        item for item in (data.get("characters") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    lookup = chapter_lookup(db, project_id)
    source_chapters = ordered_source_chapters(db, project_id, data)
    fallback_chapter = next(iter(lookup.values()), None)
    existing_chapter_appearances = {
        (row.chapter_id, row.character_id, row.appearance_type, row.description or "")
        for row in db.query(ChapterCharacter)
        .join(Chapter, Chapter.id == ChapterCharacter.chapter_id)
        .filter(Chapter.project_id == project_id)
        .all()
    }
    outline_lookup = load_outline_lookup(db, project_id)
    existing_outline_links = {
        (row.outline_node_id, row.character_id)
        for row in db.query(OutlineNodeCharacter)
        .join(OutlineNode, OutlineNode.id == OutlineNodeCharacter.outline_node_id)
        .filter(OutlineNode.project_id == project_id)
        .all()
    }
    configured_character_ids = {
        character_id for (character_id,) in db.query(CharacterAIConfig.character_id).all()
    }

    # Import or enrich characters first so outline and relationships can link to them.
    if payload.import_characters:
        for char in character_payloads:
            name = str(char.get("name") or "").strip()
            if not name:
                continue
            role_type = str(char.get("role_type") or char.get("role") or "supporting")[:50]
            abilities = char.get("abilities") if isinstance(char.get("abilities"), list) else []
            background = merge_character_background(char)
            character = existing_characters.get(name)
            created = False
            if character:
                character.role_type = character.role_type or role_type
                character.appearance = character.appearance or str(char.get("appearance") or "") or None
                character.personality = character.personality or str(char.get("personality") or "") or None
                character.background = character.background or background or None
                character.abilities = character.abilities or (json.dumps(abilities, ensure_ascii=False) if abilities else None)
            else:
                character = Character(
                    project_id=project_id,
                    name=name[:100],
                    role_type=role_type,
                    appearance=str(char.get("appearance") or "") or None,
                    personality=str(char.get("personality") or "") or None,
                    background=background or None,
                    abilities=json.dumps(abilities, ensure_ascii=False) if abilities else None,
                    is_evolution_tracked=True,
                )
                db.add(character)
                db.flush()
                existing_characters[name] = character
                created = True

            ai_config_data = char.get("ai_config") if isinstance(char.get("ai_config"), dict) else {}
            prompt = str(ai_config_data.get("custom_system_prompt") or "").strip() or default_character_prompt(char)
            if character.id in configured_character_ids:
                ai_config = character.ai_config or db.query(CharacterAIConfig).filter(CharacterAIConfig.character_id == character.id).first()
                if ai_config:
                    ai_config.tone_style = str(ai_config_data.get("tone_style") or ai_config.tone_style or "neutral")[:100]
                    if ai_config_data.get("catchphrases"):
                        ai_config.catchphrases = json.dumps(ai_config_data.get("catchphrases") or [], ensure_ascii=False)
                    ai_config.verbosity = str(ai_config_data.get("verbosity") or ai_config.verbosity or "moderate")[:50]
                    ai_config.emotion_tendency = str(ai_config_data.get("emotion_tendency") or ai_config.emotion_tendency or "neutral")[:100]
                    ai_config.custom_system_prompt = ai_config.custom_system_prompt or prompt
            else:
                ai_config = CharacterAIConfig(
                    character_id=character.id,
                    tone_style=str(ai_config_data.get("tone_style") or "neutral")[:100],
                    catchphrases=json.dumps(ai_config_data.get("catchphrases") or [], ensure_ascii=False),
                    verbosity=str(ai_config_data.get("verbosity") or "moderate")[:50],
                    emotion_tendency=str(ai_config_data.get("emotion_tendency") or "neutral")[:100],
                    custom_system_prompt=prompt,
                )
                character.ai_config = ai_config
                db.add(ai_config)
                configured_character_ids.add(character.id)

            snapshot = CharacterVersion(
                character_id=character.id,
                version_number=(character.current_version or 1),
                snapshot_data=json.dumps(character_snapshot(character, char), ensure_ascii=False),
                change_summary="由拆书结果导入/补全角色档案",
            )
            db.add(snapshot)

            imported_characters.append({
                "id": character.id,
                "name": character.name,
                "role_type": character.role_type,
                "created": created,
            })

        db.flush()

        existing_relationships = {
            (row.character_a_id, row.character_b_id, row.relationship_type)
            for row in db.query(CharacterRelationship).filter(CharacterRelationship.project_id == project_id).all()
        }
        existing_chapter_appearances = {
            (row.chapter_id, row.character_id, row.appearance_type, row.description or "")
            for row in db.query(ChapterCharacter)
            .join(Chapter, Chapter.id == ChapterCharacter.chapter_id)
            .filter(Chapter.project_id == project_id)
            .all()
        }
        for char in character_payloads:
            name = str(char.get("name") or "").strip()
            character = existing_characters.get(name)
            if not character:
                continue
            relationships = char.get("relationship_network") or char.get("relationships") or []
            for rel in relationships:
                if not isinstance(rel, dict):
                    continue
                target_name = str(rel.get("target_name") or "").strip()
                target = existing_characters.get(target_name)
                if not target or target.id == character.id:
                    continue
                relationship_type = str(rel.get("relationship_type") or "related")[:100]
                key = (character.id, target.id, relationship_type)
                if key in existing_relationships:
                    continue
                description = str(rel.get("description") or rel.get("evidence") or "").strip()
                if rel.get("attitude"):
                    description = f"{description}\n态度：{rel.get('attitude')}".strip()
                db.add(CharacterRelationship(
                    project_id=project_id,
                    character_a_id=character.id,
                    character_b_id=target.id,
                    relationship_type=relationship_type,
                    description=description or None,
                ))
                existing_relationships.add(key)
                imported_relationships.append({"from": name, "to": target.name, "type": relationship_type})

            for record in char.get("appearance_records") or []:
                if not isinstance(record, dict):
                    continue
                chapter = find_chapter_by_title(lookup, record.get("chapter_title"))
                description = str(record.get("summary") or record.get("scene") or "").strip()
                role_in_scene = str(record.get("role_in_scene") or "出场")[:50]
                if chapter and description:
                    key = (chapter.id, character.id, role_in_scene, description)
                    if key not in existing_chapter_appearances:
                        db.add(ChapterCharacter(
                            chapter_id=chapter.id,
                            character_id=character.id,
                            appearance_type=role_in_scene,
                            description=description,
                        ))
                        existing_chapter_appearances.add(key)
                        imported_appearances.append({"character": name, "chapter": chapter.title})
                timeline_chapter = chapter or fallback_chapter
                if description and timeline_chapter:
                    db.add(CharacterTimeline(
                        character_id=character.id,
                        chapter_id=timeline_chapter.id,
                        event_description=description,
                        event_type="key_decision",
                        emotional_state_change=str(record.get("scene") or "")[:500] or None,
                        sort_order=safe_int(record.get("source_chunk")),
                    ))
                    imported_timeline_events.append({"character": name, "event": description[:80]})

            for event in char.get("timeline_events") or []:
                if not isinstance(event, dict):
                    continue
                description = str(event.get("description") or "").strip()
                timeline_chapter = find_chapter_by_title(lookup, event.get("chapter_title")) or fallback_chapter
                if not description or not timeline_chapter:
                    continue
                db.add(CharacterTimeline(
                    character_id=character.id,
                    chapter_id=timeline_chapter.id,
                    event_description=description,
                    event_type=str(event.get("event_type") or "other")[:50],
                    emotional_state_change=str(event.get("emotional_state_change") or "")[:1000] or None,
                    sort_order=safe_int(event.get("source_chunk")),
                ))
                imported_timeline_events.append({"character": name, "event": description[:80]})

    if payload.import_outline:
        structure = data.get("structure") or {}
        volumes = structure.get("volumes") or []
        volume_nodes: list = []
        broad_chapter_nodes: list[tuple[dict, any]] = []
        chapter_global_index = 0
        for volume_index, volume in enumerate(volumes):
            volume_title = str(volume.get("title") or f"拆书卷 {volume_index + 1}").strip()
            volume_node, volume_created = get_or_create_outline_node(
                db,
                project_id,
                outline_lookup,
                "volume",
                volume_title,
                None,
                outline_summary(volume)[:10000] or None,
                volume_index,
            )
            volume_nodes.append(volume_node)
            if volume_created:
                imported_outline.append({"id": volume_node.id, "title": volume_node.title, "node_type": volume_node.node_type})

            for character_name in character_names_from_outline(volume):
                character = existing_characters.get(character_name)
                if character and (volume_node.id, character.id) not in existing_outline_links:
                    db.add(OutlineNodeCharacter(
                        outline_node_id=volume_node.id,
                        character_id=character.id,
                        role_in_scene=role_in_scene_for(character_name, volume),
                    ))
                    existing_outline_links.add((volume_node.id, character.id))
                    imported_outline_links.append({"outline": volume_node.title, "character": character.name})

            for chapter_index, chapter in enumerate(volume.get("chapters") or []):
                chapter_title = str(chapter.get("title") or f"拆书章节 {chapter_index + 1}").strip()
                chapter_node, chapter_created = get_or_create_outline_node(
                    db,
                    project_id,
                    outline_lookup,
                    "chapter",
                    chapter_title,
                    volume_node.id,
                    outline_summary(chapter)[:20000] or None,
                    chapter_index,
                )
                broad_chapter_nodes.append((chapter, chapter_node))
                if chapter_created:
                    imported_outline.append({"id": chapter_node.id, "title": chapter_node.title, "node_type": chapter_node.node_type})
                matched_chapter = (
                    find_chapter_by_title(lookup, chapter.get("source_title") or chapter_title)
                    or (source_chapters[chapter_global_index] if chapter_global_index < len(source_chapters) else None)
                )
                summary_text = outline_summary(chapter)
                key_events = summary_key_events(chapter)
                if matched_chapter:
                    if matched_chapter.outline_node_id != chapter_node.id:
                        matched_chapter.outline_node_id = chapter_node.id
                        imported_chapter_outline_links.append({
                            "chapter": matched_chapter.title,
                            "outline": chapter_node.title,
                        })
                    if summary_text:
                        upsert_chapter_summary(
                            db,
                            matched_chapter,
                            summary_text,
                            key_events,
                            data.get("reduce_model") or data.get("model"),
                        )
                        imported_chapter_summaries.append({
                            "chapter": matched_chapter.title,
                            "outline": chapter_node.title,
                        })

                for character_name in character_names_from_outline(chapter):
                    character = existing_characters.get(character_name)
                    if character:
                        role_in_scene = role_in_scene_for(character_name, chapter)
                        if (chapter_node.id, character.id) not in existing_outline_links:
                            db.add(OutlineNodeCharacter(
                                outline_node_id=chapter_node.id,
                                character_id=character.id,
                                role_in_scene=role_in_scene,
                            ))
                            existing_outline_links.add((chapter_node.id, character.id))
                            imported_outline_links.append({"outline": chapter_node.title, "character": character.name})
                        if matched_chapter:
                            appearance_type = role_in_scene or "涉及"
                            description = summary_text[:1000] or f"由拆书大纲《{chapter_node.title}》识别为本章涉及角色"
                            key = (matched_chapter.id, character.id, appearance_type, description)
                            if key not in existing_chapter_appearances:
                                db.add(ChapterCharacter(
                                    chapter_id=matched_chapter.id,
                                    character_id=character.id,
                                    appearance_type=appearance_type,
                                    description=description,
                                ))
                                existing_chapter_appearances.add(key)
                                imported_chapter_character_links.append({
                                    "chapter": matched_chapter.title,
                                    "character": character.name,
                                })
                chapter_global_index += 1

        arcs = []
        broad_by_item_id = {id(item): node for item, node in broad_chapter_nodes}
        for arc in flatten_structure_chapters(structure, volume_nodes):
            node = broad_by_item_id.get(id(arc["item"]))
            if node:
                arc["node"] = node
                arcs.append(arc)

        for analysis in chapter_analyses_from_report(db, project_id, data):
            chapter = analysis["chapter"]
            arc = arc_for_source_chapter(arcs, analysis["start_chunk"])
            parent_node = arc.get("node") if arc else (volume_nodes[0] if volume_nodes else None)
            parent_id = parent_node.id if parent_node else None
            detail_node, detail_created = get_or_create_outline_node(
                db,
                project_id,
                outline_lookup,
                "section" if parent_id else "chapter",
                chapter.title,
                parent_id,
                analysis["summary"],
                analysis["source_index"],
            )
            if detail_created:
                imported_outline.append({"id": detail_node.id, "title": detail_node.title, "node_type": detail_node.node_type})
            if chapter.outline_node_id != detail_node.id:
                chapter.outline_node_id = detail_node.id
                imported_chapter_outline_links.append({
                    "chapter": chapter.title,
                    "outline": detail_node.title,
                })
            upsert_chapter_summary(
                db,
                chapter,
                analysis["summary"],
                analysis["key_events"],
                data.get("map_model") or data.get("reduce_model") or data.get("model"),
            )
            imported_chapter_summaries.append({
                "chapter": chapter.title,
                "outline": detail_node.title,
            })
            for character_name in analysis["characters"]:
                character = existing_characters.get(character_name)
                if not character:
                    continue
                if (detail_node.id, character.id) not in existing_outline_links:
                    db.add(OutlineNodeCharacter(
                        outline_node_id=detail_node.id,
                        character_id=character.id,
                        role_in_scene="涉及",
                    ))
                    existing_outline_links.add((detail_node.id, character.id))
                    imported_outline_links.append({"outline": detail_node.title, "character": character.name})
                description = analysis["summary"][:1000]
                key = (chapter.id, character.id, "涉及", description)
                if key not in existing_chapter_appearances:
                    db.add(ChapterCharacter(
                        chapter_id=chapter.id,
                        character_id=character.id,
                        appearance_type="涉及",
                        description=description,
                    ))
                    existing_chapter_appearances.add(key)
                    imported_chapter_character_links.append({
                        "chapter": chapter.title,
                        "character": character.name,
                    })

    if payload.import_worldbuilding:
        existing_world_titles = {
            (dimension, title)
            for dimension, title in db.query(WorldbuildingEntry.dimension, WorldbuildingEntry.title)
            .filter(WorldbuildingEntry.project_id == project_id)
            .all()
        }
        grouped_counts: dict[str, int] = {}
        for dimension, in db.query(WorldbuildingEntry.dimension).filter(WorldbuildingEntry.project_id == project_id).all():
            grouped_counts[dimension] = grouped_counts.get(dimension, 0) + 1

        entries = data.get("worldbuilding_entries") or data.get("worldbuilding") or []
        for item in entries:
            if not isinstance(item, dict):
                continue
            dimension = str(item.get("dimension") or "culture").strip()
            if dimension not in WORLD_DIMENSIONS:
                dimension = "culture"
            title = str(item.get("title") or "").strip()
            content = str(item.get("content") or "").strip()
            if item.get("constraints"):
                constraints = "；".join(str(value) for value in item.get("constraints") or [] if value)
                if constraints:
                    content = f"{content}\n\n限制与规则：{constraints}".strip()
            if item.get("plot_usage"):
                content = f"{content}\n\n剧情用途：{item.get('plot_usage')}".strip()
            if not title or not content or (dimension, title) in existing_world_titles:
                continue
            world_entry = WorldbuildingEntry(
                project_id=project_id,
                dimension=dimension,
                title=title[:200],
                content=content,
                sort_order=grouped_counts.get(dimension, 0),
            )
            db.add(world_entry)
            db.flush()
            grouped_counts[dimension] = grouped_counts.get(dimension, 0) + 1
            existing_world_titles.add((dimension, title))
            imported_worldbuilding.append({
                "id": world_entry.id,
                "dimension": world_entry.dimension,
                "title": world_entry.title,
            })

    db.commit()
    return {
        "outline_nodes": imported_outline,
        "characters": imported_characters,
        "relationships": imported_relationships,
        "appearance_records": imported_appearances,
        "timeline_events": imported_timeline_events,
        "outline_character_links": imported_outline_links,
        "chapter_summaries": imported_chapter_summaries,
        "chapter_outline_links": imported_chapter_outline_links,
        "chapter_character_links": imported_chapter_character_links,
        "worldbuilding_entries": imported_worldbuilding,
        "outline_count": len(imported_outline),
        "character_count": len(imported_characters),
        "relationship_count": len(imported_relationships),
        "appearance_count": len(imported_appearances),
        "timeline_count": len(imported_timeline_events),
        "outline_character_link_count": len(imported_outline_links),
        "chapter_summary_count": len(imported_chapter_summaries),
        "chapter_outline_link_count": len(imported_chapter_outline_links),
        "chapter_character_link_count": len(imported_chapter_character_links),
        "worldbuilding_count": len(imported_worldbuilding),
    }
