"""Shared story granularity contract for cataloging and post-write archiving."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy.orm import Session

from ..database.models import CatalogingFact, Chapter, ChapterCharacter, Character, ChapterWorldbuilding, OutlineNode


CHARACTER_STATE_FIELDS: tuple[str, ...] = (
    "appearance",
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
)

CHARACTER_STABLE_FIELDS: tuple[str, ...] = (
    "name",
    "aliases",
    "role_type",
    "personality",
    "background",
    "abilities",
    "tone_style",
    "catchphrases",
    "emotion_tendency",
    "custom_system_prompt",
)

OUTLINE_NODE_TYPES: set[str] = {"volume", "chapter", "section"}
WORLD_DIMENSIONS: set[str] = {"geography", "history", "factions", "power_system", "races", "culture"}

VALID_CANDIDATE_TYPES: set[str] = {
    "chapter_summary",
    "outline_create",
    "outline_update",
    "character_create",
    "character_update",
    "character_state_update",
    "character_timeline",
    "character_relationship",
    "character_merge_candidate",
    "worldbuilding_create",
    "worldbuilding_update",
    "worldbuilding_timeline",
    "chapter_link",
}

NARRATIVE_STATE_FIELDS: tuple[str, ...] = (
    "events",
    "timeline_events",
    "foreshadowing_planted",
    "foreshadowing_resolved",
    "storyline_progress",
    "new_storylines",
    "reader_known_facts",
    "character_known_facts",
    "unresolved_actions",
    "character_actions",
    "relationship_changes",
)

SECTION_SCENE_STATE_FIELDS: tuple[str, ...] = (
    "scene_number",
    "purpose",
    "location",
    "timeline",
    "pov_character",
    "characters",
    "entry_state",
    "exit_state",
    "emotional_residue",
    "unresolved_actions",
)

PLOTPILOT_NARRATIVE_ALIASES: dict[str, tuple[str, ...]] = {
    "events": ("events", "chapter_events", "key_events"),
    "timeline_events": ("timeline_events", "timeline"),
    "foreshadowing_planted": ("foreshadowing_planted", "planted_foreshadowing", "new_foreshadowing"),
    "foreshadowing_resolved": ("foreshadowing_resolved", "resolved_foreshadowing"),
    "storyline_progress": ("storyline_progress", "advanced_storylines", "progressed_storylines"),
    "new_storylines": ("new_storylines",),
    "reader_known_facts": ("reader_known_facts", "revealed_facts", "facts_reader_known"),
    "character_known_facts": ("character_known_facts", "facts_character_known"),
    "unresolved_actions": ("unresolved_actions", "open_actions", "pending_actions"),
    "character_actions": ("character_actions",),
    "relationship_changes": ("relationship_changes",),
}

_CHAPTER_NUMBER_RE = re.compile(r"(?:第\s*)?(\d{1,5})\s*章")


@dataclass(frozen=True)
class CandidateCoverage:
    total: int
    has_chapter_summary: bool
    has_chapter_outline: bool
    section_count: int = 0
    scene_count: int = 1
    character_state_count: int = 0
    scene_state_count: int = 0
    event_count: int = 0
    foreshadowing_planted_count: int = 0
    foreshadowing_resolved_count: int = 0
    storyline_progress_count: int = 0
    unresolved_action_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.has_chapter_summary and self.has_chapter_outline

    @property
    def missing(self) -> list[str]:
        missing: list[str] = []
        if not self.has_chapter_summary:
            missing.append("chapter_summary")
        if not self.has_chapter_outline:
            missing.append("chapter-level outline")
        return missing

    @property
    def needs_section_warning(self) -> bool:
        return self.scene_count > 1 and self.section_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "has_chapter_summary": self.has_chapter_summary,
            "has_chapter_outline": self.has_chapter_outline,
            "section_count": self.section_count,
            "scene_count": self.scene_count,
            "character_state_count": self.character_state_count,
            "scene_state_count": self.scene_state_count,
            "event_count": self.event_count,
            "foreshadowing_planted_count": self.foreshadowing_planted_count,
            "foreshadowing_resolved_count": self.foreshadowing_resolved_count,
            "storyline_progress_count": self.storyline_progress_count,
            "unresolved_action_count": self.unresolved_action_count,
            "is_complete": self.is_complete,
            "missing": self.missing,
            "warnings": list(self.warnings),
        }


def extract_chapter_number(*texts: Any) -> int | None:
    for text in texts:
        value = str(text or "")
        match = _CHAPTER_NUMBER_RE.search(value)
        if match:
            return int(match.group(1))
    return None


def normalize_node_type(value: Any) -> str:
    node_type = str(value or "chapter").strip().lower()
    if node_type == "scene":
        node_type = "section"
    return node_type if node_type in OUTLINE_NODE_TYPES else "chapter"


def title_has_chapter_number(title: Any, chapter_number: int | None) -> bool:
    if not chapter_number:
        return True
    return bool(re.search(rf"(第\s*{chapter_number}\s*章|chapter\s*{chapter_number}|{chapter_number}\s*章)", str(title or ""), re.I))


def normalize_outline_payload(
    payload: dict[str, Any],
    *,
    chapter_number: int | None = None,
    default_chapter_title: str = "",
    title_remap: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Normalize one outline payload to the shared chapter/section contract."""
    item = dict(payload)
    node_type = normalize_node_type(item.get("node_type"))
    item["node_type"] = node_type
    title = str(item.get("title") or item.get("outline_title") or "").strip()
    old_title = title
    if chapter_number and node_type == "chapter" and title and not title_has_chapter_number(title, chapter_number):
        title = f"第{chapter_number}章 {title}"
    if not title and chapter_number:
        title = f"第{chapter_number}章"
    if title:
        item["title"] = title

    remap = title_remap if title_remap is not None else {}
    if old_title and title and old_title != title:
        remap[old_title] = title

    summary = str(item.get("summary") or item.get("actual_summary") or item.get("description") or "").strip()
    if summary:
        item["summary"] = summary
        item["actual_summary"] = str(item.get("actual_summary") or summary)
    if "planned_summary" not in item:
        item["planned_summary"] = ""

    if node_type == "section":
        parent_title = str(item.get("parent_title") or default_chapter_title or "").strip()
        raw_parent_title = parent_title
        if parent_title in remap:
            parent_title = remap[parent_title]
        if parent_title:
            item["parent_title"] = parent_title
            section_title = str(item.get("title") or "").strip()
            if section_title and not section_title.startswith(parent_title):
                if raw_parent_title and raw_parent_title != parent_title and section_title.startswith(raw_parent_title):
                    suffix = section_title[len(raw_parent_title):].lstrip(" /")
                    section_title = f"{parent_title} / {suffix}" if suffix else parent_title
                elif old_title and old_title in remap and section_title.startswith(old_title):
                    suffix = section_title[len(old_title):].lstrip(" /")
                    section_title = f"{parent_title} / {suffix}" if suffix else parent_title
                elif not title_has_chapter_number(section_title, chapter_number):
                    section_title = f"{parent_title} / {section_title}"
                item["title"] = section_title
    return item


