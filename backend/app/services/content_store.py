"""Folder-backed source of truth for project creative content.

Moshu 2.x keeps operational state in SQLite, while chapters, outline,
characters, relationships, and worldbuilding are mirrored to human-readable
project folders. SQLite rows remain the fast index and compatibility layer.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..core.utils import count_words
from ..database.models import (
    Chapter,
    Character,
    CharacterAIConfig,
    CharacterRelationship,
    OutlineNodeCharacter,
    OutlineNode,
    Project,
    WorldbuildingEntry,
)
from .character_service import (
    character_aliases,
    dumps_list,
    loads_list,
    snapshot_character,
    sync_character_aliases,
)


STORE_VERSION = 1
MANIFEST_NAME = "moshu-project.json"
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def content_root() -> Path:
    configured = os.environ.get("MOSHU_CONTENT_ROOT")
    if configured:
        root = Path(configured).expanduser()
    else:
        home = os.environ.get("MOSHU_HOME") or os.environ.get("NOVEL_AGENT_HOME")
        if home:
            root = Path(home).expanduser() / "projects"
        else:
            root = Path.cwd() / "moshu-projects"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_name(value: str, fallback: str = "item", max_len: int = 80) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", str(value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .-")
    if not text:
        text = fallback
    return text[:max_len].strip(" .-") or fallback


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _rel(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def _write_text(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return _hash_text(text)


def _write_json(path: Path, data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    return _write_text(path, text + "\n")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def delete_project_file(project: Project, rel_path: str | None) -> None:
    """Delete a generated project-content file, constrained to the project folder."""
    if not project.folder_path or not rel_path:
        return
    folder = Path(project.folder_path).resolve()
    path = (folder / rel_path).resolve()
    try:
        path.relative_to(folder)
    except ValueError:
        return
    if path.exists() and path.is_file():
        path.unlink()


def delete_project_folder(project: Project) -> None:
    """Delete a project folder when the project itself is deleted."""
    if not project.folder_path:
        return
    folder = Path(project.folder_path).resolve()
    if folder.exists() and folder.is_dir():
        shutil.rmtree(folder)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def ensure_project_folder(db: Session, project: Project) -> Path:
    if project.folder_path:
        folder = Path(project.folder_path).expanduser()
    else:
        folder = content_root() / f"{_safe_name(project.title, 'project')}-{project.id}"
        project.folder_path = str(folder)
    project.storage_mode = "folder"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("chapters", "characters", "worldbuilding", "outline", "relationships", "outbox"):
        (folder / name).mkdir(parents=True, exist_ok=True)
    return folder.resolve()


def project_manifest(project: Project) -> dict[str, Any]:
    return {
        "store_version": STORE_VERSION,
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "tags": project.tags,
        "narrative_perspective": project.narrative_perspective,
        "writing_style": project.writing_style,
        "forbidden_sentence_patterns": project.forbidden_sentence_patterns,
        "rhetoric_guidelines": project.rhetoric_guidelines,
        "short_sentences": bool(project.short_sentences),
        "custom_style_prompt": project.custom_style_prompt,
        "daily_word_goal": project.daily_word_goal,
        "updated_at": datetime.utcnow().isoformat(),
    }


def write_project_manifest(db: Session, project: Project) -> None:
    folder = ensure_project_folder(db, project)
    _write_json(folder / MANIFEST_NAME, project_manifest(project))


def _chapter_path(folder: Path, chapter: Chapter, index: int = 0) -> Path:
    prefix = f"{index:04d}-" if index else ""
    name = f"{prefix}{_safe_name(chapter.title, 'chapter')}-{chapter.id}.md"
    return folder / "chapters" / name


def chapter_markdown(chapter: Chapter) -> str:
    meta = {
        "id": chapter.id,
        "project_id": chapter.project_id,
        "outline_node_id": chapter.outline_node_id,
        "title": chapter.title,
        "word_count": chapter.word_count or count_words(chapter.content or ""),
        "current_version": chapter.current_version or 1,
        "updated_at": chapter.updated_at.isoformat() if chapter.updated_at else None,
    }
    return "---\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\n---\n\n" + (chapter.content or "")


def parse_chapter_markdown(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, text or ""
    try:
        meta = json.loads(match.group(1))
    except json.JSONDecodeError:
        meta = {}
    return meta, text[match.end():]


def sync_chapter_to_file(db: Session, project: Project, chapter: Chapter, index: int = 0) -> None:
    folder = ensure_project_folder(db, project)
    old_rel = getattr(chapter, "content_file_path", None)
    path = folder / old_rel if old_rel else _chapter_path(folder, chapter, index)
    if old_rel and not path.exists():
        path = _chapter_path(folder, chapter, index)
    digest = _write_text(path, chapter_markdown(chapter))
    chapter.content_file_path = _rel(path, folder)
    chapter.content_hash = digest


def load_chapter_from_file(project: Project, chapter: Chapter) -> None:
    if not project.folder_path or not chapter.content_file_path:
        return
    path = Path(project.folder_path) / chapter.content_file_path
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    digest = _hash_text(text)
    if chapter.content_hash == digest:
        return
    meta, content = parse_chapter_markdown(text)
    chapter.title = str(meta.get("title") or chapter.title)
    chapter.outline_node_id = meta.get("outline_node_id") or chapter.outline_node_id
    chapter.content = content
    chapter.word_count = count_words(content)
    chapter.content_hash = digest


def character_payload(character: Character) -> dict[str, Any]:
    data = snapshot_character(character)
    data["aliases"] = character_aliases(character)
    if character.ai_config:
        data["ai_config"] = {
            "tone_style": character.ai_config.tone_style,
            "catchphrases": loads_list(character.ai_config.catchphrases),
            "verbosity": character.ai_config.verbosity,
            "emotion_tendency": character.ai_config.emotion_tendency,
            "model_override": character.ai_config.model_override,
            "custom_system_prompt": character.ai_config.custom_system_prompt,
        }
    return data


def sync_character_to_file(db: Session, project: Project, character: Character) -> None:
    folder = ensure_project_folder(db, project)
    path = folder / (character.content_file_path or f"characters/{_safe_name(character.name, 'character')}-{character.id}.json")
    digest = _write_json(path, character_payload(character))
    character.content_file_path = _rel(path, folder)
    character.content_hash = digest


def sync_worldbuilding_to_file(db: Session, project: Project, entry: WorldbuildingEntry) -> None:
    folder = ensure_project_folder(db, project)
    rel = entry.content_file_path or f"worldbuilding/{_safe_name(entry.dimension, 'misc')}/{_safe_name(entry.title, 'entry')}-{entry.id}.json"
    path = folder / rel
    payload = {
        "id": entry.id,
        "project_id": entry.project_id,
        "dimension": entry.dimension,
        "title": entry.title,
        "content": entry.content,
        "first_seen_chapter_id": entry.first_seen_chapter_id,
        "last_updated_chapter_id": entry.last_updated_chapter_id,
        "status": entry.status,
        "confidence": entry.confidence,
        "sort_order": entry.sort_order,
    }
    digest = _write_json(path, payload)
    entry.content_file_path = _rel(path, folder)
    entry.content_hash = digest


def outline_payload(nodes: list[OutlineNode]) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": node.id,
                "project_id": node.project_id,
                "parent_id": node.parent_id,
                "node_type": node.node_type,
                "title": node.title,
                "summary": node.summary,
                "status": node.status,
                "source_chapter_id": node.source_chapter_id,
                "actual_summary": node.actual_summary,
                "planned_summary": node.planned_summary,
                "cataloging_status": node.cataloging_status,
                "sort_order": node.sort_order,
                "linked_characters": [
                    {
                        "character_id": link.character_id,
                        "role_in_scene": link.role_in_scene,
                    }
                    for link in node.linked_characters
                ],
            }
            for node in nodes
        ]
    }


def sync_outline_to_file(db: Session, project: Project) -> None:
    folder = ensure_project_folder(db, project)
    nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project.id)
        .order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc())
        .all()
    )
    digest = _write_json(folder / "outline" / "outline.json", outline_payload(nodes))
    for node in nodes:
        node.content_file_path = "outline/outline.json"
        node.content_hash = digest


def sync_relationships_to_file(db: Session, project: Project) -> None:
    folder = ensure_project_folder(db, project)
    rows = (
        db.query(CharacterRelationship)
        .filter(CharacterRelationship.project_id == project.id)
        .order_by(CharacterRelationship.created_at.asc())
        .all()
    )
    _write_json(folder / "relationships" / "relationships.json", {
        "items": [
            {
                "id": row.id,
                "project_id": row.project_id,
                "character_a_id": row.character_a_id,
                "character_b_id": row.character_b_id,
                "relationship_type": row.relationship_type,
                "description": row.description,
            }
            for row in rows
        ]
    })


def sync_project_to_files(db: Session, project_id: str) -> None:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return
    folder = ensure_project_folder(db, project)
    write_project_manifest(db, project)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project.id)
        .order_by(Chapter.created_at.asc())
        .all()
    )
    for index, chapter in enumerate(chapters, start=1):
        sync_chapter_to_file(db, project, chapter, index=index)
    for character in db.query(Character).filter(Character.project_id == project.id).all():
        sync_character_to_file(db, project, character)
    for entry in db.query(WorldbuildingEntry).filter(WorldbuildingEntry.project_id == project.id).all():
        sync_worldbuilding_to_file(db, project, entry)
    sync_outline_to_file(db, project)
    sync_relationships_to_file(db, project)
    project.storage_mode = "folder"
    project.folder_path = str(folder)
    project.content_migrated_at = project.content_migrated_at or datetime.utcnow()


def refresh_project_from_files(db: Session, project_id: str) -> None:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or not project.folder_path:
        return
    folder = Path(project.folder_path)
    if not folder.exists():
        return

    chapters_by_id = {
        chapter.id: chapter
        for chapter in db.query(Chapter).filter(Chapter.project_id == project.id).all()
    }
    chapters_by_path = {
        str(chapter.content_file_path or ""): chapter
        for chapter in chapters_by_id.values()
        if chapter.content_file_path
    }
    for path in sorted((folder / "chapters").glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        meta, content = parse_chapter_markdown(text)
        rel_path = _rel(path, folder)
        chapter_id = str(meta.get("id") or "").strip()
        chapter = chapters_by_id.get(chapter_id) if chapter_id else None
        if not chapter:
            chapter = chapters_by_path.get(rel_path)
        if not chapter:
            chapter_data = dict(
                project_id=project.id,
                title=str(meta.get("title") or path.stem)[:200],
                content=content or "",
                word_count=count_words(content or ""),
                current_version=int(meta.get("current_version") or 1),
            )
            if chapter_id:
                chapter_data["id"] = chapter_id
            chapter = Chapter(**chapter_data)
            db.add(chapter)
            db.flush()
        chapter.title = str(meta.get("title") or chapter.title)[:200]
        chapter.outline_node_id = meta.get("outline_node_id") or chapter.outline_node_id
        chapter.content = content
        chapter.word_count = count_words(content)
        chapter.current_version = int(meta.get("current_version") or chapter.current_version or 1)
        chapter.content_file_path = rel_path
        chapter.content_hash = _hash_text(text)

    for path in (folder / "characters").glob("*.json"):
        try:
            data = _read_json(path)
        except Exception:
            continue
        character_id = data.get("id")
        if not character_id:
            continue
        character = db.query(Character).filter(Character.id == character_id, Character.project_id == project.id).first()
        if not character:
            character = Character(id=character_id, project_id=project.id, name=str(data.get("name") or "未命名角色"))
            db.add(character)
            db.flush()
        for field in (
            "name", "appearance", "personality", "background", "role_type", "age",
            "life_status", "current_location", "realm_or_level", "physical_state",
            "mental_state", "current_goal", "active_conflict", "abilities_state",
            "items_or_assets", "last_seen_chapter_id", "last_updated_chapter_id",
        ):
            if field in data:
                setattr(character, field, data.get(field))
        if "abilities" in data:
            character.abilities = dumps_list(data.get("abilities") or [])
        sync_character_aliases(db, character, data.get("aliases"))
        if isinstance(data.get("ai_config"), dict):
            cfg = character.ai_config or CharacterAIConfig(character_id=character.id)
            db.add(cfg)
            ai = data["ai_config"]
            cfg.tone_style = ai.get("tone_style") or cfg.tone_style
            cfg.catchphrases = dumps_list(ai.get("catchphrases") or [])
            cfg.verbosity = ai.get("verbosity") or cfg.verbosity
            cfg.emotion_tendency = ai.get("emotion_tendency") or cfg.emotion_tendency
            cfg.model_override = ai.get("model_override")
            cfg.custom_system_prompt = ai.get("custom_system_prompt")
        character.content_file_path = _rel(path, folder)
        character.content_hash = _hash_text(path.read_text(encoding="utf-8"))

    for path in (folder / "worldbuilding").glob("*/*.json"):
        try:
            data = _read_json(path)
        except Exception:
            continue
        entry_id = data.get("id")
        if not entry_id:
            continue
        entry = db.query(WorldbuildingEntry).filter(WorldbuildingEntry.id == entry_id, WorldbuildingEntry.project_id == project.id).first()
        if not entry:
            entry = WorldbuildingEntry(
                id=entry_id,
                project_id=project.id,
                dimension=str(data.get("dimension") or "culture"),
                title=str(data.get("title") or "未命名设定"),
                content=str(data.get("content") or ""),
            )
            db.add(entry)
        for field in ("dimension", "title", "content", "first_seen_chapter_id", "last_updated_chapter_id", "status", "confidence", "sort_order"):
            if field in data:
                setattr(entry, field, data.get(field))
        entry.content_file_path = _rel(path, folder)
        entry.content_hash = _hash_text(path.read_text(encoding="utf-8"))

    outline_path = folder / "outline" / "outline.json"
    if outline_path.exists():
        try:
            payload = _read_json(outline_path)
            items = payload.get("items") if isinstance(payload, dict) else None
        except Exception:
            items = None
        if isinstance(items, list):
            nodes_by_id = {
                node.id: node
                for node in db.query(OutlineNode).filter(OutlineNode.project_id == project.id).all()
            }
            touched_nodes: list[tuple[OutlineNode, dict[str, Any]]] = []
            digest = _hash_text(outline_path.read_text(encoding="utf-8"))
            for item in items:
                if not isinstance(item, dict):
                    continue
                node_id = str(item.get("id") or "").strip()
                if not node_id:
                    continue
                node = nodes_by_id.get(node_id)
                if not node:
                    node = OutlineNode(
                        id=node_id,
                        project_id=project.id,
                        node_type=str(item.get("node_type") or "chapter")[:20],
                        title=str(item.get("title") or "未命名大纲")[:200],
                    )
                    db.add(node)
                    nodes_by_id[node_id] = node
                for field in (
                    "parent_id", "node_type", "title", "summary", "status",
                    "source_chapter_id", "actual_summary", "planned_summary",
                    "cataloging_status", "sort_order",
                ):
                    if field in item:
                        setattr(node, field, item.get(field))
                node.content_file_path = "outline/outline.json"
                node.content_hash = digest
                touched_nodes.append((node, item))
            db.flush()
            for node, item in touched_nodes:
                db.query(OutlineNodeCharacter).filter(
                    OutlineNodeCharacter.outline_node_id == node.id
                ).delete()
                links = item.get("linked_characters")
                if not isinstance(links, list):
                    continue
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    character_id = str(link.get("character_id") or "").strip()
                    if not character_id:
                        continue
                    if not db.query(Character).filter(
                        Character.project_id == project.id,
                        Character.id == character_id,
                    ).first():
                        continue
                    db.add(OutlineNodeCharacter(
                        outline_node_id=node.id,
                        character_id=character_id,
                        role_in_scene=str(link.get("role_in_scene") or "")[:50] or None,
                    ))

    relationships_path = folder / "relationships" / "relationships.json"
    if relationships_path.exists():
        try:
            payload = _read_json(relationships_path)
            items = payload.get("items") if isinstance(payload, dict) else None
        except Exception:
            items = None
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                relationship_id = str(item.get("id") or "").strip()
                character_a_id = str(item.get("character_a_id") or "").strip()
                character_b_id = str(item.get("character_b_id") or "").strip()
                if not relationship_id or not character_a_id or not character_b_id:
                    continue
                if character_a_id == character_b_id:
                    continue
                rel = db.query(CharacterRelationship).filter(
                    CharacterRelationship.id == relationship_id,
                    CharacterRelationship.project_id == project.id,
                ).first()
                if not rel:
                    rel = CharacterRelationship(
                        id=relationship_id,
                        project_id=project.id,
                        character_a_id=character_a_id,
                        character_b_id=character_b_id,
                        relationship_type=str(item.get("relationship_type") or "关联")[:100],
                    )
                    db.add(rel)
                rel.character_a_id = character_a_id
                rel.character_b_id = character_b_id
                rel.relationship_type = str(item.get("relationship_type") or rel.relationship_type or "关联")[:100]
                rel.description = str(item.get("description") or "")[:4000] or None


def migrate_legacy_projects_to_files(db: Session) -> None:
    projects = db.query(Project).all()
    for project in projects:
        if project.storage_mode == "folder" and project.folder_path and (Path(project.folder_path) / MANIFEST_NAME).exists():
            continue
        sync_project_to_files(db, project.id)
    db.commit()


def migrate_projects_to_content_root(
    db: Session,
    target_root: str | Path,
    *,
    previous_root: str | Path | None = None,
    cleanup_old: bool = True,
) -> dict[str, Any]:
    """Move all project content folders under a new Moshu content root.

    Existing file-backed projects are refreshed into the DB first, then written
    into the target root. Old project folders are removed only when they are
    children of the previous content root.
    """
    target = Path(target_root).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    old_root = Path(previous_root).expanduser().resolve() if previous_root else content_root()
    projects = db.query(Project).all()
    migrated = 0
    cleaned = 0
    for project in projects:
        old_folder = Path(project.folder_path).expanduser().resolve() if project.folder_path else None
        if old_folder and old_folder.exists():
            refresh_project_from_files(db, project.id)
        project.folder_path = None
        project.storage_mode = "folder"
        sync_project_to_files(db, project.id)
        migrated += 1
        new_folder = Path(project.folder_path).expanduser().resolve() if project.folder_path else None
        if (
            cleanup_old
            and old_folder
            and old_folder.exists()
            and new_folder
            and old_folder != new_folder
            and _is_relative_to(old_folder, old_root)
            and not _is_relative_to(new_folder, old_folder)
        ):
            shutil.rmtree(old_folder)
            cleaned += 1
    return {
        "target_root": str(target),
        "previous_root": str(old_root),
        "migrated_projects": migrated,
        "cleaned_project_folders": cleaned,
    }
