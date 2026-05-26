"""Fact extraction parsing and lookup helpers for staged cataloging."""
from __future__ import annotations

import json
from typing import Any

from .jsonl import clean_jsonl_text, parse_json_line


FACT_TYPES = {
    "chapter_overview",
    "character_fact",
    "relationship_fact",
    "worldbuilding_fact",
    "outline_fact",
    "identity_hint",
}

NAME_KEYS = {
    "name",
    "names",
    "primary_name",
    "source_name",
    "target_name",
    "character_name",
    "character_names",
    "aliases",
}

TITLE_KEYS = {
    "title",
    "title_hint",
    "worldbuilding_titles",
    "outline_title",
    "keywords",
}


def try_parse_fact_line(line: str) -> dict[str, Any]:
    text = clean_jsonl_text(line)
    if not text:
        return {}
    try:
        parsed = parse_json_line(text)
        if parsed is None:
            return {}
        fact = normalize_fact(parsed)
        if fact["fact_type"] not in FACT_TYPES:
            return {"bad_line": text, "error": f"未知 fact_type: {fact['fact_type']}"}
        return {"fact": fact, "raw": json.dumps(fact, ensure_ascii=False)}
    except Exception as exc:
        return {"bad_line": text, "error": str(exc)}


def normalize_fact(raw: dict[str, Any]) -> dict[str, Any]:
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = {key: value for key, value in raw.items() if key not in {"fact_type", "type"}}
    return {
        "fact_type": str(raw.get("fact_type") or raw.get("type") or "").strip(),
        "confidence": raw.get("confidence") or payload.get("confidence"),
        "evidence": raw.get("evidence") or payload.get("evidence"),
        "payload": payload,
    }


def facts_to_jsonl(facts: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(fact, ensure_ascii=False, separators=(",", ":")) for fact in facts)


def extract_fact_terms(facts: list[dict[str, Any]]) -> dict[str, set[str]]:
    names: set[str] = set()
    titles: set[str] = set()
    keywords: set[str] = set()
    for fact in facts:
        payload = fact.get("payload") if isinstance(fact, dict) else None
        if not isinstance(payload, dict):
            continue
        _collect_terms(payload, names, titles, keywords)
    return {
        "names": _clean_terms(names),
        "titles": _clean_terms(titles),
        "keywords": _clean_terms(keywords),
    }


def facts_text(facts: list[dict[str, Any]], limit: int = 20000) -> str:
    text = facts_to_jsonl(facts)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n{\"fact_type\":\"truncated\",\"payload\":{\"note\":\"事实列表过长，已截断\"}}"


def _collect_terms(value: Any, names: set[str], titles: set[str], keywords: set[str], current_key: str = "") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            _collect_terms(nested, names, titles, keywords, str(key))
        return
    if isinstance(value, list):
        for item in value:
            _collect_terms(item, names, titles, keywords, current_key)
        return
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    if current_key in NAME_KEYS:
        names.add(text)
    elif current_key in TITLE_KEYS:
        titles.add(text)
        keywords.add(text)
    elif current_key.endswith("_hint") or current_key.endswith("_clues"):
        keywords.add(text)


def _clean_terms(values: set[str]) -> set[str]:
    cleaned: set[str] = set()
    for value in values:
        text = " ".join(value.split()).strip(" ，。；;、")
        if 1 < len(text) <= 80:
            cleaned.add(text)
    return cleaned