def normalize_outline_batch(nodes: Iterable[dict[str, Any]], *, chapter_number: int | None = None) -> list[dict[str, Any]]:
    title_remap: dict[str, str] = {}
    normalized: list[dict[str, Any]] = []
    default_chapter_title = ""
    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        item = normalize_outline_payload(
            raw,
            chapter_number=chapter_number,
            default_chapter_title=default_chapter_title,
            title_remap=title_remap,
        )
        if item.get("node_type") == "chapter" and item.get("title") and not default_chapter_title:
            default_chapter_title = str(item["title"])
        normalized.append(item)
    if default_chapter_title:
        normalized = [
            normalize_outline_payload(
                item,
                chapter_number=chapter_number,
                default_chapter_title=default_chapter_title,
                title_remap=title_remap,
            )
            for item in normalized
        ]
    return normalized


def _payload(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, dict):
        raw = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else candidate
        return dict(raw)
    raw = getattr(candidate, "edited_payload", None) or getattr(candidate, "raw_payload", None)
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    text = str(value).strip()
    return [text] if text else []


def _first_present(payload: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    nested = payload.get("narrative_state")
    if isinstance(nested, dict):
        for alias in aliases:
            if alias in nested:
                return nested.get(alias)
    for alias in aliases:
        if alias in payload:
            return payload.get(alias)
    return None


def normalize_chapter_narrative_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical PlotPilot-inspired chapter narrative state payload."""
    result: dict[str, Any] = {}
    for canonical, aliases in PLOTPILOT_NARRATIVE_ALIASES.items():
        items = _as_list(_first_present(payload, aliases))
        if items:
            result[canonical] = items
    if payload.get("chapter_id"):
        result["chapter_id"] = str(payload.get("chapter_id"))
    if payload.get("chapter_title"):
        result["chapter_title"] = str(payload.get("chapter_title"))
    if payload.get("summary_text") or payload.get("summary"):
        result["summary"] = str(payload.get("summary_text") or payload.get("summary"))
    return result


def has_chapter_narrative_state(payload: dict[str, Any]) -> bool:
    state = normalize_chapter_narrative_state(payload)
    return any(bool(state.get(key)) for key in NARRATIVE_STATE_FIELDS)


def normalize_section_scene_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical scene-state payload for a section outline candidate."""
    if normalize_node_type(payload.get("node_type")) != "section":
        return {}
    result: dict[str, Any] = {}
    for key in SECTION_SCENE_STATE_FIELDS:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            result[key] = value
    if not result:
        return {}
    for key in ("id", "target_id", "title", "parent_title", "summary", "actual_summary", "planned_summary"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            result[key] = value
    return result


def narrative_counts(payload: dict[str, Any]) -> dict[str, int]:
    state = normalize_chapter_narrative_state(payload)
    return {
        "event_count": len(_as_list(state.get("events"))) + len(_as_list(state.get("timeline_events"))),
        "foreshadowing_planted_count": len(_as_list(state.get("foreshadowing_planted"))),
        "foreshadowing_resolved_count": len(_as_list(state.get("foreshadowing_resolved"))),
        "storyline_progress_count": len(_as_list(state.get("storyline_progress"))) + len(_as_list(state.get("new_storylines"))),
        "unresolved_action_count": len(_as_list(state.get("unresolved_actions"))),
    }


def _item_type(candidate: Any) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("item_type") or candidate.get("type") or "")
    return str(getattr(candidate, "item_type", "") or "")


def _candidate_status(candidate: Any) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("status") or "")
    return str(getattr(candidate, "status", "") or "")


