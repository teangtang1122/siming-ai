"""Identity helpers for cataloging completeness repair."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

ANONYMOUS_CHARACTER_EXACT = {
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
ANONYMOUS_CHARACTER_PATTERN = re.compile(
    r"^(?:神秘|陌生|未知|不明|模糊|黑衣|蒙面|斗篷|无名|某)"
    r"(?:人|人影|身影|声音|女子|男子|老人|少年|少女|修士|来客|存在|角色|者)?"
    r"(?:[甲乙丙丁]|\d+)?$"
)
TRAILING_DESCRIPTOR = re.compile(
    r"(?:[（(【\[][^（）()【】\[\]]{2,80}[）)】\]])+$"
)
STABLE_PROFILE_FIELDS = {
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
    "profile_json",
    "ai_config",
}


def identity(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def worldbuilding_base(value: Any) -> str:
    raw = identity(value)
    if not raw:
        return ""
    base = TRAILING_DESCRIPTOR.sub("", raw).strip("-—:：·")
    return base or raw


def is_anonymous_character(value: Any) -> bool:
    raw = identity(value)
    return bool(
        raw
        and (
            raw in ANONYMOUS_CHARACTER_EXACT
            or ANONYMOUS_CHARACTER_PATTERN.fullmatch(raw)
        )
    )


def candidate_payload(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        value = item.get("payload") if isinstance(item.get("payload"), dict) else item
        return dict(value)
    raw = getattr(item, "edited_payload", None) or getattr(item, "raw_payload", None)
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def candidate_type(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("item_type") or item.get("type") or "").strip()
    return str(getattr(item, "item_type", "") or "").strip()


def meaningful(value: Any) -> bool:
    if isinstance(value, dict):
        return any(meaningful(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(meaningful(item) for item in value)
    return bool(str(value or "").strip())


def has_stable_profile_evidence(payload: dict[str, Any]) -> bool:
    return any(meaningful(payload.get(key)) for key in STABLE_PROFILE_FIELDS)


def candidate_character_name(item: Any) -> str:
    payload = candidate_payload(item)
    return identity(
        payload.get("name")
        or payload.get("character_name")
        or payload.get("target_name")
        or getattr(item, "target_name", "")
    )


def worldbuilding_alias_map(
    values: Iterable[str],
    declared: set[str],
    existing: set[str],
) -> dict[str, str]:
    values = {value for value in values if value}
    by_base: dict[str, set[str]] = {}
    for value in values:
        by_base.setdefault(worldbuilding_base(value), set()).add(value)

    aliases = {value: value for value in values}
    for base, variants in by_base.items():
        canonical = ""
        if base in declared:
            canonical = base
        elif base in existing and len(variants & declared) == 1:
            canonical = base
        if canonical:
            for variant in variants:
                aliases[variant] = canonical
    return aliases


def canonicalize(values: Iterable[str], aliases: dict[str, str]) -> set[str]:
    return {aliases.get(value, value) for value in values if value}


def split_diagnostic_identities(item: str, prefix: str) -> list[str] | None:
    if not item.startswith(prefix):
        return None
    _, separator, detail = item.partition(": ")
    if not separator:
        return []
    return [part.strip() for part in detail.split("、") if part.strip()]


def filter_diagnostics(
    items: Iterable[str],
    *,
    prefixes: tuple[str, ...],
    excluded: set[str],
) -> tuple[str, ...]:
    result: list[str] = []
    for item in items:
        replacement: str | None = None
        matched = False
        for prefix in prefixes:
            identities = split_diagnostic_identities(item, prefix)
            if identities is None:
                continue
            matched = True
            kept = [name for name in identities if identity(name) not in excluded]
            if kept:
                replacement = f"{prefix}: " + "、".join(kept)
            break
        if replacement:
            result.append(replacement)
        elif not matched:
            result.append(item)
    return tuple(result)
