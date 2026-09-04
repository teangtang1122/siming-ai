"""Validation helpers for deciding when a cataloging chapter is writable."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from ...database.models import (
    CatalogingCandidate,
    CatalogingChapterRun,
    Character,
    CharacterAlias,
    WorldbuildingEntry,
)
from ...database.query_filters import current_worldbuilding_clause
from ..story_granularity import CandidateCoverage, inspect_candidate_coverage_items
from .repair_identity import (
    canonicalize,
    has_stable_profile_evidence,
    is_anonymous_character,
    worldbuilding_alias_map,
)
from .candidate_source_expectations import (
    _candidate_context,
    _canonical_display_identity,
    _display_identity_references_content,
    _non_archival_fact_names,
    _source_expectations,
    _source_fact_payloads,
    _value_items,
    _worldbuilding_candidate_documents,
    _worldbuilding_candidate_source_resolutions,
    _worldbuilding_term_is_covered,
)

_MISSING_ITEM_LABELS = {
    "source characters missing from coverage_manifest.characters": "原文角色未进入章节覆盖清单",
    "source worldbuilding missing from coverage_manifest.worldbuilding": (
        "原文设定未进入章节覆盖清单"
    ),
    "source relationships missing from coverage_manifest.relationships": (
        "原文角色关系未进入章节覆盖清单"
    ),
    "source character profile evidence missing from coverage_manifest.character_profiles": (
        "原文角色档案信息未进入角色资料候选"
    ),
    "character_create/update for new declared characters": "新角色缺少可落库的角色资料候选",
    "relationship endpoints without character profiles": "角色关系引用了没有资料卡的角色",
    "relationship endpoints missing from coverage_manifest.characters": (
        "角色关系中的人物未进入章节角色清单"
    ),
    "chapter summary has fewer than 40 non-whitespace characters": (
        "章节摘要少于40个非空白字符，不能作为可靠建档摘要"
    ),
    "chapter_overview scenes disagree with coverage_manifest.scene_count": (
        "事实阶段场景数与章节覆盖清单不一致"
    ),
}


def describe_candidate_coverage_missing(items: Iterable[str]) -> list[str]:
    """Translate persistence diagnostics while preserving actionable detail."""

    result: list[str] = []
    for item in items:
        raw = str(item or "").strip()
        prefix, separator, detail = raw.partition(": ")
        label = _MISSING_ITEM_LABELS.get(prefix)
        if not label:
            result.append(raw)
            continue
        result.append(f"{label}：{detail}" if separator and detail else label)
    return result


def candidate_coverage_error_message(
    coverage: CandidateCoverage,
    *,
    prefix: str = "候选覆盖不完整",
) -> str:
    missing = describe_candidate_coverage_missing(coverage.cli_parity_missing)
    details = _candidate_coverage_identity_details(coverage)
    messages = [*missing, *details]
    return prefix if not messages else f"{prefix}：" + "；".join(messages)


def candidate_coverage_review_message(coverage: CandidateCoverage) -> str:
    warnings = describe_candidate_coverage_missing(coverage.review_warnings)
    if not warnings:
        return ""
    return "候选已保留，需要核对模型抽取的原文线索：" + "；".join(warnings)


def candidate_coverage_should_retry(coverage: CandidateCoverage) -> bool:
    """Every hard gap is eligible for an incremental model repair turn."""

    return bool(coverage.cli_parity_missing)


def _candidate_coverage_identity_details(coverage: CandidateCoverage) -> list[str]:
    pairs = (
        (
            set(coverage.declared_character_identities)
            - set(coverage.character_state_identities),
            "缺少角色状态候选",
        ),
        (
            set(coverage.declared_worldbuilding_identities)
            - set(coverage.worldbuilding_candidate_identities),
            "缺少世界观候选或既有设定关联",
        ),
        (
            set(coverage.declared_relationship_identities)
            - set(coverage.relationship_candidate_identities),
            "缺少角色关系候选",
        ),
        (
            set(coverage.declared_character_profile_identities)
            - set(coverage.character_profile_candidate_identities),
            "缺少角色资料候选",
        ),
        (
            set(coverage.declared_character_identities)
            - set(coverage.chapter_link_character_identities),
            "缺少角色章节关联",
        ),
        (
            set(coverage.declared_worldbuilding_identities)
            - set(coverage.chapter_link_worldbuilding_identities),
            "缺少世界观章节关联",
        ),
    )
    return [
        f"{label}：" + "、".join(sorted(values))
        for values, label in pairs
        if values
    ]


def _identity(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def _character_identity_index(
    db: Session,
    project_id: str,
    *,
    created_before: Any = None,
) -> tuple[list[Character], set[str], dict[str, str], dict[str, str]]:
    character_query = db.query(Character).filter(Character.project_id == project_id)
    if created_before is not None:
        character_query = character_query.filter(Character.created_at <= created_before)
    characters = character_query.all()
    by_id = {row.id: _identity(row.name) for row in characters if _identity(row.name)}
    alias_query = db.query(CharacterAlias).filter(CharacterAlias.project_id == project_id)
    if created_before is not None:
        alias_query = alias_query.filter(CharacterAlias.created_at <= created_before)
    aliases = alias_query.all()
    identity_map = {canonical: canonical for canonical in by_id.values()}
    for alias in aliases:
        canonical = by_id.get(alias.character_id)
        alias_identity = _identity(alias.alias)
        if canonical and alias_identity:
            identity_map[alias_identity] = canonical
    # Model-selected database IDs are explicit references, not display names.
    # Validate coverage against the same project-scoped records used to apply.
    identity_map.update({_identity(identity): canonical for identity, canonical in by_id.items()})
    return characters, set(by_id.values()), identity_map, by_id


def _candidate_payload(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else item
        return dict(payload)
    raw = getattr(item, "edited_payload", None) or getattr(item, "raw_payload", None)
    if isinstance(raw, dict):
        return dict(raw)
    try:
        payload = json.loads(raw or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _candidate_type(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("item_type") or item.get("type") or "").strip()
    return str(getattr(item, "item_type", "") or "").strip()


def _candidate_status(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("status") or "").strip()
    return str(getattr(item, "status", "") or "").strip()


def _candidate_character_name(item: Any) -> str:
    payload = _candidate_payload(item)
    return _identity(
        payload.get("name")
        or payload.get("character_name")
        or payload.get("target_name")
        or getattr(item, "target_name", "")
    )


def _anonymous_with_stable_cards(items: Iterable[Any]) -> set[str]:
    return {
        name
        for item in items
        if _candidate_status(item) != "rejected"
        and _candidate_type(item) in {"character_create", "character_update"}
        and (name := _candidate_character_name(item))
        and is_anonymous_character(name)
        and has_stable_profile_evidence(_candidate_payload(item))
    }


def _reject_weak_anonymous_cards(items: Iterable[Any], unresolved: set[str]) -> bool:
    changed = False
    for item in items:
        if _candidate_type(item) not in {"character_create", "character_update"}:
            continue
        if _candidate_character_name(item) not in unresolved:
            continue
        if isinstance(item, dict):
            if item.get("status") != "rejected":
                item["status"] = "rejected"
                item["error"] = "身份未确认且缺少稳定档案，已保留为章节线索"
                changed = True
            continue
        if _candidate_status(item) != "rejected":
            item.status = "rejected"
            item.error = "身份未确认且缺少稳定档案，已保留为章节线索"
            changed = True
    return changed


def _candidate_character_identity_map(
    items: list[Any],
    base_map: dict[str, str],
    by_id: dict[str, str],
) -> dict[str, str]:
    """Include aliases from staged character cards without trusting conflicts.

    Candidate aliases are part of the same transactional write set as the
    coverage manifest.  Ignoring them makes a fact such as ``特昂糖`` fail to
    match the staged canonical card ``陆糖 (alias: 特昂糖)``.  An alias claimed
    by multiple cards remains deliberately unresolved.
    """

    targets: dict[str, set[str]] = defaultdict(set)
    for alias, canonical in base_map.items():
        targets[alias].add(canonical)
    for item in items:
        if _candidate_status(item) == "rejected":
            continue
        if _candidate_type(item) not in {"character_create", "character_update"}:
            continue
        payload = _candidate_payload(item)
        raw_name = _identity(
            payload.get("name")
            or payload.get("character_name")
            or payload.get("target_name")
        )
        target_id = str(
            payload.get("character_id")
            or payload.get("target_id")
            or getattr(item, "target_id", "")
            or ""
        ).strip()
        canonical = by_id.get(target_id) or base_map.get(raw_name, raw_name)
        if not canonical:
            continue
        targets[canonical].add(canonical)
        if raw_name:
            targets[raw_name].add(canonical)
        for alias in _value_items(payload.get("aliases")):
            alias_identity = _identity(alias)
            if alias_identity:
                targets[alias_identity].add(canonical)
    resolved = {
        alias: next(iter(canonicals))
        for alias, canonicals in targets.items()
        if len(canonicals) == 1
    }
    resolved.update({_identity(identity): canonical for identity, canonical in by_id.items()})
    return resolved


def _apply_identity_hints(
    identity_map: dict[str, str],
    facts: list[tuple[str, dict[str, Any]]],
) -> dict[str, str]:
    """Resolve an identity hint only when it has one known card as anchor."""

    result = dict(identity_map)
    for fact_type, payload in facts:
        if fact_type != "identity_hint":
            continue
        names = {
            _identity(item)
            for item in _value_items(payload.get("names") or payload.get("aliases"))
            if _identity(item)
        }
        anchors = {result[name] for name in names if name in result}
        if len(anchors) != 1:
            continue
        canonical = next(iter(anchors))
        for name in names:
            result[name] = canonical
    return result


def _relationship_endpoints(keys: Iterable[str]) -> set[str]:
    endpoints: set[str] = set()
    for key in keys:
        source, separator, remainder = str(key or "").partition("|")
        target, _, _relationship_type = remainder.partition("|") if separator else ("", "", "")
        if source:
            endpoints.add(source)
        if target:
            endpoints.add(target)
    return endpoints


def _canonical_relationship(key: str, identity_map: dict[str, str]) -> str:
    source, separator, remainder = str(key or "").partition("|")
    target, target_separator, relationship_type = (
        remainder.partition("|") if separator else ("", "", "")
    )
    if not separator or not target_separator:
        return str(key or "")
    return "|".join((
        identity_map.get(source, source),
        identity_map.get(target, target),
        relationship_type,
    ))


def _canonicalize_coverage(
    coverage: CandidateCoverage,
    identity_map: dict[str, str],
) -> CandidateCoverage:
    def identities(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            sorted({_canonical_display_identity(value, identity_map) for value in values if value})
        )

    def relationships(values: Iterable[str]) -> tuple[str, ...]:
        return tuple(
            sorted({_canonical_relationship(value, identity_map) for value in values if value})
        )

    declared = identities(coverage.declared_character_identities)
    states = identities(coverage.character_state_identities)
    profiles = identities(coverage.character_profile_candidate_identities)
    declared_profiles = identities(coverage.declared_character_profile_identities)
    links = identities(coverage.chapter_link_character_identities)
    declared_relationships = relationships(coverage.declared_relationship_identities)
    relationship_candidates = relationships(coverage.relationship_candidate_identities)
    return replace(
        coverage,
        declared_character_count=len(declared),
        character_state_count=len(states),
        declared_character_profile_count=len(declared_profiles),
        character_profile_candidate_count=len(profiles),
        declared_relationship_count=len(declared_relationships),
        relationship_candidate_count=len(relationship_candidates),
        declared_character_identities=declared,
        character_state_identities=states,
        declared_character_profile_identities=declared_profiles,
        character_profile_candidate_identities=profiles,
        chapter_link_character_identities=links,
        declared_relationship_identities=declared_relationships,
        relationship_candidate_identities=relationship_candidates,
    )


def _append_identity_warning(
    warnings: list[str],
    values: set[str],
    label: str,
) -> None:
    if values:
        warnings.append(f"{label}：" + "、".join(sorted(values)))


def _reconcile_candidate_policy(
    coverage: CandidateCoverage,
    items: list[Any],
    *,
    existing_characters: set[str],
    existing_worldbuilding: set[str],
) -> tuple[CandidateCoverage, set[str]]:
    """Apply the cataloging repair policy directly to the canonical coverage.

    This used to be installed by a package-import monkey patch. Keeping it in
    the validator makes API, local CLI and MCP callers execute the same code.
    """

    stable_anonymous = _anonymous_with_stable_cards(items)
    unresolved = {
        name
        for name in coverage.declared_character_identities
        if name not in existing_characters
        and name not in stable_anonymous
        and is_anonymous_character(name)
    }

    declared_characters = set(coverage.declared_character_identities)
    states = set(coverage.character_state_identities) & declared_characters
    declared_profiles = (
        set(coverage.declared_character_profile_identities) - unresolved
    )
    candidate_profiles = (
        set(coverage.character_profile_candidate_identities) - unresolved
    )
    # Incremental repair deliberately does not replay chapter_summary.  A
    # valid profile card for a character already declared by that retained
    # summary therefore extends the effective character_profiles manifest.
    # Requiring the old summary to declare the repair beforehand made a
    # successfully generated character_create/update impossible to accept.
    declared_profiles.update(candidate_profiles & declared_characters)
    profiles = candidate_profiles & declared_profiles
    declared_relationships = set(coverage.declared_relationship_identities)
    relationships = (
        set(coverage.relationship_candidate_identities) & declared_relationships
    )
    character_links = (
        set(coverage.chapter_link_character_identities) & declared_characters
    )

    declared_worldbuilding = set(coverage.declared_worldbuilding_identities)
    raw_worldbuilding = set(coverage.worldbuilding_candidate_identities)
    worldbuilding_links = set(coverage.chapter_link_worldbuilding_identities)
    aliases = worldbuilding_alias_map(
        declared_worldbuilding
        | raw_worldbuilding
        | worldbuilding_links
        | existing_worldbuilding,
        declared_worldbuilding,
        existing_worldbuilding,
    )
    declared_worldbuilding = canonicalize(declared_worldbuilding, aliases)
    raw_worldbuilding = canonicalize(raw_worldbuilding, aliases)
    worldbuilding_links = canonicalize(worldbuilding_links, aliases)
    existing_worldbuilding = canonicalize(existing_worldbuilding, aliases)
    covered_worldbuilding = raw_worldbuilding | (
        declared_worldbuilding & worldbuilding_links & existing_worldbuilding
    )
    covered_worldbuilding &= declared_worldbuilding
    worldbuilding_links &= declared_worldbuilding

    warnings = list(coverage.review_warnings)
    _append_identity_warning(
        warnings,
        set(coverage.character_state_identities) - declared_characters,
        "角色状态候选未写入 coverage_manifest.characters",
    )
    _append_identity_warning(
        warnings,
        set(coverage.character_profile_candidate_identities) - declared_profiles,
        "角色资料候选未写入 coverage_manifest.character_profiles",
    )
    _append_identity_warning(
        warnings,
        set(coverage.relationship_candidate_identities) - declared_relationships,
        "角色关系候选未写入 coverage_manifest.relationships",
    )
    _append_identity_warning(
        warnings,
        raw_worldbuilding - declared_worldbuilding,
        "世界观候选未写入 coverage_manifest.worldbuilding",
    )
    _append_identity_warning(
        warnings,
        set(coverage.chapter_link_character_identities) - declared_characters,
        "章节关联包含清单外角色",
    )
    _append_identity_warning(
        warnings,
        set(coverage.chapter_link_worldbuilding_identities) - declared_worldbuilding,
        "章节关联包含清单外世界观",
    )
    _append_identity_warning(
        warnings,
        unresolved,
        "身份未确认角色按章节线索保留，不强制建立永久角色卡",
    )

    return replace(
        coverage,
        character_state_count=len(states),
        declared_character_profile_count=len(declared_profiles),
        character_profile_candidate_count=len(profiles),
        declared_relationship_count=len(declared_relationships),
        relationship_candidate_count=len(relationships),
        declared_worldbuilding_count=len(declared_worldbuilding),
        worldbuilding_candidate_count=len(covered_worldbuilding),
        character_state_identities=tuple(sorted(states)),
        declared_character_profile_identities=tuple(sorted(declared_profiles)),
        character_profile_candidate_identities=tuple(sorted(profiles)),
        relationship_candidate_identities=tuple(sorted(relationships)),
        declared_worldbuilding_identities=tuple(sorted(declared_worldbuilding)),
        worldbuilding_candidate_identities=tuple(sorted(covered_worldbuilding)),
        chapter_link_character_identities=tuple(sorted(character_links)),
        chapter_link_worldbuilding_identities=tuple(sorted(worldbuilding_links)),
        review_warnings=tuple(dict.fromkeys(warnings)),
    ), unresolved


def _prepare_database_coverage(
    db: Session,
    project_id: str,
    items: list[Any],
    coverage: CandidateCoverage,
) -> tuple[CandidateCoverage, list[Character], set[str], dict[str, str], set[str], Any]:
    run_id, _chapter_id = _candidate_context(items)
    run = (
        db.query(CatalogingChapterRun).filter(CatalogingChapterRun.id == run_id).first()
        if run_id else None
    )
    source_baseline = (run.started_at or run.created_at) if run is not None else None
    characters, existing, database_identity_map, by_id = _character_identity_index(
        db,
        project_id,
        created_before=source_baseline,
    )
    facts = _source_fact_payloads(db, items)
    identity_map = _apply_identity_hints(
        _candidate_character_identity_map(items, database_identity_map, by_id),
        facts,
    )
    coverage = _canonicalize_coverage(coverage, identity_map)
    unresolved_before_reconcile = {
        name
        for name in coverage.declared_character_identities
        if name not in existing
        and name not in _anonymous_with_stable_cards(items)
        and is_anonymous_character(name)
    }
    if _reject_weak_anonymous_cards(items, unresolved_before_reconcile):
        coverage = inspect_candidate_coverage_items(items)
        identity_map = _apply_identity_hints(
            _candidate_character_identity_map(items, database_identity_map, by_id),
            facts,
        )
        coverage = _canonicalize_coverage(coverage, identity_map)
    entry_query = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
        current_worldbuilding_clause(WorldbuildingEntry.status),
    )
    if source_baseline is not None:
        entry_query = entry_query.filter(WorldbuildingEntry.created_at <= source_baseline)
    existing_worldbuilding = {
        title for row in entry_query.all() if (title := _identity(row.title))
    }
    coverage, unresolved = _reconcile_candidate_policy(
        coverage,
        items,
        existing_characters=existing,
        existing_worldbuilding=existing_worldbuilding,
    )
    return coverage, characters, existing, identity_map, unresolved, source_baseline


def validate_candidate_source_character_grounding(
    db: Session,
    project_id: str,
    run: CatalogingChapterRun,
    normalized: dict[str, Any],
) -> None:
    """Reject character bindings that the current chapter did not establish.

    The facts model owns the semantic decision about who appears in the
    chapter.  Candidate resolution may use the archive to select an existing
    card, but it must not turn an unnamed role into an old named character.
    This check only compares the model's structured identities with the
    current chapter, its saved facts, and project-scoped IDs/aliases.
    """

    preview = {
        "item_type": str(normalized.get("item_type") or ""),
        "status": "pending",
        "payload": normalized.get("payload")
        if isinstance(normalized.get("payload"), dict)
        else {},
        "target_id": normalized.get("target_id"),
        "target_name": normalized.get("target_name"),
        "chapter_run_id": run.id,
        "chapter_id": run.chapter_id,
    }
    staged = (
        db.query(CatalogingCandidate)
        .filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.status != "rejected",
        )
        .all()
    )
    context_items: list[Any] = [*staged, preview]
    facts = _source_fact_payloads(db, context_items)
    # Manual candidate entry may legitimately operate without a facts stage.
    # The strict binding rule applies once the two-stage cataloging contract
    # has established a source snapshot.
    if not facts:
        return

    source_baseline = run.started_at or run.created_at
    characters, _existing, base_identity_map, by_id = _character_identity_index(
        db,
        project_id,
        created_before=source_baseline,
    )
    identity_map = _apply_identity_hints(
        _candidate_character_identity_map(context_items, base_identity_map, by_id),
        facts,
    )
    grounded, _worldbuilding, _relationships, _profiles = _source_expectations(
        db,
        project_id,
        context_items,
        characters,
        identity_map,
        source_baseline,
    )
    chapter = run.chapter
    chapter_content = str(chapter.content or "") if chapter is not None else ""
    non_archival = _non_archival_fact_names(facts)

    def supported(value: Any) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return True
        identity = _identity(raw)
        canonical = _canonical_display_identity(identity, identity_map)
        if identity in non_archival or canonical in non_archival:
            return False
        return bool(
            identity in grounded
            or canonical in grounded
            or _display_identity_references_content(raw, chapter_content)
        )

    def display_value(value: Any) -> str:
        if isinstance(value, dict):
            value = (
                value.get("name")
                or value.get("character_name")
                or value.get("source_name")
                or value.get("target_name")
            )
        return str(value or "").strip()

    unsupported: list[str] = []

    def require(values: Any) -> None:
        for value in _value_items(values):
            text = display_value(value)
            if text and not supported(text):
                unsupported.append(text)

    item_type = preview["item_type"]
    payload = preview["payload"]
    if item_type in {
        "character_create",
        "character_update",
        "character_state_update",
        "character_timeline",
    }:
        identity_values = [
            normalized.get("target_id"),
            normalized.get("target_name"),
            payload.get("id"),
            payload.get("character_id"),
            payload.get("name"),
            payload.get("character_name"),
            *_value_items(payload.get("aliases")),
        ]
        if not any(supported(value) for value in identity_values if value):
            require(
                payload.get("name")
                or payload.get("character_name")
                or normalized.get("target_name")
                or normalized.get("target_id")
                or payload.get("id")
            )
    elif item_type == "character_relationship":
        require(payload.get("source_name") or payload.get("source") or payload.get("character_a"))
        require(payload.get("target_name") or payload.get("target") or payload.get("character_b"))
    elif item_type == "character_merge_candidate":
        require(payload.get("primary_name") or payload.get("primary_character_name"))
        require(payload.get("secondary_name") or payload.get("secondary_character_name"))

    if item_type == "chapter_summary":
        require(payload.get("characters"))
        manifest = payload.get("coverage_manifest")
        if isinstance(manifest, dict):
            require(manifest.get("characters"))
            require(manifest.get("character_profiles"))
            for relationship in _value_items(manifest.get("relationships")):
                if isinstance(relationship, dict):
                    require(relationship.get("source_name") or relationship.get("source"))
                    require(relationship.get("target_name") or relationship.get("target"))
    elif item_type in {"outline_create", "outline_update"}:
        require(payload.get("characters"))
        require(payload.get("related_characters"))
        require(payload.get("pov_character"))
    elif item_type == "chapter_link":
        require(payload.get("characters"))
    elif item_type in {
        "worldbuilding_create",
        "worldbuilding_update",
        "worldbuilding_timeline",
    }:
        require(payload.get("affected_characters"))

    unsupported = list(dict.fromkeys(unsupported))
    if unsupported:
        names = "、".join(unsupported)
        raise ValueError(
            "候选人物没有本版正文或事实阶段的身份依据："
            f"{names}；不得把未具名角色绑定到旧档案人物，请保留原文身份或移除该人物引用"
        )


def _referential_missing(
    coverage: CandidateCoverage,
    existing: set[str],
    unresolved: set[str],
) -> list[str]:
    missing = list(coverage.persistence_missing)
    declared = set(coverage.declared_character_identities)
    profiles = set(coverage.character_profile_candidate_identities)
    new_without_profiles = sorted(declared - existing - profiles - unresolved)
    if new_without_profiles:
        missing.append(
            "character_create/update for new declared characters: "
            + "、".join(new_without_profiles)
        )
    endpoints = _relationship_endpoints([
        *coverage.declared_relationship_identities,
        *coverage.relationship_candidate_identities,
    ])
    unknown = sorted(endpoints - (existing | profiles) - unresolved)
    if unknown:
        missing.append(
            "relationship endpoints without character profiles: " + "、".join(unknown)
        )
    undeclared = sorted(endpoints - declared)
    if undeclared:
        missing.append(
            "relationship endpoints missing from coverage_manifest.characters: "
            + "、".join(undeclared)
        )
    return missing


def _source_review_warnings(
    db: Session,
    project_id: str,
    items: list[Any],
    coverage: CandidateCoverage,
    characters: list[Character],
    identity_map: dict[str, str],
    source_baseline: Any,
) -> list[str]:
    warnings = list(coverage.review_warnings)
    summary_lengths = [
        len(re.sub(r"\s+", "", str(
            _candidate_payload(item).get("summary_text")
            or _candidate_payload(item).get("summary")
            or ""
        )))
        for item in items
        if _candidate_status(item) != "rejected"
        and _candidate_type(item) == "chapter_summary"
    ]
    if summary_lengths and max(summary_lengths) < 40:
        warnings.append("chapter summary has fewer than 40 non-whitespace characters")

    source_scene_counts = [
        len(_value_items(payload.get("scenes")))
        for fact_type, payload in _source_fact_payloads(db, items)
        if fact_type == "chapter_overview"
        and _value_items(payload.get("scenes"))
    ]
    if source_scene_counts:
        source_scene_count = max(source_scene_counts)
        if coverage.scene_count != source_scene_count:
            warnings.append(
                "chapter_overview scenes disagree with coverage_manifest.scene_count: "
                f"facts={source_scene_count}, manifest={coverage.scene_count}"
            )
    expected_characters, expected_worldbuilding, relationships, profiles = (
        _source_expectations(
            db,
            project_id,
            items,
            characters,
            identity_map,
            source_baseline,
        )
    )
    undeclared_characters = sorted(
        expected_characters - set(coverage.declared_character_identities)
    )
    if undeclared_characters:
        warnings.append(
            "source characters missing from coverage_manifest.characters: "
            + "、".join(undeclared_characters)
        )
    documents = _worldbuilding_candidate_documents(items)
    source_resolutions = _worldbuilding_candidate_source_resolutions(items)
    declared_worldbuilding = set(coverage.declared_worldbuilding_identities)
    undeclared_worldbuilding = sorted({
        term for term in expected_worldbuilding
        if not _worldbuilding_term_is_covered(
            term,
            declared_worldbuilding,
            documents,
            source_resolutions,
        )
    })
    if undeclared_worldbuilding:
        warnings.append(
            "source worldbuilding missing from coverage_manifest.worldbuilding: "
            + "、".join(undeclared_worldbuilding)
        )
    undeclared_relationships = sorted(
        relationships - set(coverage.declared_relationship_identities)
    )
    if undeclared_relationships:
        warnings.append(
            "source relationships missing from coverage_manifest.relationships: "
            + "、".join(undeclared_relationships)
        )
    undeclared_profiles = sorted(
        profiles - set(coverage.declared_character_profile_identities)
    )
    if undeclared_profiles:
        warnings.append(
            "source character profile evidence missing from coverage_manifest.character_profiles: "
            + "、".join(undeclared_profiles)
        )
    return warnings


def inspect_candidate_coverage(
    candidates: Iterable[Any],
    *,
    db: Session | None = None,
    project_id: str | None = None,
) -> CandidateCoverage:
    """Return shared coverage plus database-aware referential checks.

    The pure coverage contract prevents duplicate cards from satisfying a
    declared count.  With a session, it also guarantees that every newly
    declared character has a stable profile card and that relationships cannot
    manufacture empty character rows as a side effect.
    """

    items = list(candidates)
    coverage = inspect_candidate_coverage_items(items)
    if db is None or not project_id:
        return coverage

    coverage, characters, existing, identity_map, unresolved, source_baseline = (
        _prepare_database_coverage(
            db,
            project_id,
            items,
            coverage,
        )
    )
    missing = _referential_missing(coverage, existing, unresolved)
    review_warnings = _source_review_warnings(
        db,
        project_id,
        items,
        coverage,
        characters,
        identity_map,
        source_baseline,
    )
    if not missing and not review_warnings:
        return coverage
    return replace(
        coverage,
        persistence_missing=tuple(dict.fromkeys(missing)),
        review_warnings=tuple(dict.fromkeys(review_warnings)),
    )
