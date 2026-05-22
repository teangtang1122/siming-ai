"""JSON extraction and repair utilities for deconstruct pipeline.

Pure functions for cleaning, parsing, and repairing LLM JSON output.
The async repair function accepts its dependencies (prompts, limits) as
parameters to avoid circular imports with the prompts and gateway modules.
"""

import json
import re
from typing import Optional


def strip_json_fences(text_result: str) -> str:
    """Remove markdown code fences from model output."""
    cleaned = text_result.strip().lstrip("﻿")
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def remove_trailing_commas(text: str) -> str:
    """Remove trailing commas before closing brackets/braces."""
    return re.sub(r",(\s*[}\]])", r"\1", text)


def normalize_json_punctuation(text: str) -> str:
    """Replace Chinese quotation marks with ASCII equivalents."""
    return (
        text
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )


def repair_truncated_json(candidate: str) -> Optional[str]:
    """Conservatively close a JSON object cut off by token limits."""
    repaired = candidate.strip()
    if not repaired.startswith("{"):
        return None

    stack: list[str] = []
    in_string = False
    escape = False
    for char in repaired:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]":
            if stack and stack[-1] == char:
                stack.pop()

    if not stack and not in_string:
        return None

    if in_string:
        repaired += '"'

    repaired = repaired.rstrip()
    for _ in range(3):
        next_text = re.sub(r',?\s*"[^"\\]*(?:\\.[^"\\]*)*"\s*:\s*$', "", repaired).rstrip()
        if next_text == repaired:
            break
        repaired = next_text
    repaired = re.sub(r"[:,]\s*$", "", repaired).rstrip()
    repaired += "".join(reversed(stack))
    return remove_trailing_commas(repaired)


def extract_json(text_result: str) -> dict:
    """Best-effort JSON extraction from model output."""
    cleaned = normalize_json_punctuation(strip_json_fences(text_result))
    if not cleaned:
        raise json.JSONDecodeError("empty response", text_result, 0)

    decoder = json.JSONDecoder()
    start = cleaned.find("{")
    if start < 0:
        raise json.JSONDecodeError("missing JSON object", cleaned, 0)
    candidate = cleaned[start:]

    try:
        parsed, _ = decoder.raw_decode(remove_trailing_commas(candidate))
        return parsed
    except json.JSONDecodeError as first_error:
        end = candidate.rfind("}")
        if end > 0:
            try:
                return json.loads(remove_trailing_commas(candidate[:end + 1]))
            except json.JSONDecodeError:
                pass

        repaired = repair_truncated_json(candidate)
        if repaired:
            try:
                parsed = json.loads(repaired)
                if isinstance(parsed, dict):
                    parsed["_json_repaired"] = True
                return parsed
            except json.JSONDecodeError:
                pass
        raise first_error


def parse_model_json(text_result: str) -> tuple[Optional[dict], Optional[str]]:
    """Try to parse model output as JSON; return (dict, None) or (None, error_code)."""
    try:
        return extract_json(text_result), None
    except json.JSONDecodeError as exc:
        if not text_result.strip():
            return None, "empty_response"
        message = str(exc).lower()
        if "unterminated" in message or "expecting value" in message:
            return None, "truncated_json"
        return None, "parse_failed"
