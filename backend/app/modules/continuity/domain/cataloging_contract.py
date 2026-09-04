"""Canonical cataloging contracts shared by ingestion, validation, and writes."""
from __future__ import annotations

from typing import Any, Literal, get_args

CatalogingFactType = Literal[
    "chapter_overview",
    "character_fact",
    "relationship_fact",
    "worldbuilding_fact",
    "outline_fact",
    "identity_hint",
]

CATALOGING_FACT_TYPES = get_args(CatalogingFactType)

CHAPTER_CHARACTER_APPEARANCE_TYPES = frozenset({"出场", "提及", "回忆"})

# An aggregate chapter link is amended additively during ordinary incremental
# retries. When the model explicitly corrects an overdeclared alias, all of
# these collections are required so persistence can replace the complete link
# projection without guessing which old values should survive.
CHAPTER_LINK_REPLACE_LIST_FIELDS = (
    "characters",
    "worldbuilding_titles",
    "locations",
    "items",
    "events",
)

# Optional endpoint fields belong to older/non-aggregate link shapes. Clear
# them during an explicit aggregate replacement unless the model resubmits
# them, otherwise an incorrect endpoint can remain active invisibly.
CHAPTER_LINK_REPLACE_FIELDS = (
    *CHAPTER_LINK_REPLACE_LIST_FIELDS,
    "outline_title",
    "source",
    "target",
    "source_name",
    "target_name",
    "source_id",
    "target_id",
    "source_type",
    "target_type",
)


def coverage_manifest_duplicate_relationship_pairs(value: Any) -> list[str]:
    """Return repeated directed endpoint pairs without judging relationship meaning."""

    if not isinstance(value, list):
        return []
    seen: set[tuple[str, str]] = set()
    duplicates: dict[tuple[str, str], str] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_name") or item.get("source") or "").strip()
        target = str(item.get("target_name") or item.get("target") or "").strip()
        if not source or not target:
            continue
        key = ("".join(source.split()).casefold(), "".join(target.split()).casefold())
        if key in seen:
            duplicates.setdefault(key, f"{source}→{target}")
        else:
            seen.add(key)
    return sorted(duplicates.values())


def validate_coverage_manifest_relationships(payload: dict[str, Any]) -> None:
    """Require one model-selected current relationship per directed pair."""

    manifest = payload.get("coverage_manifest")
    if not isinstance(manifest, dict):
        return
    duplicates = coverage_manifest_duplicate_relationship_pairs(
        manifest.get("relationships")
    )
    if duplicates:
        raise ValueError(
            "coverage_manifest.relationships 同一有向角色对只能保留一个当前关系对象："
            + "、".join(duplicates)
            + "；请由模型选择一个当前 relationship_type，若摘要已保存则使用 "
            "coverage_manifest_mode=\"replace\" 提交完整纠正清单"
        )


def canonical_chapter_link_characters(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Return the one current chapter-character link representation.

    ``character_names`` is accepted only as a protocol-boundary migration for
    candidate rows written before appearance classification became required.
    All callers receive the current structured representation.
    """

    raw = payload.get("characters")
    if raw is None and isinstance(payload.get("character_names"), list):
        raw = [
            {"name": str(value).strip(), "appearance_type": "出场"}
            for value in payload["character_names"]
            if str(value or "").strip()
        ]
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("chapter_link.characters 必须是人物关联对象数组")

    result: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("chapter_link.characters 每项必须包含 name 与 appearance_type")
        name = str(
            item.get("name")
            or item.get("character_name")
            or item.get("character_id")
            or item.get("id")
            or ""
        ).strip()
        appearance_type = str(item.get("appearance_type") or "").strip()
        if not name or appearance_type not in CHAPTER_CHARACTER_APPEARANCE_TYPES:
            raise ValueError(
                "chapter_link.characters 每项必须明确 name，"
                "appearance_type 只能是出场、提及或回忆"
            )
        identity = "".join(name.split()).casefold()
        if identity in seen_names:
            raise ValueError(
                f"chapter_link.characters 中角色 {name} 重复；"
                "每个角色只能出现一次，并由模型选择一个 appearance_type"
            )
        seen_names.add(identity)
        result.append({"name": name, "appearance_type": appearance_type})
    return result
