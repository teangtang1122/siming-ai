"""Shared story granularity contract for cataloging and post-write archiving."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..modules.continuity.domain.cataloging_contract import (
    canonical_chapter_link_characters,
    coverage_manifest_duplicate_relationship_pairs,
)

from sqlalchemy.orm import Session

from ..core.numbers import (
    chinese_number_to_int,
    extract_chapter_number as extract_shared_chapter_number,
)
from ..database.models import (
    CatalogingFact,
    Chapter,
    ChapterCharacter,
    ChapterWorldbuilding,
    Character,
    OutlineNode,
)

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

CHARACTER_PROFILE_FIELDS: tuple[str, ...] = (
    "core_motivation",
    "inner_lack",
    "core_belief",
    "public_persona",
    "hidden_persona",
    "reveal_chapter",
    "moral_taboo",
    "voice",
    "action_habit",
    "trauma_trigger",
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
    "verbosity",
    "emotion_tendency",
    "custom_system_prompt",
    "profile",
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
    narrative_assessed: bool = False
    governance_findings_count: int = 0
    governance_review_source: str = ""
    has_scene_count_declaration: bool = False
    has_character_declaration: bool = False
    has_worldbuilding_declaration: bool = False
    has_relationship_declaration: bool = False
    has_character_profile_declaration: bool = False
    declared_character_count: int = 0
    declared_worldbuilding_count: int = 0
    declared_relationship_count: int = 0
    declared_character_profile_count: int = 0
    worldbuilding_candidate_count: int = 0
    relationship_candidate_count: int = 0
    character_profile_candidate_count: int = 0
    chapter_link_count: int = 0
    declared_character_identities: tuple[str, ...] = ()
    declared_worldbuilding_identities: tuple[str, ...] = ()
    declared_relationship_identities: tuple[str, ...] = ()
    declared_character_profile_identities: tuple[str, ...] = ()
    character_state_identities: tuple[str, ...] = ()
    worldbuilding_candidate_identities: tuple[str, ...] = ()
    relationship_candidate_identities: tuple[str, ...] = ()
    character_profile_candidate_identities: tuple[str, ...] = ()
    chapter_link_character_identities: tuple[str, ...] = ()
    chapter_link_worldbuilding_identities: tuple[str, ...] = ()
    persistence_missing: tuple[str, ...] = ()
    review_warnings: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.cli_parity_missing

    @property
    def missing(self) -> list[str]:
        missing: list[str] = []
        if not self.has_chapter_summary:
            missing.append("chapter_summary")
        if not self.has_chapter_outline:
            missing.append("chapter-level outline")
        if not self.narrative_assessed:
            missing.append("narrative-governance assessment")
        return missing

    @property
    def granular_missing(self) -> list[str]:
        """Return data-card gaps declared by the chapter summary itself."""

        missing = list(self.missing)
        if self.scene_count > 1 and self.section_count != self.scene_count:
            missing.append(
                f"section outlines for declared scenes ({self.section_count}/{self.scene_count})"
            )
        if self.scene_count > 1 and self.scene_state_count != self.scene_count:
            missing.append(
                f"section scene states ({self.scene_state_count}/{self.scene_count})"
            )
        missing_character_states = set(self.declared_character_identities) - set(self.character_state_identities)
        if missing_character_states or self.character_state_count != self.declared_character_count:
            missing.append(
                "character_state_update for declared characters "
                f"({self.character_state_count}/{self.declared_character_count})"
            )
        undeclared_character_states = (
            set(self.character_state_identities) - set(self.declared_character_identities)
        )
        if undeclared_character_states:
            missing.append(
                "character_state_update identities missing from coverage_manifest.characters: "
                + "、".join(sorted(undeclared_character_states))
            )
        missing_worldbuilding = (
            set(self.declared_worldbuilding_identities)
            - set(self.worldbuilding_candidate_identities)
        )
        if missing_worldbuilding or self.worldbuilding_candidate_count != self.declared_worldbuilding_count:
            missing.append(
                "worldbuilding candidates for declared entries "
                f"({self.worldbuilding_candidate_count}/{self.declared_worldbuilding_count})"
            )
        undeclared_worldbuilding = (
            set(self.worldbuilding_candidate_identities)
            - set(self.declared_worldbuilding_identities)
        )
        if undeclared_worldbuilding:
            missing.append(
                "worldbuilding candidate identities missing from coverage_manifest.worldbuilding: "
                + "、".join(sorted(undeclared_worldbuilding))
            )
        missing_relationships = (
            set(self.declared_relationship_identities)
            - set(self.relationship_candidate_identities)
        )
        if missing_relationships or self.relationship_candidate_count != self.declared_relationship_count:
            missing.append(
                "character_relationship candidates for declared changes "
                f"({self.relationship_candidate_count}/{self.declared_relationship_count})"
            )
        undeclared_relationships = (
            set(self.relationship_candidate_identities)
            - set(self.declared_relationship_identities)
        )
        if undeclared_relationships:
            missing.append(
                "character_relationship identities missing from coverage_manifest.relationships: "
                + "、".join(sorted(undeclared_relationships))
            )
        missing_profiles = (
            set(self.declared_character_profile_identities)
            - set(self.character_profile_candidate_identities)
        )
        if missing_profiles or self.character_profile_candidate_count != self.declared_character_profile_count:
            missing.append(
                "character_create/update for declared profile changes "
                f"({self.character_profile_candidate_count}/{self.declared_character_profile_count})"
            )
        undeclared_profiles = (
            set(self.character_profile_candidate_identities)
            - set(self.declared_character_profile_identities)
        )
        if undeclared_profiles:
            missing.append(
                "character_create/update identities missing from coverage_manifest.character_profiles: "
                + "、".join(sorted(undeclared_profiles))
            )
        missing_character_links = (
            set(self.declared_character_identities)
            - set(self.chapter_link_character_identities)
        )
        missing_worldbuilding_links = (
            set(self.declared_worldbuilding_identities)
            - set(self.chapter_link_worldbuilding_identities)
        )
        required_links = self.declared_character_count + self.declared_worldbuilding_count
        linked_identity_count = (
            len(set(self.declared_character_identities) & set(self.chapter_link_character_identities))
            + len(set(self.declared_worldbuilding_identities) & set(self.chapter_link_worldbuilding_identities))
        )
        if missing_character_links or missing_worldbuilding_links or linked_identity_count < required_links:
            missing.append(
                "chapter_link candidates for declared characters/worldbuilding "
                f"({linked_identity_count}/{required_links})"
            )
        missing.extend(item for item in self.persistence_missing if item not in missing)
        return missing

    @property
    def cli_parity_missing(self) -> list[str]:
        """Return the stricter transport contract for Siming-managed CLI turns.

        Internal API generation already owns the resolution prompt and can be
        evaluated from the candidates it produced.  A CLI turn is an external
        tool loop, so it must also declare the coverage it used; otherwise an
        answer containing only the two minimum cards is indistinguishable from
        a genuinely character-free, single-scene chapter.
        """

        missing = list(self.granular_missing)
        if not self.has_scene_count_declaration:
            missing.append("chapter_summary.scene_count coverage declaration")
        if not self.has_character_declaration:
            missing.append("chapter_summary.characters coverage declaration")
        if not self.has_worldbuilding_declaration:
            missing.append("chapter_summary.worldbuilding coverage declaration")
        if not self.has_relationship_declaration:
            missing.append("chapter_summary.relationships coverage declaration")
        if not self.has_character_profile_declaration:
            missing.append("chapter_summary.character_profiles coverage declaration")
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
            "narrative_assessed": self.narrative_assessed,
            "governance_findings_count": self.governance_findings_count,
            "governance_review_source": self.governance_review_source,
            "has_scene_count_declaration": self.has_scene_count_declaration,
            "has_character_declaration": self.has_character_declaration,
            "has_worldbuilding_declaration": self.has_worldbuilding_declaration,
            "has_relationship_declaration": self.has_relationship_declaration,
            "has_character_profile_declaration": self.has_character_profile_declaration,
            "declared_character_count": self.declared_character_count,
            "declared_worldbuilding_count": self.declared_worldbuilding_count,
            "declared_relationship_count": self.declared_relationship_count,
            "declared_character_profile_count": self.declared_character_profile_count,
            "worldbuilding_candidate_count": self.worldbuilding_candidate_count,
            "relationship_candidate_count": self.relationship_candidate_count,
            "character_profile_candidate_count": self.character_profile_candidate_count,
            "chapter_link_count": self.chapter_link_count,
            "declared_character_identities": list(self.declared_character_identities),
            "declared_worldbuilding_identities": list(self.declared_worldbuilding_identities),
            "declared_relationship_identities": list(self.declared_relationship_identities),
            "declared_character_profile_identities": list(self.declared_character_profile_identities),
            "character_state_identities": list(self.character_state_identities),
            "worldbuilding_candidate_identities": list(self.worldbuilding_candidate_identities),
            "relationship_candidate_identities": list(self.relationship_candidate_identities),
            "character_profile_candidate_identities": list(self.character_profile_candidate_identities),
            "chapter_link_character_identities": list(self.chapter_link_character_identities),
            "chapter_link_worldbuilding_identities": list(self.chapter_link_worldbuilding_identities),
            "persistence_missing": list(self.persistence_missing),
            "review_warnings": list(self.review_warnings),
            "is_complete": self.is_complete,
            "missing": self.missing,
            "granular_missing": self.granular_missing,
            "cli_parity_missing": self.cli_parity_missing,
            "warnings": list(self.warnings),
        }


def extract_chapter_number(*texts: Any) -> int | None:
    for text in texts:
        chapter_number = extract_shared_chapter_number(
            str(text or ""),
            allow_bare=True,
            allow_unmarked=True,
        )
        if chapter_number is not None:
            return chapter_number
    return None


def normalize_node_type(value: Any) -> str:
    node_type = str(value or "chapter").strip().lower()
    if node_type == "scene":
        node_type = "section"
    return node_type if node_type in OUTLINE_NODE_TYPES else "chapter"


def title_has_chapter_number(title: Any, chapter_number: int | None) -> bool:
    if not chapter_number:
        return True
    return extract_chapter_number(title) == chapter_number


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


def _summary_fragment(item: Any) -> str:
    if isinstance(item, dict):
        for key in (
            "summary_text",
            "summary",
            "description",
            "event_description",
            "event",
            "title",
            "text",
            "content",
        ):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return ""
    return str(item or "").strip()


def derive_chapter_summary_text(
    payload: dict[str, Any],
    *,
    max_items: int = 4,
    max_chars: int = 1000,
) -> str:
    """Recover a factual summary from a provider's rich narrative payload.

    Some providers satisfy the narrative-state contract but omit the redundant
    ``summary_text`` field.  The archive already contains enough factual text
    to recover it deterministically; doing so is safer than discarding the
    whole chapter or asking the model to invent a second answer.  Very small
    ledger-only payloads are deliberately rejected as insufficient summaries.
    """

    existing = str(
        payload.get("summary_text")
        or payload.get("summary")
        or payload.get("content")
        or ""
    ).strip()
    if existing:
        return existing

    state = normalize_chapter_narrative_state(payload)
    fragments: list[str] = []
    seen: set[str] = set()
    for key in (
        "events",
        "timeline_events",
        "storyline_progress",
        "new_storylines",
        "reader_known_facts",
        "unresolved_actions",
    ):
        for item in _as_list(state.get(key)):
            text = _summary_fragment(item)
            signature = re.sub(r"\s+", "", text)
            if text and signature not in seen:
                seen.add(signature)
                fragments.append(text)
            if len(fragments) >= max_items:
                break
        if len(fragments) >= max_items:
            break
    if fragments:
        summary = "；".join(fragments)
        if len(re.sub(r"\s+", "", summary)) >= 20:
            return summary[:max_chars].rstrip("；")

    review = payload.get("narrative_review")
    if isinstance(review, dict):
        review = (
            review.get("summary_text")
            or review.get("summary")
            or review.get("overview")
            or review.get("conclusion")
        )
    review_text = str(review or "").strip()
    if len(re.sub(r"\s+", "", review_text)) >= 20:
        return review_text[:max_chars]
    return ""


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


def _coverage_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("coverage_manifest")
    return value if isinstance(value, dict) else {}


def _declared_value(payload: dict[str, Any], *keys: str) -> tuple[Any, bool]:
    manifest = _coverage_manifest(payload)
    for source in (manifest, payload):
        for key in keys:
            if key in source:
                return source.get(key), True
    return None, False


def _manifest_identity(item: Any, keys: tuple[str, ...]) -> str:
    if isinstance(item, dict):
        for key in keys:
            value = str(item.get(key) or "").strip()
            if value:
                return re.sub(r"\s+", "", value).casefold()
        value = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    else:
        value = str(item or "").strip()
    return re.sub(r"\s+", "", value).casefold()


def _declared_count(value: Any, keys: tuple[str, ...]) -> int:
    identities = {
        identity
        for item in _as_list(value)
        if (identity := _manifest_identity(item, keys))
    }
    return len(identities)


def _declared_identities(value: Any, keys: tuple[str, ...]) -> set[str]:
    return {
        identity
        for item in _as_list(value)
        if (identity := _manifest_identity(item, keys))
    }


def _relationship_identity(value: Any) -> str:
    if not isinstance(value, dict):
        return _manifest_identity(value, ())
    source = _manifest_identity(
        value,
        ("source_name", "source", "from_name", "character_a", "character_a_name"),
    )
    target = _manifest_identity(
        value,
        ("target_name", "target", "to_name", "character_b", "character_b_name"),
    )
    relation = _manifest_identity(value, ("relationship_type", "relation", "type"))
    if not source or not target:
        return ""
    return "|".join((source, target, relation or "关联"))


def _relationship_identities(value: Any) -> set[str]:
    return {
        identity
        for item in _as_list(value)
        if (identity := _relationship_identity(item))
    }


def _has_meaningful_character_profile(payload: dict[str, Any]) -> bool:
    """Reject identity-only character cards that would leave a blank profile."""

    for attribute in (
        "aliases",
        "role_type",
        "personality",
        "background",
        "abilities",
        "tone_style",
        "catchphrases",
        "verbosity",
        "emotion_tendency",
        "custom_system_prompt", "appearance", "age",
    ):
        value = payload.get(attribute)
        if value not in (None, "", [], {}):
            return True
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        profile = payload.get("profile_json")
    if isinstance(profile, dict) and any(
        profile.get(attribute) not in (None, "", [], {})
        for attribute in CHARACTER_PROFILE_FIELDS
    ):
        return True
    ai_config = payload.get("ai_config")
    return isinstance(ai_config, dict) and any(
        ai_config.get(attribute) not in (None, "", [], {})
        for attribute in (
            "tone_style",
            "catchphrases",
            "verbosity",
            "emotion_tendency",
            "custom_system_prompt",
        )
    )


def _hint_scene_count(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 1
    match = re.search(
        r"([0-9〇零一二两三四五六七八九十百]{1,4})\s*个?\s*(?:独立)?场景",
        text,
    )
    if match:
        token = match.group(1)
        parsed = int(token) if token.isdigit() else chinese_number_to_int(token)
        if parsed:
            return max(1, min(6, parsed))
    return 2 if "多场景" in text else 1


def _declared_scene_coverage(payload: dict[str, Any]) -> tuple[int, bool]:
    raw_count, has_count = _declared_value(payload, "scene_count")
    scenes, has_scenes = _declared_value(payload, "scenes")
    count = 1
    if isinstance(raw_count, int):
        count = max(count, raw_count)
    elif str(raw_count or "").strip().isdigit():
        count = max(count, int(str(raw_count).strip()))
    if isinstance(scenes, list):
        count = max(count, len(scenes))
    hint, _ = _declared_value(payload, "outline_hint", "scene_outline_hint")
    count = max(count, _hint_scene_count(hint))
    return min(6, count), bool(has_count or has_scenes)


def inspect_candidate_coverage_items(candidates: Iterable[Any]) -> CandidateCoverage:
    items = list(candidates)
    has_summary = False
    has_chapter_outline = False
    section_count = 0
    section_candidate_count = 0
    section_scene_numbers: list[int] = []
    invalid_section_scene_numbers = 0
    scene_count = 1
    character_state_count = 0
    scene_state_count = 0
    scene_state_numbers: set[int] = set()
    event_count = 0
    foreshadowing_planted_count = 0
    foreshadowing_resolved_count = 0
    storyline_progress_count = 0
    unresolved_action_count = 0
    narrative_assessed = False
    governance_findings_count = 0
    governance_review_source = ""
    has_scene_count_declaration = False
    has_character_declaration = False
    has_worldbuilding_declaration = False
    has_relationship_declaration = False
    has_character_profile_declaration = False
    declared_character_identities: set[str] = set()
    declared_worldbuilding_identities: set[str] = set()
    declared_relationship_identities: set[str] = set()
    declared_character_profile_identities: set[str] = set()
    character_state_identities: set[str] = set()
    worldbuilding_candidate_identities: set[str] = set()
    relationship_candidate_identities: set[str] = set()
    character_profile_candidate_identities: set[str] = set()
    chapter_link_character_identities: set[str] = set()
    chapter_link_worldbuilding_identities: set[str] = set()
    chapter_link_count = 0
    governance_source_priority = {"": 0, "fallback": 1, "provided": 2, "llm": 3, "manual": 4}
    warnings: list[str] = []
    duplicate_relationship_pairs: set[str] = set()
    for candidate in items:
        if _candidate_status(candidate) == "rejected":
            continue
        item_type = _item_type(candidate)
        payload = _payload(candidate)
        if item_type == "chapter_summary":
            if derive_chapter_summary_text(payload):
                has_summary = True
            declared_scenes, scene_declaration = _declared_scene_coverage(payload)
            scene_count = max(scene_count, declared_scenes)
            has_scene_count_declaration = has_scene_count_declaration or scene_declaration
            declared_characters, character_declaration = _declared_value(
                payload,
                "characters",
                "appearing_characters",
            )
            declared_worldbuilding, worldbuilding_declaration = _declared_value(
                payload,
                "worldbuilding",
                "worldbuilding_entries",
                "settings",
            )
            declared_relationships, relationship_declaration = _declared_value(
                payload,
                "relationships",
                "character_relationships",
            )
            declared_character_profiles, character_profile_declaration = _declared_value(
                payload,
                "character_profiles",
                "profile_updates",
            )
            has_character_declaration = has_character_declaration or character_declaration
            has_worldbuilding_declaration = has_worldbuilding_declaration or worldbuilding_declaration
            has_relationship_declaration = has_relationship_declaration or relationship_declaration
            has_character_profile_declaration = (
                has_character_profile_declaration or character_profile_declaration
            )
            declared_character_identities.update(
                _declared_identities(
                    declared_characters,
                    ("id", "character_id", "name", "character_name", "title"),
                )
            )
            declared_worldbuilding_identities.update(
                _declared_identities(
                    declared_worldbuilding,
                    ("id", "entry_id", "title", "name", "entry_title"),
                )
            )
            declared_relationship_identities.update(
                _relationship_identities(declared_relationships)
            )
            duplicate_relationship_pairs.update(
                coverage_manifest_duplicate_relationship_pairs(declared_relationships)
            )
            declared_character_profile_identities.update(
                _declared_identities(
                    declared_character_profiles,
                    ("id", "character_id", "name", "character_name", "title"),
                )
            )
            relationship_changes = _first_present(
                payload,
                PLOTPILOT_NARRATIVE_ALIASES["relationship_changes"],
            )
            if not relationship_declaration:
                declared_relationship_identities.update(
                    _relationship_identities(relationship_changes)
                )
        if item_type in {"chapter_summary", "chapter_state"}:
            counts = narrative_counts(payload)
            review = payload.get("narrative_review")
            has_state_contract = (
                isinstance(payload.get("narrative_state"), dict)
                or item_type == "chapter_state"
            )
            if has_state_contract or isinstance(review, dict):
                narrative_assessed = True
            if isinstance(review, dict):
                candidate_source = str(review.get("source") or "").strip()
            elif has_state_contract:
                candidate_source = "provided"
            else:
                candidate_source = ""
            if governance_source_priority.get(candidate_source, 2) > governance_source_priority.get(governance_review_source, 0):
                governance_review_source = candidate_source
            event_count += counts["event_count"]
            foreshadowing_planted_count += counts["foreshadowing_planted_count"]
            foreshadowing_resolved_count += counts["foreshadowing_resolved_count"]
            storyline_progress_count += counts["storyline_progress_count"]
            unresolved_action_count += counts["unresolved_action_count"]
            governance_findings_count += (
                counts["foreshadowing_planted_count"]
                + counts["foreshadowing_resolved_count"]
                + counts["unresolved_action_count"]
            )
        if item_type in {"outline_create", "outline_update"}:
            node_type = normalize_node_type(payload.get("node_type"))
            if node_type == "chapter":
                has_chapter_outline = True
            elif node_type == "section":
                section_candidate_count += 1
                try:
                    section_scene_number = int(payload.get("scene_number"))
                except (TypeError, ValueError):
                    section_scene_number = 0
                if section_scene_number > 0:
                    section_scene_numbers.append(section_scene_number)
                else:
                    invalid_section_scene_numbers += 1
                scene_state = normalize_section_scene_state(payload)
                if scene_state and section_scene_number > 0:
                    scene_state_numbers.add(section_scene_number)
                    unresolved_action_count += len(_as_list(scene_state.get("unresolved_actions")))
        elif item_type == "character_state_update":
            identity = _manifest_identity(
                payload,
                ("id", "character_id", "name", "character_name", "target_name"),
            )
            if identity:
                character_state_identities.add(identity)
        elif item_type in {"character_create", "character_update"}:
            identity = _manifest_identity(
                payload,
                ("id", "character_id", "name", "character_name", "target_name"),
            )
            if identity and _has_meaningful_character_profile(payload):
                character_profile_candidate_identities.add(identity)
        elif item_type in {"worldbuilding_create", "worldbuilding_update", "worldbuilding_timeline"}:
            identity = _manifest_identity(
                payload,
                # coverage_manifest.worldbuilding is a title contract.  An
                # update candidate also carries a database ID, but preferring
                # that ID here makes a correct title manifest look incomplete.
                # Ownership/existence of the ID is validated separately at the
                # write boundary.
                ("title", "name", "entry_title", "target_name", "id", "entry_id"),
            )
            if identity:
                worldbuilding_candidate_identities.add(identity)
        elif item_type == "character_relationship":
            identity = _relationship_identity(payload)
            if identity:
                relationship_candidate_identities.add(identity)
        elif item_type == "chapter_link":
            chapter_link_count += 1
            chapter_link_character_identities.update(
                _declared_identities(
                    canonical_chapter_link_characters(payload),
                    ("id", "character_id", "name", "character_name", "title"),
                )
            )
            chapter_link_worldbuilding_identities.update(
                _declared_identities(
                    payload.get("worldbuilding_titles")
                    or payload.get("worldbuilding")
                    or payload.get("settings"),
                    ("id", "entry_id", "title", "name", "entry_title"),
                )
            )
        governance_candidates = payload.get("governance_candidates")
        if isinstance(governance_candidates, list):
            narrative_assessed = True
            governance_findings_count += len(
                [item for item in governance_candidates if isinstance(item, dict)]
            )
    expected_scene_numbers = set(range(1, scene_count + 1))
    observed_scene_numbers = set(section_scene_numbers)
    section_count = len(observed_scene_numbers & expected_scene_numbers)
    scene_state_count = len(scene_state_numbers & expected_scene_numbers)
    duplicate_scene_numbers = sorted({
        value for value in section_scene_numbers if section_scene_numbers.count(value) > 1
    })
    out_of_range_scene_numbers = sorted(
        observed_scene_numbers - expected_scene_numbers
    )
    scene_number_integrity: list[str] = []
    if invalid_section_scene_numbers:
        scene_number_integrity.append(
            f"missing_or_invalid={invalid_section_scene_numbers}"
        )
    if duplicate_scene_numbers:
        scene_number_integrity.append(
            "duplicate=" + ",".join(map(str, duplicate_scene_numbers))
        )
    if out_of_range_scene_numbers:
        scene_number_integrity.append(
            "out_of_range=" + ",".join(map(str, out_of_range_scene_numbers))
        )
    if scene_count > 1 and section_count == 0:
        warnings.append("multi_scene_chapter_without_section_outline")
    if scene_count > 1 and scene_state_count == 0:
        warnings.append("multi_scene_chapter_without_scene_state")
    character_state_count = len(character_state_identities)
    declared_character_count = len(declared_character_identities)
    declared_worldbuilding_count = len(declared_worldbuilding_identities)
    declared_relationship_count = len(declared_relationship_identities)
    declared_character_profile_count = len(declared_character_profile_identities)
    worldbuilding_candidate_count = len(worldbuilding_candidate_identities)
    relationship_candidate_count = len(relationship_candidate_identities)
    character_profile_candidate_count = len(character_profile_candidate_identities)
    if character_state_count == 0:
        warnings.append("no_character_state_candidates")
    if event_count == 0 and storyline_progress_count == 0:
        warnings.append("no_narrative_state_candidates")
    if not narrative_assessed:
        warnings.append("no_narrative_governance_assessment")
    elif governance_review_source == "fallback":
        warnings.append("narrative_governance_requires_human_review")
    persistence_missing: list[str] = []
    if duplicate_relationship_pairs:
        persistence_missing.append(
            "coverage_manifest.relationships has multiple current types for one directed pair: "
            + "、".join(sorted(duplicate_relationship_pairs))
        )
    if section_candidate_count and scene_number_integrity:
        persistence_missing.append(
            "section outline candidates require unique scene_number within "
            f"1..{scene_count}: " + ", ".join(scene_number_integrity)
        )
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
        narrative_assessed=narrative_assessed,
        governance_findings_count=governance_findings_count,
        governance_review_source=governance_review_source,
        has_scene_count_declaration=has_scene_count_declaration,
        has_character_declaration=has_character_declaration,
        has_worldbuilding_declaration=has_worldbuilding_declaration,
        has_relationship_declaration=has_relationship_declaration,
        has_character_profile_declaration=has_character_profile_declaration,
        declared_character_count=declared_character_count,
        declared_worldbuilding_count=declared_worldbuilding_count,
        declared_relationship_count=declared_relationship_count,
        declared_character_profile_count=declared_character_profile_count,
        worldbuilding_candidate_count=worldbuilding_candidate_count,
        relationship_candidate_count=relationship_candidate_count,
        character_profile_candidate_count=character_profile_candidate_count,
        chapter_link_count=chapter_link_count,
        declared_character_identities=tuple(sorted(declared_character_identities)),
        declared_worldbuilding_identities=tuple(sorted(declared_worldbuilding_identities)),
        declared_relationship_identities=tuple(sorted(declared_relationship_identities)),
        declared_character_profile_identities=tuple(sorted(declared_character_profile_identities)),
        character_state_identities=tuple(sorted(character_state_identities)),
        worldbuilding_candidate_identities=tuple(sorted(worldbuilding_candidate_identities)),
        relationship_candidate_identities=tuple(sorted(relationship_candidate_identities)),
        character_profile_candidate_identities=tuple(sorted(character_profile_candidate_identities)),
        chapter_link_character_identities=tuple(sorted(chapter_link_character_identities)),
        chapter_link_worldbuilding_identities=tuple(sorted(chapter_link_worldbuilding_identities)),
        persistence_missing=tuple(persistence_missing),
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
        candidates = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.node_type == "chapter",
        ).order_by(OutlineNode.sort_order.asc(), OutlineNode.created_at.asc()).all()
        return next(
            (
                candidate
                for candidate in candidates
                if extract_chapter_number(candidate.title) == chapter_number
            ),
            None,
        )
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
    from .narrative_ledger import list_narrative_ledger

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
        "ledger_entry_count": 0,
        "completed_beat_count": 0,
        "revealed_clue_count": 0,
        "narrative_promise_count": 0,
        "storyline_state_count": 0,
    }
    for payload in chapter_states:
        counts = narrative_counts(payload)
        for key, value in counts.items():
            totals[key] += value
    for payload in section_states:
        totals["unresolved_action_count"] += len(_as_list(payload.get("unresolved_actions")))
    ledger_items = list_narrative_ledger(db, project_id, chapter_id=chapter.id)
    totals["ledger_entry_count"] = len(ledger_items)
    for item in ledger_items:
        kind = str(item.get("ledger_type") or "")
        key = f"{kind}_count"
        if key in totals:
            totals[key] += 1

    warnings: list[str] = []
    if not chapter_states:
        warnings.append("chapter_narrative_state_missing")
    if estimated_scene_count > 1 and not section_states:
        warnings.append("section_scene_state_missing")
    if totals["event_count"] == 0 and totals["storyline_progress_count"] == 0:
        warnings.append("narrative_progress_missing")
    if chapter_states and not ledger_items:
        warnings.append("narrative_ledger_missing")
    if totals["narrative_promise_count"] > 0 and totals["storyline_state_count"] == 0:
        warnings.append("unanchored_narrative_promises")
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
        "chapter_summary must declare coverage_manifest.scene_count, characters, worldbuilding, relationships, and character_profiles, including explicit empty lists; "
        "each declared character needs a same-identity character_state_update, each declared setting needs a same-title worldbuilding candidate, each declared relationship needs a same-endpoint character_relationship, each declared profile change needs character_create/update, and every character/setting needs chapter_link; "
        "multi-scene chapters need 2-6 section outline nodes under the chapter node. "
        "chapter_summary must include narrative_state with events, timeline_events, "
        "foreshadowing_planted, foreshadowing_resolved, storyline_progress, "
        "new_storylines, reader_known_facts, character_known_facts, unresolved_actions. "
        "Use stable title/id/storyline fields whenever known so the narrative ledger can advance completed beats, revealed clues, promises, and storyline states. "
        "character_actions, and relationship_changes. "
        "section outline payloads may include scene_number, purpose, location, timeline, "
        "pov_character, characters, entry_state, exit_state, emotional_residue, and unresolved_actions. "
        "Every appearing character should receive character_state_update. Optional state fields are "
        + ", ".join(CHARACTER_STATE_FIELDS)
        + ". Read the current full card first; omit unchanged or unmentioned fields so their stored values are preserved. "
        "Do not rewrite age, appearance or possessions merely to satisfy appearance coverage. "
        "When no state changed, repeat only one verified existing state value verbatim."
    )