def inspect_candidate_coverage_items(candidates: Iterable[Any]) -> CandidateCoverage:
    items = list(candidates)
    has_summary = False
    has_chapter_outline = False
    section_count = 0
    scene_count = 1
    character_state_count = 0
    scene_state_count = 0
    event_count = 0
    foreshadowing_planted_count = 0
    foreshadowing_resolved_count = 0
    storyline_progress_count = 0
    unresolved_action_count = 0
    warnings: list[str] = []
    for candidate in items:
        if _candidate_status(candidate) == "rejected":
            continue
        item_type = _item_type(candidate)
        payload = _payload(candidate)
        if item_type == "chapter_summary":
            if str(payload.get("summary_text") or payload.get("summary") or payload.get("content") or "").strip():
                has_summary = True
            raw_scene_count = payload.get("scene_count")
            scenes = payload.get("scenes")
            if isinstance(raw_scene_count, int):
                scene_count = max(scene_count, raw_scene_count)
            if isinstance(scenes, list):
                scene_count = max(scene_count, len(scenes))
                scene_state_count += sum(1 for scene in scenes if isinstance(scene, dict) and normalize_section_scene_state({**scene, "node_type": "section"}))
            counts = narrative_counts(payload)
            event_count += counts["event_count"]
            foreshadowing_planted_count += counts["foreshadowing_planted_count"]
            foreshadowing_resolved_count += counts["foreshadowing_resolved_count"]
            storyline_progress_count += counts["storyline_progress_count"]
            unresolved_action_count += counts["unresolved_action_count"]
        elif item_type in {"outline_create", "outline_update"}:
            node_type = normalize_node_type(payload.get("node_type"))
            if node_type == "chapter":
                has_chapter_outline = True
            elif node_type == "section":
                section_count += 1
                scene_state = normalize_section_scene_state(payload)
                if scene_state:
                    scene_state_count += 1
                    unresolved_action_count += len(_as_list(scene_state.get("unresolved_actions")))
        elif item_type == "character_state_update":
            character_state_count += 1
    if scene_count > 1 and section_count == 0:
        warnings.append("multi_scene_chapter_without_section_outline")
    if scene_count > 1 and scene_state_count == 0:
        warnings.append("multi_scene_chapter_without_scene_state")
    if character_state_count == 0:
        warnings.append("no_character_state_candidates")
    if event_count == 0 and storyline_progress_count == 0:
        warnings.append("no_narrative_state_candidates")
    return CandidateCoverage(
        total=len(items),
        has_chapter_summary=has_summary,
        has_chapter_outline=has_chapter_outline,
        section_count=section_count,
        scene_count=scene_count,
        character_state_count=character_state_count,
        scene_state_count=scene_state_count,
        event_count=event_count,
        foreshadowing_planted_count=foreshadowing_planted_count,
        foreshadowing_resolved_count=foreshadowing_resolved_count,
        storyline_progress_count=storyline_progress_count,
        unresolved_action_count=unresolved_action_count,
        warnings=warnings,
    )


