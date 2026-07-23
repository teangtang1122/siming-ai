"""Normalize local CLI stdout without mistaking lifecycle events for model text."""
from __future__ import annotations

import json
from collections.abc import Callable


def _is_metadata_event(data: dict) -> bool:
    event_type = str(data.get("type") or "").strip().lower().replace("-", "_")
    part = data.get("part") if isinstance(data.get("part"), dict) else {}
    part_type = str(part.get("type") or "").strip().lower().replace("-", "_")
    metadata_types = {
        "step_start", "step_finish", "message_start", "message_finish",
        "tool_start", "tool_finish",
    }
    return event_type in metadata_types or part_type in metadata_types


def normalize_cli_output(text: str, extract_text: Callable[[dict], str]) -> str:
    """Return only model text or a direct structured payload from JSONL stdout."""
    if not text:
        return ""
    parsed_parts: list[str] = []
    raw_json_parts: list[str] = []
    plain_parts: list[str] = []
    json_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except Exception:
            plain_parts.append(stripped)
            continue
        json_lines += 1
        if not isinstance(data, dict):
            raw_json_parts.append(stripped)
            continue
        extracted = extract_text(data)
        if extracted:
            parsed_parts.append(extracted)
        elif not _is_metadata_event(data) and data.get("type") != "error" and "error" not in data:
            raw_json_parts.append(stripped)
    if parsed_parts:
        return "".join(parsed_parts).strip()
    if raw_json_parts:
        return "\n".join(raw_json_parts).strip()
    if plain_parts:
        return "\n".join(plain_parts).strip()
    return "" if json_lines else text.strip()
