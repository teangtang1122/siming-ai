"""Runtime repairs for cataloging candidate completeness.

The cataloging pipeline streams useful candidates before it knows whether the
whole chapter satisfies the coverage contract. Providers often omit one or
two trailing cards, vary an identity label, or stop after a long response. A
full retry used to delete the valid cards and could fail again on a different
item. This module installs one shared repair policy for internal API, local
CLI, PC Gateway, and Android-triggered cataloging:

* keep valid staged candidates while a repair turn is running;
* retry every remaining hard coverage gap with exact missing identities;
* canonicalize harmless worldbuilding title decorations;
* accept existing unchanged settings when they are linked to the chapter;
* keep anonymous/unknown figures chapter-local instead of forcing blank cards;
* move surplus candidate identities to review warnings instead of blocking.

The hooks are installed from ``cataloging.__init__`` before the orchestrator
imports these functions. Keeping the policy in one module makes the behavior
identical for all front ends while avoiding a second mobile-only validator.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Iterable

from ...database.models import CatalogingCandidate, Character, WorldbuildingEntry
from .. import story_granularity as granularity
from . import candidate_store
from . import candidate_validation as validation
from . import fact_store, staged_prompts

_INSTALLED = False

_REPAIR_PROMPT = r"""
【候选缺项自动修复】
- 当用户消息包含“上一轮校验未通过”时，这是增量修复回合。系统会保留上一轮已经通过的候选；只补充错误信息明确指出的缺失身份，或修正身份不一致的候选，不要为了重试删除、缩减或改写已有正确卡片。
- 结束前逐项核对 coverage_manifest 与候选：characters 对应 character_state_update；worldbuilding 中真正新增、变化、确认、受损、受限或被使用的设定对应 worldbuilding_create/update/timeline；character_profiles 对应 character_create/update；relationships 对应 character_relationship；每个角色和设定都有 chapter_link。
- 已存在且本章只是引用、没有新增或变化的世界观，不要虚构 update；保留其清单身份并输出同标题 chapter_link 即可。
- coverage_manifest 中的名称必须与候选 name/title 完全一致。说明性后缀放进 content/description，不要把“系统”改写成“系统（无界面·无沟通·自行探索型）”之类的新标题。
- “神秘人影、陌生声音、黑影、蒙面人”等尚无稳定身份的描述，只作为本章出场线索和 chapter_link/摘要信息；除非正文已提供可持续使用的稳定档案，否则不要放入 character_profiles，也不要创建空白永久角色卡。
- 如果输出很长，优先保证清单中每个身份都有对应候选，再补充非必需时间线与说明；不得在已经声明完整清单后提前结束。
""".strip()

_ANONYMOUS_CHARACTER_EXACT = {
    "人影",
    "身影",
    "黑影",
    "神秘人影",
    "神秘身影",
    "陌生人",
    "陌生人影",
    "陌生声音",
    "神秘声音",
    "未知声音",
    "无名者",
    "来人",
    "访客",
    "袭击者",
    "追兵",
    "蒙面人",
    "黑衣人",
    "斗篷人",
}
_ANONYMOUS_CHARACTER_PATTERN = re.compile(
    r"^(?:神秘|陌生|未知|不明|模糊|黑衣|蒙面|斗篷|无名|某)"
    r"(?:人|人影|身影|声音|女子|男子|老人|少年|少女|修士|来客|存在|角色|者)?"
    r"(?:[甲乙丙丁]|\d+)?$"
)
_TRAILING_DESCRIPTOR = re.compile(
    r"(?:[（(【\[][^（）()【】\[\]]{2,80}[）)】\]])+$"
)


def _identity(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def _worldbuilding_base(value: Any) -> str:
    raw = _identity(value)
    if not raw:
        return ""
    base = _TRAILING_DESCRIPTOR.sub("", raw).strip("-—:：·")
    return base or raw


def _is_unresolved_character(value: Any) -> bool:
    raw = _identity(value)
    if not raw:
        return False
    return raw in _ANONYMOUS_CHARACTER_EXACT or bool(
        _ANONYMOUS_CHARACTER_PATTERN.fullmatch(raw)
    )


def _split_diagnostic_identities(item: str, prefix: str) -> list[str] | None:
    if not item.startswith(prefix):
        return None
    _, separator, detail = item.partition(": ")
    if not separator:
        return []
    return [part.strip() for part in detail.split("、") if part.strip()]


def _filter_diagnostic_identities(
    items: Iterable[str],
    *,
    prefixes: tuple[str, ...],
    excluded: set[str],
) -> tuple[str, ...]:
    result: list[str] = []
    for item in items:
        replaced = False
        for prefix in prefixes:
            identities = _split_diagnostic_identities(item, prefix)
            if identities is None:
                continue
            kept = [name for name in identities if _identity(name) not in excluded]
            if kept:
                result.append(f"{prefix}: " + "、".join(kept))
            replaced = True
            break
        if not replaced:
            result.append(item)
    return tuple(result)


def _worldbuilding_alias_map(
    values: Iterable[str],
    declared: set[str],
) -> dict[str, str]:
    values = {item for item in values if item}
    by_base: dict[str, set[str]] = {}
    for value in values:
        by_base.setdefault(_worldbuilding_base(value), set()).add(value)

    result = {value: value for value in values}
    for base, variants in by_base.items():
        declared_variants = variants & declared
        if len(declared_variants) == 1:
            canonical = next(iter(declared_variants))
        elif base in variants:
            canonical = base
        elif len(variants) == 1:
            canonical = next(iter(variants))
        else:
            # Ambiguous decorated titles remain separate and require review.
            continue
        for variant in variants:
            result[variant] = canonical
    return result


def _canonicalize(values: Iterable[str], aliases: dict[str, str]) -> set[str]:
    return {aliases.get(value, value) for value in values if value}


def _diagnostic_details(coverage: granularity.CandidateCoverage) -> list[str]:
    details: list[str] = []
    missing_states = set(coverage.declared_character_identities) - set(
        coverage.character_state_identities
    )
    missing_worldbuilding = set(coverage.declared_worldbuilding_identities) - set(
        coverage.worldbuilding_candidate_identities
    )
    missing_relationships = set(coverage.declared_relationship_identities) - set(
        coverage.relationship_candidate_identities
    )
    missing_profiles = set(coverage.declared_character_profile_identities) - set(
        coverage.character_profile_candidate_identities
    )
    missing_character_links = set(coverage.declared_character_identities) - set(
        coverage.chapter_link_character_identities
    )
    missing_worldbuilding_links = set(coverage.declared_worldbuilding_identities) - set(
        coverage.chapter_link_worldbuilding_identities
    )
    if missing_states:
        details.append("缺少角色状态候选：" + "、".join(sorted(missing_states)))
    if missing_worldbuilding:
        details.append(
            "缺少世界观候选或既有设定关联："
            + "、".join(sorted(missing_worldbuilding))
        )
    if missing_relationships:
        details.append("缺少角色关系候选：" + "、".join(sorted(missing_relationships)))
    if missing_profiles:
        details.append("缺少角色资料候选：" + "、".join(sorted(missing_profiles)))
    if missing_character_links:
        details.append("缺少角色章节关联：" + "、".join(sorted(missing_character_links)))
    if missing_worldbuilding_links:
        details.append(
            "缺少世界观章节关联：" + "、".join(sorted(missing_worldbuilding_links))
        )
    return details


def install_cataloging_runtime_repairs() -> None:
    """Install one idempotent completeness/repair policy for cataloging."""

    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_inspect = validation.inspect_candidate_coverage
    original_error_message = validation.candidate_coverage_error_message
    original_review_message = validation.candidate_coverage_review_message
    original_clear_candidates = fact_store.clear_candidates_for_run
    original_try_create_candidates = candidate_store.try_create_candidates
    original_profile_check = granularity._has_meaningful_character_profile
    original_candidate_rules = None

    try:
        from ...prompts import cataloging_source

        original_candidate_rules = cataloging_source.get_cataloging_candidate_rules
    except Exception:  # pragma: no cover - prompt module is always present in production
        cataloging_source = None

    def inspect_candidate_coverage(
        candidates: Iterable[Any],
        *,
        db: Any = None,
        project_id: str | None = None,
    ) -> granularity.CandidateCoverage:
        items = list(candidates)
        coverage = original_inspect(items, db=db, project_id=project_id)

        declared_characters = set(coverage.declared_character_identities)
        character_states = set(coverage.character_state_identities)
        declared_profiles = set(coverage.declared_character_profile_identities)
        profile_candidates = set(coverage.character_profile_candidate_identities)
        declared_relationships = set(coverage.declared_relationship_identities)
        relationship_candidates = set(coverage.relationship_candidate_identities)
        character_links = set(coverage.chapter_link_character_identities)

        declared_worldbuilding = set(coverage.declared_worldbuilding_identities)
        worldbuilding_candidates = set(coverage.worldbuilding_candidate_identities)
        worldbuilding_links = set(coverage.chapter_link_worldbuilding_identities)
        existing_worldbuilding: set[str] = set()
        existing_characters: set[str] = set()
        if db is not None and project_id:
            existing_worldbuilding = {
                _identity(row.title)
                for row in db.query(WorldbuildingEntry)
                .filter(WorldbuildingEntry.project_id == project_id)
                .all()
                if _identity(row.title)
            }
            existing_characters = {
                _identity(row.name)
                for row in db.query(Character)
                .filter(Character.project_id == project_id)
                .all()
                if _identity(row.name)
            }

        world_values = (
            declared_worldbuilding
            | worldbuilding_candidates
            | worldbuilding_links
            | existing_worldbuilding
        )
        world_aliases = _worldbuilding_alias_map(
            world_values,
            declared_worldbuilding,
        )
        declared_worldbuilding = _canonicalize(
            declared_worldbuilding,
            world_aliases,
        )
        worldbuilding_candidates = _canonicalize(
            worldbuilding_candidates,
            world_aliases,
        )
        worldbuilding_links = _canonicalize(worldbuilding_links, world_aliases)
        existing_worldbuilding = _canonicalize(
            existing_worldbuilding,
            world_aliases,
        )

        # A pre-existing setting that is merely referenced does not need a fake
        # update card. Its same-title chapter_link is the persistence action.
        linked_existing = (
            declared_worldbuilding & worldbuilding_links & existing_worldbuilding
        )
        covered_worldbuilding = worldbuilding_candidates | linked_existing

        extra_states = character_states - declared_characters
        extra_profiles = profile_candidates - declared_profiles
        extra_relationships = relationship_candidates - declared_relationships
        extra_worldbuilding = worldbuilding_candidates - declared_worldbuilding
        extra_character_links = character_links - declared_characters
        extra_worldbuilding_links = worldbuilding_links - declared_worldbuilding

        unresolved_characters = {
            identity
            for identity in declared_characters
            if identity not in existing_characters and _is_unresolved_character(identity)
        }
        declared_profiles -= unresolved_characters

        review_warnings = list(coverage.review_warnings)
        if extra_states:
            review_warnings.append(
                "角色状态候选未写入 coverage_manifest.characters："
                + "、".join(sorted(extra_states))
            )
        if extra_profiles:
            review_warnings.append(
                "角色资料候选未写入 coverage_manifest.character_profiles："
                + "、".join(sorted(extra_profiles))
            )
        if extra_relationships:
            review_warnings.append(
                "角色关系候选未写入 coverage_manifest.relationships："
                + "、".join(sorted(extra_relationships))
            )
        if extra_worldbuilding:
            review_warnings.append(
                "世界观候选未写入 coverage_manifest.worldbuilding："
                + "、".join(sorted(extra_worldbuilding))
            )
        if extra_character_links:
            review_warnings.append(
                "章节关联包含清单外角色：" + "、".join(sorted(extra_character_links))
            )
        if extra_worldbuilding_links:
            review_warnings.append(
                "章节关联包含清单外世界观："
                + "、".join(sorted(extra_worldbuilding_links))
            )
        if unresolved_characters:
            review_warnings.append(
                "身份未确认角色按章节线索保留，不强制建立永久角色卡："
                + "、".join(sorted(unresolved_characters))
            )

        persistence_missing = _filter_diagnostic_identities(
            coverage.persistence_missing,
            prefixes=(
                "character_create/update for new declared characters",
                "relationship endpoints without character profiles",
            ),
            excluded=unresolved_characters,
        )

        # Surplus cards are useful data and should be reviewed, not treated as
        # data loss. Contract counts below only include declared identities.
        character_states &= declared_characters
        profile_candidates &= declared_profiles
        relationship_candidates &= declared_relationships
        character_links &= declared_characters
        covered_worldbuilding &= declared_worldbuilding
        worldbuilding_links &= declared_worldbuilding

        return replace(
            coverage,
            declared_character_profile_count=len(declared_profiles),
            character_state_count=len(character_states),
            worldbuilding_candidate_count=len(covered_worldbuilding),
            relationship_candidate_count=len(relationship_candidates),
            character_profile_candidate_count=len(profile_candidates),
            declared_worldbuilding_count=len(declared_worldbuilding),
            declared_character_profile_identities=tuple(sorted(declared_profiles)),
            character_state_identities=tuple(sorted(character_states)),
            worldbuilding_candidate_identities=tuple(sorted(covered_worldbuilding)),
            relationship_candidate_identities=tuple(sorted(relationship_candidates)),
            character_profile_candidate_identities=tuple(sorted(profile_candidates)),
            declared_worldbuilding_identities=tuple(sorted(declared_worldbuilding)),
            chapter_link_character_identities=tuple(sorted(character_links)),
            chapter_link_worldbuilding_identities=tuple(sorted(worldbuilding_links)),
            persistence_missing=persistence_missing,
            review_warnings=tuple(dict.fromkeys(review_warnings)),
        )

    def candidate_coverage_should_retry(
        coverage: granularity.CandidateCoverage,
    ) -> bool:
        # After deterministic reconciliation, every remaining hard gap is a
        # real missing card and should receive an incremental model repair turn.
        return bool(coverage.cli_parity_missing)

    def candidate_coverage_error_message(
        coverage: granularity.CandidateCoverage,
        *,
        prefix: str = "候选覆盖不完整",
    ) -> str:
        base = original_error_message(coverage, prefix=prefix)
        details = _diagnostic_details(coverage)
        return base if not details else base + "；" + "；".join(details)

    def candidate_coverage_review_message(
        coverage: granularity.CandidateCoverage,
    ) -> str:
        return original_review_message(coverage)

    def clear_candidates_for_run(db: Any, run: Any) -> None:
        # The first pass has no cards. Any later call while extracting is a
        # repair/retry pass, so retain its valid staged cards and merge fixes.
        existing = (
            db.query(CatalogingCandidate.id)
            .filter(
                CatalogingCandidate.chapter_run_id == run.id,
                CatalogingCandidate.status.notin_(["rejected", "applied"]),
            )
            .first()
        )
        if (
            existing is not None
            and str(getattr(run, "status", "") or "") == "extracting"
        ):
            return
        original_clear_candidates(db, run)

    def try_create_candidates(
        db: Any,
        job: Any,
        run: Any,
        line: str,
        sort_order: int,
    ) -> list[dict[str, Any]]:
        # The orchestrator resets its local counter after requesting a retry.
        # Preserved cards still occupy sort positions, so continue after the
        # actual staged row count instead of reusing zero-based positions.
        existing_count = (
            db.query(CatalogingCandidate)
            .filter(CatalogingCandidate.chapter_run_id == run.id)
            .count()
        )
        return original_try_create_candidates(
            db,
            job,
            run,
            line,
            max(int(sort_order or 0), int(existing_count or 0)),
        )

    def has_meaningful_character_profile(payload: dict[str, Any]) -> bool:
        if original_profile_check(payload):
            return True
        name = (
            payload.get("name")
            or payload.get("character_name")
            or payload.get("target_name")
        )
        if _is_unresolved_character(name):
            return False
        # Character rows persist appearance and age, so a stable named newcomer
        # with grounded visual/age evidence is not an identity-only blank card.
        return any(
            payload.get(key) not in (None, "", [], {})
            for key in ("appearance", "age")
        )

    validation.inspect_candidate_coverage = inspect_candidate_coverage
    validation.candidate_coverage_should_retry = candidate_coverage_should_retry
    validation.candidate_coverage_error_message = candidate_coverage_error_message
    validation.candidate_coverage_review_message = candidate_coverage_review_message
    fact_store.clear_candidates_for_run = clear_candidates_for_run
    candidate_store.try_create_candidates = try_create_candidates
    granularity._has_meaningful_character_profile = has_meaningful_character_profile

    if _REPAIR_PROMPT not in staged_prompts.CATALOGING_RESOLUTION_SYSTEM_PROMPT:
        staged_prompts.CATALOGING_RESOLUTION_SYSTEM_PROMPT += "\n\n" + _REPAIR_PROMPT
    if _REPAIR_PROMPT not in staged_prompts.CATALOGING_MERGED_SYSTEM_PROMPT:
        staged_prompts.CATALOGING_MERGED_SYSTEM_PROMPT += "\n\n" + _REPAIR_PROMPT

    if cataloging_source is not None and original_candidate_rules is not None:

        def get_cataloging_candidate_rules() -> str:
            value = original_candidate_rules()
            return value if _REPAIR_PROMPT in value else value + "\n\n" + _REPAIR_PROMPT

        cataloging_source.get_cataloging_candidate_rules = get_cataloging_candidate_rules