def estimate_scene_count(content: str) -> int:
    text = (content or "").strip()
    if not text:
        return 1
    markers = len(re.findall(r"(?m)^\s*(?:#{1,4}\s+|\*\s*\*\s*\*|---+|场景\s*\d+|scene\s*\d+)", text, re.I))
    if markers > 1:
        return max(2, min(6, markers))
    paragraphs = [p for p in re.split(r"\n\s*\n+", text) if len(p.strip()) >= 20]
    if len(paragraphs) >= 12:
        return min(6, max(2, len(paragraphs) // 6))
    return 1


def chapter_outline_node(db: Session, project_id: str, chapter: Chapter) -> OutlineNode | None:
    node = None
    if chapter.outline_node_id:
        node = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.id == chapter.outline_node_id,
        ).first()
    if node and node.node_type == "chapter":
        return node
    if node and node.parent_id:
        parent = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.id == node.parent_id,
        ).first()
        if parent and parent.node_type == "chapter":
            return parent
    chapter_number = extract_chapter_number(chapter.title)
    if chapter_number:
        return db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.node_type == "chapter",
            OutlineNode.title.contains(str(chapter_number)),
        ).order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc()).first()
    return None


def _active_fact_payloads(db: Session, project_id: str, chapter_id: str, fact_type: str) -> list[dict[str, Any]]:
    rows = (
        db.query(CatalogingFact)
        .filter(CatalogingFact.project_id == project_id)
        .filter(CatalogingFact.chapter_id == chapter_id)
        .filter(CatalogingFact.fact_type == fact_type)
        .filter(CatalogingFact.status == "active")
        .order_by(CatalogingFact.created_at.desc())
        .all()
    )
    payloads: list[dict[str, Any]] = []
    for row in rows:
        try:
            parsed = json.loads(row.raw_payload or "{}")
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _narrative_health(db: Session, project_id: str, chapter: Chapter, estimated_scene_count: int) -> dict[str, Any]:
    chapter_states = _active_fact_payloads(db, project_id, chapter.id, "chapter_narrative_state")
    section_states = _active_fact_payloads(db, project_id, chapter.id, "section_scene_state")
    chapter_links = _active_fact_payloads(db, project_id, chapter.id, "chapter_element_links")
    totals = {
        "chapter_narrative_state_count": len(chapter_states),
        "section_scene_state_count": len(section_states),
        "chapter_element_link_count": len(chapter_links),
        "event_count": 0,
        "foreshadowing_planted_count": 0,
        "foreshadowing_resolved_count": 0,
        "storyline_progress_count": 0,
        "unresolved_action_count": 0,
    }
    for payload in chapter_states:
        counts = narrative_counts(payload)
        for key, value in counts.items():
            totals[key] += value
    for payload in section_states:
        totals["unresolved_action_count"] += len(_as_list(payload.get("unresolved_actions")))

    warnings: list[str] = []
    if not chapter_states:
        warnings.append("chapter_narrative_state_missing")
    if estimated_scene_count > 1 and not section_states:
        warnings.append("section_scene_state_missing")
    if totals["event_count"] == 0 and totals["storyline_progress_count"] == 0:
        warnings.append("narrative_progress_missing")
    return {
        **totals,
        "warnings": warnings,
        "ok": not warnings,
    }


