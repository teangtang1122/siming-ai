"""Source-grounding checks for cataloging candidate coverage."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingFact,
    Chapter,
    Character,
)
from .repair_identity import (
    candidate_payload as _candidate_payload,
    candidate_type as _candidate_type,
    identity as _identity,
    meaningful as _meaningful,
)

def _candidate_status(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("status") or "").strip()
    return str(getattr(item, "status", "") or "").strip()

def _canonical_display_identity(value: str, identity_map: dict[str, str]) -> str:
    """Resolve provider display labels without merging conflicting people.

    Parentheses normally add an alias or role to one list item, while a slash
    can also mean two separate people.  Parenthetical labels may resolve from
    one unambiguous component; slash labels require every component to resolve
    to the same canonical card.
    """

    raw = _identity(value)
    if not raw:
        return ""
    direct = identity_map.get(raw)
    if direct:
        return direct

    match = re.fullmatch(r"(.+?)[（(]([^（）()]+)[）)]", raw)
    if match:
        components = {_identity(match.group(1)), _identity(match.group(2))}
        anchors = {identity_map[item] for item in components if item in identity_map}
        if len(anchors) == 1:
            return next(iter(anchors))
        return raw

    components = {
        _identity(part)
        for part in re.split(r"[/／|｜、]", raw)
        if _identity(part)
    }
    anchors = {identity_map[item] for item in components if item in identity_map}
    if components and len(anchors) == 1 and all(item in identity_map for item in components):
        return next(iter(anchors))
    return raw


def _source_fact_payloads(
    db: Session,
    items: list[Any],
) -> list[tuple[str, dict[str, Any]]]:
    run_id, _chapter_id = _candidate_context(items)
    if not run_id:
        return []
    rows = db.query(CatalogingFact).filter(
        CatalogingFact.chapter_run_id == run_id,
        CatalogingFact.status == "active",
    ).all()
    result: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        try:
            payload = json.loads(row.raw_payload or "{}")
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            result.append((str(row.fact_type or ""), payload))
    return result


def _value_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _fact_names(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in (
        "name",
        "character_name",
        "primary_name",
        "source_name",
        "target_name",
        "characters",
        "character_names",
        "names",
    ):
        for item in _value_items(payload.get(key)):
            if isinstance(item, dict):
                item = item.get("name") or item.get("character_name") or item.get("title")
            identity = _identity(item)
            if identity:
                names.add(identity)
    return names


def _fact_archive_identity(payload: dict[str, Any]) -> str:
    return str(payload.get("archive_identity") or "").strip().casefold()


def _fact_is_archival_character(payload: dict[str, Any]) -> bool:
    """Honor the model's structured identity decision without guessing from names."""

    return _fact_archive_identity(payload) == "stable_character"


def _fact_is_archival_worldbuilding(payload: dict[str, Any]) -> bool:
    """Honor the facts model's structured setting decision without heuristics."""

    return _fact_archive_identity(payload) == "stable_setting"


def _fact_character_names(fact_type: str, payload: dict[str, Any]) -> set[str]:
    if fact_type == "character_fact" and not _fact_is_archival_character(payload):
        return set()
    if fact_type == "chapter_overview" and "cataloging_characters" in payload:
        return _fact_names({"characters": payload.get("cataloging_characters")})
    return _fact_names(payload)


def _non_archival_fact_names(facts: list[tuple[str, dict[str, Any]]]) -> set[str]:
    result: set[str] = set()
    for fact_type, payload in facts:
        if fact_type == "character_fact" and not _fact_is_archival_character(payload):
            result.update(_fact_names(payload))
        if fact_type == "chapter_overview":
            result.update(_fact_names({"characters": payload.get("anonymous_participants")}))
    return result


def _display_identity_references_content(value: str, content: str) -> bool:
    raw = str(value or "").strip()
    if _contains_reference(content, raw):
        return True
    # One-character Chinese names are uncommon but valid. Preserve the
    # stricter threshold for generic references, but accept an exact CJK name.
    if re.fullmatch(r"[\u3400-\u9fff]", raw) and raw in content:
        return True
    parts = {
        part.strip()
        for part in re.split(r"[（()）/／|｜、]", raw)
        if part.strip()
    }
    return any(
        _contains_reference(content, part)
        or (re.fullmatch(r"[\u3400-\u9fff]", part) is not None and part in content)
        for part in parts
    )


def _grounded_fact_names(
    payload: dict[str, Any],
    identity_map: dict[str, str],
    chapter_content: str,
) -> set[str]:
    return {
        _canonical_display_identity(name, identity_map)
        for name in _fact_names(payload)
        if _display_identity_references_content(name, chapter_content)
    }


def _fact_worldbuilding_titles(fact_type: str, payload: dict[str, Any]) -> set[str]:
    if fact_type == "worldbuilding_fact" and not _fact_is_archival_worldbuilding(payload):
        return set()
    if fact_type == "chapter_overview" and "cataloging_worldbuilding_titles" in payload:
        payload = {
            "worldbuilding_titles": payload.get("cataloging_worldbuilding_titles")
        }
    titles: set[str] = set()
    for key in (
        "title",
        "entry_title",
        "worldbuilding",
        "worldbuilding_titles",
        "settings",
    ):
        for item in _value_items(payload.get(key)):
            if isinstance(item, dict):
                item = item.get("title") or item.get("name") or item.get("entry_title")
            identity = _identity(item)
            if identity:
                titles.add(identity)
    return titles


