"""Shared runtime policy for repairing incomplete cataloging candidates.

Providers commonly omit a trailing card or vary one identity label. This
module keeps valid cards, reconciles deterministic identity differences, and
turns every remaining hard gap into an incremental repair turn.

Cataloging submodules are loaded dynamically so installation from the package
initializer does not introduce a static import cycle.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from importlib import import_module
from typing import Any

from ...database.models import CatalogingCandidate, Character, WorldbuildingEntry
from .repair_identity import (
    candidate_character_name,
    candidate_payload,
    candidate_type,
    canonicalize,
    filter_diagnostics,
    has_stable_profile_evidence,
    identity,
    is_anonymous_character,
    worldbuilding_alias_map,
)

_INSTALLED = False
_ORIGINAL_INSPECT: Callable[..., Any] | None = None
_ORIGINAL_ERROR_MESSAGE: Callable[..., str] | None = None
_ORIGINAL_REVIEW_MESSAGE: Callable[..., str] | None = None
_ORIGINAL_CLEAR_CANDIDATES: Callable[..., None] | None = None
_ORIGINAL_TRY_CREATE_CANDIDATES: Callable[..., list[dict[str, Any]]] | None = None
_ORIGINAL_PROFILE_CHECK: Callable[[dict[str, Any]], bool] | None = None
_ORIGINAL_CANDIDATE_RULES: Callable[[], str] | None = None

_REPAIR_PROMPT = """
【候选缺项自动修复】
- 当用户消息包含“上一轮校验未通过”时，这是增量修复回合。系统会保留上一轮已经
  通过的候选；只补充错误信息明确指出的缺失身份，或修正身份不一致的候选，不要
  删除、缩减或改写已有正确卡片。
- 结束前逐项核对 coverage_manifest 与候选：角色对应 character_state_update；
  真正变化的设定对应 worldbuilding_create/update/timeline；角色资料对应
  character_create/update；关系对应 character_relationship；每个角色和设定都有
  chapter_link。
- 已存在且本章只是引用、没有新增或变化的世界观，不要虚构 update；保留其清单
  身份并输出同标题 chapter_link 即可。
- coverage_manifest 中的名称必须与候选 name/title 完全一致。说明性后缀放进
  content/description，不要把“系统”改写成带说明性括号后缀的新标题。
- “神秘人影、陌生声音、黑影、蒙面人”等尚无稳定身份的描述，只作为本章线索；
  除非正文已提供可持续使用的稳定档案，否则不要创建空白永久角色卡。
