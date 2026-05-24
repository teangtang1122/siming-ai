"""JSON repair utilities for LLM outputs."""
from __future__ import annotations

import json
from typing import Optional

from ..ai.gateway import LLMGateway


def strip_json_fences(text: str) -> str:
    value = (text or "").strip()
    for _ in range(2):
        if value.startswith("```json"):
            value = value[7:]
        elif value.startswith("```"):
            value = value[3:]
        if value.endswith("```"):
            value = value[:-3]
    return value.strip()


def escape_json_string_values(text: str) -> str:
    """Escape unescaped ASCII double-quotes inside JSON string values.

    Scans the text tracking in-string / out-of-string state and escape mode.
    When a double-quote appears inside a string and is NOT followed by a JSON
    structural character (, } ] :), it is treated as an accidental unescaped
    quote (e.g. from Chinese dialogue) and escaped as \\\".
    """
    result: list[str] = []
    in_string = False
    escape_next = False
    i = 0
    while i < len(text):
        ch = text[i]
        if not in_string:
            result.append(ch)
            if ch == '"':
                in_string = True
            i += 1
        else:
            if escape_next:
                result.append(ch)
                escape_next = False
                i += 1
            elif ch == '\\':
                result.append(ch)
                escape_next = True
                i += 1
            elif ch == '"':
                ahead = i + 1
                while ahead < len(text) and text[ahead].isspace():
                    ahead += 1
                if ahead >= len(text) or text[ahead] in ',}:]':
                    in_string = False
                    result.append(ch)
                else:
                    result.append('\\')
                    result.append('"')
                i += 1
            else:
                result.append(ch)
                i += 1
    return ''.join(result)


def parse_json_object(text: str) -> Optional[dict]:
    cleaned = strip_json_fences(text)

    def _try_parse(candidate_text: str) -> Optional[dict]:
        start = candidate_text.find("{")
        if start < 0:
            return None
        for end_offset in range(len(candidate_text), start + 1, -1):
            end = candidate_text.rfind("}", start, end_offset)
            if end < 0:
                continue
            candidate = candidate_text[start:end + 1]
            try:
                parsed = json.loads(candidate, strict=False)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    parsed = _try_parse(cleaned)
    if parsed is not None:
        return parsed
    escaped = escape_json_string_values(cleaned)
    if escaped != cleaned:
        return _try_parse(escaped)
    return None


WORKSPACE_JSON_REPAIR_SYSTEM_PROMPT = (
    "你是JSON修复器，只修复语法，不改写正文，不增删工具动作。"
    "输入是小说项目助手返回的近似JSON，可能因为章节正文里的引号、换行或尾随文本导致无法解析。"
    "请把它修复为一个可被 json.loads 解析的合法JSON对象。"
    "必须保留 reply、done、actions、needs_confirmation 字段；actions 内的工具名和参数必须尽量原样保留。"
    "只输出JSON对象，不要Markdown，不要解释。"
)


async def repair_workspace_json_output(raw_text: str, model: Optional[str]) -> Optional[dict]:
    """Repair near-JSON workspace assistant output once before dropping actions."""
    if not raw_text.strip():
        return None
    try:
        result = await LLMGateway.chat_completion(
            messages=[
                {"role": "system", "content": WORKSPACE_JSON_REPAIR_SYSTEM_PROMPT},
                {"role": "user", "content": raw_text[:120_000]},
            ],
            model=model,
            temperature=0,
            timeout=90,
            retry=0,
        )
    except Exception:
        return None
    return parse_json_object(result.get("content", ""))