def _searchable_fragments(value: Any) -> list[str]:
    fragments: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            fragments.extend(_searchable_fragments(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            fragments.extend(_searchable_fragments(item))
    elif value is not None:
        text = str(value).strip()
        if text:
            fragments.append(text)
    return fragments


def _worldbuilding_candidate_documents(items: list[Any]) -> dict[str, str]:
    documents: dict[str, str] = {}
    for item in items:
        if _candidate_status(item) == "rejected":
            continue
        if _candidate_type(item) not in {
            "worldbuilding_create",
            "worldbuilding_update",
            "worldbuilding_timeline",
        }:
            continue
        payload = _candidate_payload(item)
        title = _identity(
            payload.get("title")
            or payload.get("entry_title")
            or payload.get("name")
            or payload.get("target_name")
        )
        if not title:
            continue
        searchable = "\n".join(_searchable_fragments(payload))
        documents[title] = f"{documents.get(title, '')}\n{searchable}".strip()
    return documents


def _worldbuilding_candidate_source_resolutions(items: list[Any]) -> dict[str, str]:
    """Return unambiguous model-declared source fact to archive mappings.

    Facts are extracted before the archive is read, so their stable label may
    differ from the active card title. The candidate model resolves that
    semantic identity by attaching source_fact_titles to a candidate selected
    with a real ID/title. The application only accepts an unambiguous mapping;
    it does not infer one from wording.
    """

    targets: dict[str, set[str]] = defaultdict(set)
    for item in items:
        if _candidate_status(item) == "rejected":
            continue
        if _candidate_type(item) not in {
            "worldbuilding_create",
            "worldbuilding_update",
            "worldbuilding_timeline",
        }:
            continue
        payload = _candidate_payload(item)
        target = _identity(
            payload.get("title")
            or payload.get("entry_title")
            or payload.get("name")
            or payload.get("target_name")
        )
        if not target:
            continue
        for value in _value_items(payload.get("source_fact_titles")):
            source = _identity(value)
            if source:
                targets[source].add(target)
    return {
        source: next(iter(values))
        for source, values in targets.items()
        if len(values) == 1
    }


def _worldbuilding_term_is_covered(
    term: str,
    declared: set[str],
    documents: dict[str, str],
    source_resolutions: dict[str, str] | None = None,
) -> bool:
    if not declared or not term:
        return False
    if term in declared:
        return True
    resolved = (source_resolutions or {}).get(term)
    if resolved and resolved in declared and resolved in documents:
        return True
    for declared_title in declared:
        document = documents.get(declared_title, "")
        # A provider may append a harmless category suffix when turning a fact
        # into a card title (游戏世界 -> 游戏世界设定).  Require containment in
        # the actual candidate title, not fuzzy edit-distance matching.
        if len(term) >= 2 and (term in declared_title or declared_title in term):
            return True
        # A narrower fact may be intentionally folded into a broader card
        # (灵气波动 -> 游戏世界设定).  Accept that only when the staged card's
        # persisted payload explicitly contains the fact term.
        if len(term) >= 2 and _contains_reference(document, term):
            return True
    return False


def _worldbuilding_expectation_terms(fact_type: str, payload: dict[str, Any]) -> set[str]:
    if fact_type == "worldbuilding_fact" and not _fact_is_archival_worldbuilding(payload):
        return set()
    terms = _fact_worldbuilding_titles(fact_type, payload)
    if fact_type == "worldbuilding_fact" and not terms:
        # The facts contract and chapter_overview validation already use
        # canonical_title_hint as the model-selected stable identity. Coverage
        # must use the same field; preferring title_hint here made API and CLI
        # validation disagree and could turn one document label into a second
        # mandatory entry.
        for key in ("canonical_title_hint", "title_hint"):
            key_terms: set[str] = set()
            for item in _value_items(payload.get(key)):
                identity = _identity(item)
                if identity:
                    key_terms.add(identity)
            if key_terms:
                terms.update(key_terms)
                break
    return terms


def _worldbuilding_term_is_grounded(
    term: str,
    fact_type: str,
    payload: dict[str, Any],
    chapter_content: str,
) -> bool:
    if _contains_reference(chapter_content, term):
        return True
    if fact_type != "worldbuilding_fact":
        return False
    for keyword in _value_items(payload.get("keywords")):
        text = str(keyword or "").strip()
        if len(_identity(text)) >= 2 and _contains_reference(chapter_content, text):
            return True
    return False


def _fact_has_character_profile_evidence(payload: dict[str, Any]) -> bool:
    """Identify facts that must update the stable character card."""
    if payload.get("stable_profile_change") is False:
        return False
    return any(
        _meaningful(payload.get(key))
        for key in (
            "aliases",
            "role_hint",
            "appearance_clues",
            "background_clues",
            "ability_clues",
            "profile",
            "profile_clues",
            "tone_style",
            "catchphrases",
            "verbosity",
            "emotion_tendency",
            "custom_system_prompt",
        )
    )


def _fact_has_grounded_character_profile_evidence(
    payload: dict[str, Any],
    chapter_content: str,
) -> bool:
    if not _fact_has_character_profile_evidence(payload):
        return False
    for key in (
        "aliases",
        "role_hint",
        "appearance_clues",
        "background_clues",
        "ability_clues",
        "profile",
        "profile_clues",
        "tone_style",
        "catchphrases",
        "verbosity",
        "emotion_tendency",
        "custom_system_prompt",
    ):
        for fragment in _searchable_fragments(payload.get(key)):
            if len(_identity(fragment)) >= 2 and _contains_reference(chapter_content, fragment):
                return True
    return False


def _fact_relationship(payload: dict[str, Any], identity_map: dict[str, str]) -> str:
    source = _identity(
        payload.get("source_name")
        or payload.get("source")
        or payload.get("character_a")
    )
    target = _identity(
        payload.get("target_name")
        or payload.get("target")
        or payload.get("character_b")
    )
    relationship_type = _identity(
        payload.get("relationship_type")
        or payload.get("relation")
    )
    if not source or not target or not relationship_type:
        return ""
    return "|".join((
        identity_map.get(source, source),
        identity_map.get(target, target),
        relationship_type,
    ))


def _fact_relationship_is_grounded(payload: dict[str, Any], chapter_content: str) -> bool:
    source = str(
        payload.get("source_name")
        or payload.get("source")
        or payload.get("character_a")
        or ""
    )
    target = str(
        payload.get("target_name")
        or payload.get("target")
        or payload.get("character_b")
        or ""
    )
    return bool(
        source
        and target
        and _display_identity_references_content(source, chapter_content)
        and _display_identity_references_content(target, chapter_content)
    )


def _candidate_context(items: list[Any]) -> tuple[str, str]:
    for item in items:
        if isinstance(item, dict):
            run_id = str(item.get("chapter_run_id") or "").strip()
            chapter_id = str(item.get("chapter_id") or "").strip()
        else:
            run_id = str(getattr(item, "chapter_run_id", "") or "").strip()
            chapter_id = str(getattr(item, "chapter_id", "") or "").strip()
        if run_id or chapter_id:
            return run_id, chapter_id
    return "", ""


def _contains_reference(content: str, value: str) -> bool:
    text = str(value or "").strip()
    if len(text) < 2:
        return False
    if re.fullmatch(r"[A-Za-z0-9_. -]+", text):
        return bool(re.search(rf"(?<!\w){re.escape(text)}(?!\w)", content, re.IGNORECASE))
    return text in content


def _source_expectations(
    db: Session,
    project_id: str,
    items: list[Any],
    characters: list[Character],
    identity_map: dict[str, str],
    created_before: Any = None,
) -> tuple[set[str], set[str], set[str], set[str]]:
    expected_characters: set[str] = set()
    expected_worldbuilding: set[str] = set()
    expected_relationships: set[str] = set()
    expected_character_profiles: set[str] = set()
    run_id, chapter_id = _candidate_context(items)
    source_facts = _source_fact_payloads(db, items) if run_id else []
    chapter_content = ""

    chapter = None
    if chapter_id:
        chapter = db.query(Chapter).filter(
            Chapter.id == chapter_id,
            Chapter.project_id == project_id,
        ).first()
    if chapter:
        chapter_content = str(chapter.content or "")

    if source_facts:
        for fact_type, payload in source_facts:
            if fact_type in {"character_fact", "relationship_fact", "chapter_overview"}:
                selected_names = _fact_character_names(fact_type, payload)
                fact_names = {
                    _canonical_display_identity(name, identity_map)
                    for name in selected_names
                    if _display_identity_references_content(name, chapter_content)
                }
                expected_characters.update(fact_names)
                if fact_type == "character_fact" and _fact_has_grounded_character_profile_evidence(
                    payload,
                    chapter_content,
                ):
                    expected_character_profiles.update(fact_names)
            if fact_type in {"worldbuilding_fact", "chapter_overview"}:
                expected_worldbuilding.update({
                    term
                    for term in _worldbuilding_expectation_terms(fact_type, payload)
                    if _worldbuilding_term_is_grounded(
                        term,
                        fact_type,
                        payload,
                        chapter_content,
                    )
                })
            if fact_type == "relationship_fact" and _fact_relationship_is_grounded(
                payload,
                chapter_content,
            ):
                relationship = _fact_relationship(payload, identity_map)
                if relationship:
                    expected_relationships.add(relationship)
    return (
        expected_characters,
        expected_worldbuilding,
        expected_relationships,
        expected_character_profiles,
    )


__all__ = [
    "_candidate_context",
    "_canonical_display_identity",
    "_source_expectations",
    "_source_fact_payloads",
    "_value_items",
    "_worldbuilding_candidate_documents",
    "_worldbuilding_candidate_source_resolutions",
    "_worldbuilding_term_is_covered",
]