- 如果输出很长，优先保证清单中每个身份都有对应候选，再补充非必需时间线与说明。
""".strip()


def _load_existing_identities(
    db: Any,
    project_id: str | None,
) -> tuple[set[str], set[str]]:
    if db is None or not project_id:
        return set(), set()
    worldbuilding = {
        identity(row.title)
        for row in db.query(WorldbuildingEntry)
        .filter(WorldbuildingEntry.project_id == project_id)
        .all()
        if identity(row.title)
    }
    characters = {
        identity(row.name)
        for row in db.query(Character)
        .filter(Character.project_id == project_id)
        .all()
        if identity(row.name)
    }
    return worldbuilding, characters


def _anonymous_with_stable_cards(items: Iterable[Any]) -> set[str]:
    result: set[str] = set()
    for item in items:
        if candidate_type(item) not in {"character_create", "character_update"}:
            continue
        name = candidate_character_name(item)
        payload = candidate_payload(item)
        if name and is_anonymous_character(name) and has_stable_profile_evidence(payload):
            result.add(name)
    return result


def _reject_weak_anonymous_cards(items: Iterable[Any], unresolved: set[str]) -> bool:
    changed = False
    for item in items:
        if candidate_type(item) not in {"character_create", "character_update"}:
            continue
        if candidate_character_name(item) not in unresolved:
            continue
        if isinstance(item, dict):
            if item.get("status") != "rejected":
                item["status"] = "rejected"
                item["error"] = "身份未确认且缺少稳定档案，已保留为章节线索"
                changed = True
            continue
        if str(getattr(item, "status", "") or "") != "rejected":
            item.status = "rejected"
            item.error = "身份未确认且缺少稳定档案，已保留为章节线索"
            changed = True
    return changed


def _append_extra_warning(
    warnings: list[str],
    values: set[str],
    label: str,
) -> None:
    if values:
        warnings.append(label + "：" + "、".join(sorted(values)))


def _review_warnings_for_extras(
    *,
    extra_states: set[str],
    extra_profiles: set[str],
    extra_relationships: set[str],
    extra_worldbuilding: set[str],
    extra_character_links: set[str],
    extra_worldbuilding_links: set[str],
    unresolved: set[str],
) -> list[str]:
    warnings: list[str] = []
    _append_extra_warning(
        warnings,
        extra_states,
        "角色状态候选未写入 coverage_manifest.characters",
    )
    _append_extra_warning(
        warnings,
        extra_profiles,
        "角色资料候选未写入 coverage_manifest.character_profiles",
    )
    _append_extra_warning(
        warnings,
        extra_relationships,
        "角色关系候选未写入 coverage_manifest.relationships",
    )
    _append_extra_warning(
        warnings,
        extra_worldbuilding,
        "世界观候选未写入 coverage_manifest.worldbuilding",
    )
    _append_extra_warning(warnings, extra_character_links, "章节关联包含清单外角色")
    _append_extra_warning(
        warnings,
        extra_worldbuilding_links,
        "章节关联包含清单外世界观",
    )
    _append_extra_warning(
        warnings,
        unresolved,
        "身份未确认角色按章节线索保留，不强制建立永久角色卡",
    )
    return warnings


def _reconcile_worldbuilding(
    coverage: Any,
    existing: set[str],
) -> tuple[set[str], set[str], set[str], set[str]]:
    declared = set(coverage.declared_worldbuilding_identities)
    candidates = set(coverage.worldbuilding_candidate_identities)
    links = set(coverage.chapter_link_worldbuilding_identities)
    aliases = worldbuilding_alias_map(
        declared | candidates | links | existing,
        declared,
        existing,
    )
    declared = canonicalize(declared, aliases)
    candidates = canonicalize(candidates, aliases)
    links = canonicalize(links, aliases)
    existing = canonicalize(existing, aliases)
    linked_existing = declared & links & existing
    return declared, candidates | linked_existing, links, candidates


def _candidate_sets(coverage: Any) -> dict[str, set[str]]:
    return {
        "declared_characters": set(coverage.declared_character_identities),
        "states": set(coverage.character_state_identities),
        "declared_profiles": set(coverage.declared_character_profile_identities),
        "profiles": set(coverage.character_profile_candidate_identities),
        "declared_relationships": set(coverage.declared_relationship_identities),
        "relationships": set(coverage.relationship_candidate_identities),
        "character_links": set(coverage.chapter_link_character_identities),
    }


def _reconciled_coverage(
    coverage: Any,
    *,
    sets: dict[str, set[str]],
    worldbuilding: tuple[set[str], set[str], set[str], set[str]],
    unresolved: set[str],
) -> Any:
    declared_worldbuilding, covered_worldbuilding, worldbuilding_links, raw_world = (
        worldbuilding
    )
    declared_characters = sets["declared_characters"]
    states = sets["states"]
    declared_profiles = sets["declared_profiles"] - unresolved
    profiles = sets["profiles"]
    declared_relationships = sets["declared_relationships"]
    relationships = sets["relationships"]
    character_links = sets["character_links"]

    extras = _review_warnings_for_extras(
        extra_states=states - declared_characters,
        extra_profiles=profiles - declared_profiles,
        extra_relationships=relationships - declared_relationships,
        extra_worldbuilding=raw_world - declared_worldbuilding,
        extra_character_links=character_links - declared_characters,
        extra_worldbuilding_links=worldbuilding_links - declared_worldbuilding,
        unresolved=unresolved,
    )
    persistence_missing = filter_diagnostics(
        coverage.persistence_missing,
        prefixes=(
            "character_create/update for new declared characters",
            "relationship endpoints without character profiles",
        ),
        excluded=unresolved,
    )
    states &= declared_characters
    profiles &= declared_profiles
    relationships &= declared_relationships
    character_links &= declared_characters
    covered_worldbuilding &= declared_worldbuilding
    worldbuilding_links &= declared_worldbuilding
    review_warnings = tuple(dict.fromkeys([*coverage.review_warnings, *extras]))

    return replace(
        coverage,
        declared_character_profile_count=len(declared_profiles),
        character_state_count=len(states),
        worldbuilding_candidate_count=len(covered_worldbuilding),
        relationship_candidate_count=len(relationships),
        character_profile_candidate_count=len(profiles),
        declared_worldbuilding_count=len(declared_worldbuilding),
        declared_character_profile_identities=tuple(sorted(declared_profiles)),
        character_state_identities=tuple(sorted(states)),
        worldbuilding_candidate_identities=tuple(sorted(covered_worldbuilding)),
        relationship_candidate_identities=tuple(sorted(relationships)),
        character_profile_candidate_identities=tuple(sorted(profiles)),
        declared_worldbuilding_identities=tuple(sorted(declared_worldbuilding)),
        chapter_link_character_identities=tuple(sorted(character_links)),
        chapter_link_worldbuilding_identities=tuple(sorted(worldbuilding_links)),
        persistence_missing=persistence_missing,
        review_warnings=review_warnings,
    )


def _reconcile_coverage(
    coverage: Any,
    items: list[Any],
    *,
    db: Any,
    project_id: str | None,
) -> Any:
    existing_worldbuilding, existing_characters = _load_existing_identities(
        db,
        project_id,
    )
    stable_anonymous = _anonymous_with_stable_cards(items)
    unresolved = {
        name
        for name in coverage.declared_character_identities
        if (
            name not in existing_characters
            and name not in stable_anonymous
            and is_anonymous_character(name)
        )
    }
    if _reject_weak_anonymous_cards(items, unresolved):
        assert _ORIGINAL_INSPECT is not None
        coverage = _ORIGINAL_INSPECT(items, db=db, project_id=project_id)
    sets = _candidate_sets(coverage)
    worldbuilding = _reconcile_worldbuilding(coverage, existing_worldbuilding)
    return _reconciled_coverage(
        coverage,
        sets=sets,
        worldbuilding=worldbuilding,
        unresolved=unresolved,
    )


def _inspect_candidate_coverage(
    candidates: Iterable[Any],
    *,
    db: Any = None,
    project_id: str | None = None,
) -> Any:
    assert _ORIGINAL_INSPECT is not None
    items = list(candidates)
    coverage = _ORIGINAL_INSPECT(items, db=db, project_id=project_id)
    return _reconcile_coverage(
        coverage,
        items,
        db=db,
        project_id=project_id,
    )


def _candidate_coverage_should_retry(coverage: Any) -> bool:
    return bool(coverage.cli_parity_missing)


def _diagnostic_details(coverage: Any) -> list[str]:
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
        label + "：" + "、".join(sorted(values))
        for values, label in pairs
        if values
    ]


def _candidate_coverage_error_message(
    coverage: Any,
    *,
    prefix: str = "候选覆盖不完整",
) -> str:
    assert _ORIGINAL_ERROR_MESSAGE is not None
    base = _ORIGINAL_ERROR_MESSAGE(coverage, prefix=prefix)
    details = _diagnostic_details(coverage)
    return base if not details else base + "；" + "；".join(details)


def _candidate_coverage_review_message(coverage: Any) -> str:
    assert _ORIGINAL_REVIEW_MESSAGE is not None
    return _ORIGINAL_REVIEW_MESSAGE(coverage)


def _clear_candidates_for_run(db: Any, run: Any) -> None:
    assert _ORIGINAL_CLEAR_CANDIDATES is not None
    existing = (
        db.query(CatalogingCandidate.id)
        .filter(
            CatalogingCandidate.chapter_run_id == run.id,
            CatalogingCandidate.status.notin_(["rejected", "applied"]),
        )
        .first()
    )
    if existing is not None and str(getattr(run, "status", "") or "") == "extracting":
        return
    _ORIGINAL_CLEAR_CANDIDATES(db, run)


def _reject_created_anonymous_cards(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for result in results:
        candidate = result.get("candidate")
        if candidate is None:
            continue
        if candidate_type(candidate) not in {"character_create", "character_update"}:
            continue
        payload = candidate_payload(candidate)
        name = candidate_character_name(candidate)
        if not is_anonymous_character(name) or has_stable_profile_evidence(payload):
            continue
        candidate.status = "rejected"
        candidate.error = "身份未确认且缺少稳定档案，已保留为章节线索"
        result.pop("candidate", None)
        result["skipped"] = True
        result["reason"] = candidate.error
    return results


def _try_create_candidates(
    db: Any,
    job: Any,
    run: Any,
    line: str,
    sort_order: int,
) -> list[dict[str, Any]]:
    assert _ORIGINAL_TRY_CREATE_CANDIDATES is not None
    existing_count = (
        db.query(CatalogingCandidate)
        .filter(CatalogingCandidate.chapter_run_id == run.id)
        .count()
    )
    results = _ORIGINAL_TRY_CREATE_CANDIDATES(
        db,
        job,
        run,
        line,
        max(int(sort_order or 0), int(existing_count or 0)),
    )
    return _reject_created_anonymous_cards(results)


def _has_meaningful_character_profile(payload: dict[str, Any]) -> bool:
    assert _ORIGINAL_PROFILE_CHECK is not None
    if _ORIGINAL_PROFILE_CHECK(payload):
        return True
    name = (
        payload.get("name")
        or payload.get("character_name")
        or payload.get("target_name")
    )
    if is_anonymous_character(name):
        return False
    return any(
        payload.get(key) not in (None, "", [], {})
        for key in ("appearance", "age")
    )


def _candidate_rules_with_repair() -> str:
    assert _ORIGINAL_CANDIDATE_RULES is not None
    value = _ORIGINAL_CANDIDATE_RULES()
    return value if _REPAIR_PROMPT in value else value + "\n\n" + _REPAIR_PROMPT


def _install_prompt_rules(staged_prompts: Any, cataloging_source: Any) -> None:
    global _ORIGINAL_CANDIDATE_RULES
    _ORIGINAL_CANDIDATE_RULES = cataloging_source.get_cataloging_candidate_rules
    cataloging_source.get_cataloging_candidate_rules = _candidate_rules_with_repair
    if _REPAIR_PROMPT not in staged_prompts.CATALOGING_RESOLUTION_SYSTEM_PROMPT:
        staged_prompts.CATALOGING_RESOLUTION_SYSTEM_PROMPT += "\n\n" + _REPAIR_PROMPT
    if _REPAIR_PROMPT not in staged_prompts.CATALOGING_MERGED_SYSTEM_PROMPT:
        staged_prompts.CATALOGING_MERGED_SYSTEM_PROMPT += "\n\n" + _REPAIR_PROMPT


def _install_validation_rules(
    validation: Any,
    candidate_store: Any,
    granularity: Any,
) -> None:
    validation.inspect_candidate_coverage = _inspect_candidate_coverage
    validation.candidate_coverage_should_retry = _candidate_coverage_should_retry
    validation.candidate_coverage_error_message = _candidate_coverage_error_message
    validation.candidate_coverage_review_message = _candidate_coverage_review_message
    candidate_store.inspect_candidate_coverage = _inspect_candidate_coverage
    granularity._has_meaningful_character_profile = _has_meaningful_character_profile


def install_cataloging_runtime_repairs() -> None:
    """Install the shared repair policy exactly once."""

    global _INSTALLED
    global _ORIGINAL_CLEAR_CANDIDATES
    global _ORIGINAL_ERROR_MESSAGE
    global _ORIGINAL_INSPECT
    global _ORIGINAL_PROFILE_CHECK
    global _ORIGINAL_REVIEW_MESSAGE
    global _ORIGINAL_TRY_CREATE_CANDIDATES

    if _INSTALLED:
        return

    granularity = import_module("app.services.story_granularity")
    candidate_store = import_module("app.services.cataloging.candidate_store")
    validation = import_module("app.services.cataloging.candidate_validation")
    fact_store = import_module("app.services.cataloging.fact_store")
    staged_prompts = import_module("app.services.cataloging.staged_prompts")
    cataloging_source = import_module("app.prompts.cataloging_source")

    _ORIGINAL_INSPECT = validation.inspect_candidate_coverage
    _ORIGINAL_ERROR_MESSAGE = validation.candidate_coverage_error_message
    _ORIGINAL_REVIEW_MESSAGE = validation.candidate_coverage_review_message
    _ORIGINAL_CLEAR_CANDIDATES = fact_store.clear_candidates_for_run
    _ORIGINAL_TRY_CREATE_CANDIDATES = candidate_store.try_create_candidates
    _ORIGINAL_PROFILE_CHECK = granularity._has_meaningful_character_profile

    _install_validation_rules(validation, candidate_store, granularity)
    fact_store.clear_candidates_for_run = _clear_candidates_for_run
    candidate_store.try_create_candidates = _try_create_candidates
    _install_prompt_rules(staged_prompts, cataloging_source)
    _INSTALLED = True