def inspect_chapter_granularity(db: Session, project_id: str, chapter: Chapter, *, level: str = "narrative") -> dict[str, Any]:
    outline = chapter_outline_node(db, project_id, chapter)
    section_count = 0
    if outline:
        section_count = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.parent_id == outline.id,
            OutlineNode.node_type == "section",
        ).count()
    scene_count = estimate_scene_count(chapter.content or "")
    chapter_characters = db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).all()
    linked_character_ids = {link.character_id for link in chapter_characters if link.character_id}
    state_missing: list[str] = []
    if linked_character_ids:
        characters = db.query(Character).filter(Character.id.in_(linked_character_ids)).all()
        for character in characters:
            if character.last_updated_chapter_id != chapter.id:
                state_missing.append(character.name)
    wb_links = db.query(ChapterWorldbuilding).filter(ChapterWorldbuilding.chapter_id == chapter.id).count()

    missing: list[str] = []
    warnings: list[str] = []
    if not chapter.summary:
        missing.append("chapter_summary")
    if not outline:
        missing.append("chapter_outline")
    if scene_count > 1 and section_count == 0:
        warnings.append("section_outline_missing_for_multi_scene_chapter")
    if chapter_characters and state_missing:
        warnings.append("character_state_update_missing")
    if not chapter_characters:
        warnings.append("chapter_character_links_missing")
    if wb_links == 0:
        warnings.append("worldbuilding_links_missing")
    narrative_health = _narrative_health(db, project_id, chapter, scene_count) if level == "narrative" else None
    if narrative_health:
        warnings.extend(narrative_health.get("warnings") or [])
    return {
        "chapter_id": chapter.id,
        "title": chapter.title,
        "word_count": chapter.word_count or 0,
        "outline_node_id": outline.id if outline else None,
        "outline_title": outline.title if outline else None,
        "section_count": section_count,
        "estimated_scene_count": scene_count,
        "linked_characters": len(linked_character_ids),
        "characters_missing_state_update": state_missing,
        "worldbuilding_links": wb_links,
        "narrative_health": narrative_health,
        "missing": missing,
        "warnings": warnings,
        "ok": not missing and not warnings,
    }


def granularity_contract_prompt() -> str:
    return (
        "Post-write/archive candidates must use the same schema as cataloging: "
        "chapter_summary, outline_create/update, character_create/update, "
        "character_state_update, character_timeline, character_relationship, "
        "worldbuilding_create/update, worldbuilding_timeline, and chapter_link. "
        "Every saved chapter needs chapter_summary and a chapter-level outline node; "
        "multi-scene chapters need 2-6 section outline nodes under the chapter node. "
        "chapter_summary may include narrative_state with events, timeline_events, "
        "foreshadowing_planted, foreshadowing_resolved, storyline_progress, "
        "new_storylines, reader_known_facts, character_known_facts, unresolved_actions, "
        "character_actions, and relationship_changes. "
        "section outline payloads may include scene_number, purpose, location, timeline, "
        "pov_character, characters, entry_state, exit_state, emotional_residue, and unresolved_actions. "
        "Every appearing character should receive character_state_update with "
        + ", ".join(CHARACTER_STATE_FIELDS)
        + "."
    )
