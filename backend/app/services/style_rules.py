"""Project writing style rules and forbidden sentence repair."""
from __future__ import annotations

import re
from typing import Optional

from ..ai.gateway import LLMGateway
from ..database.models import Project
from ..prompts.style_prompts import (
    DEFAULT_FORBIDDEN_SENTENCE_PATTERNS,
    build_style_repair_messages,
)


STYLE_OPTIONS = ["vivid", "concise", "serious", "humorous", "poetic"]
STYLE_PROMPTS = {
    "vivid": "请用生动形象、富有画面感的语言改写。要求：优先使用具体动作、感官细节和场景调度制造画面感；不要依赖密集比喻或华丽排比；将抽象概括转化为具体场景。",
    "concise": "请用简洁精炼的语言改写，去除冗余。要求：删除重复表述和空洞修饰词；合并可归并的句子；用精准动词和名词替代冗长形容结构；提高信息密度。",
    "serious": "请用严肃庄重的语言改写。要求：句式规整，避免口语化和俏皮话；用词精准克制，不夸张不煽情；保持客观冷静的叙事距离。",
    "humorous": "请用幽默诙谐的语言改写。要求：可运用反讽、夸张、反差、双关等手法；节奏轻快；幽默应为角色和剧情服务，而非单纯搞笑。",
    "poetic": "请用富有诗意的语言改写。要求：注重语句的韵律感和节奏美；善用意象和留白；情感含蓄有层次，避免直白抒情。",
}

FORBIDDEN_SENTENCE_REGEXES = {
    "不是……是……": [
        r"(?<!是)不是[^。！？!?；;\n]{1,80}[，,、\s]*是[^。！？!?；;\n]{1,80}",
        r"(?<!是)不是[^。！？!?；;\n]{1,80}[。！？!?；;]\s*是[^。！？!?；;\n]{1,80}",
    ],
    "不是……而是……": [
        r"(?<!是)不是[^。！？!?；;\n]{1,120}而是[^。！？!?；;\n]{1,120}",
        r"(?<!是)不是[^。！？!?；;\n]{1,80}[。！？!?；;]\s*而是[^。！？!?；;\n]{1,80}",
    ],
    "不是……却是……": [
        r"(?<!是)不是[^。！？!?；;\n]{1,120}却是[^。！？!?；;\n]{1,120}",
        r"(?<!是)不是[^。！？!?；;\n]{1,80}[。！？!?；;]\s*却是[^。！？!?；;\n]{1,80}",
    ],
    "与其说……不如说……": [
        r"与其说[\s\S]{1,120}?不如说[\s\S]{1,120}?",
    ],
    "在……中……": [
        r"在[^。；，]{2,30}中[，,\s]*[^。]{2,60}",
    ],
    "在……时……": [
        r"在[^。；，]{2,30}时[，,\s]*[^。]{2,60}",
    ],
    "随着……": [
        r"[，。！？\n]随着[^。]{5,80}",
        r"^随着[^。]{5,80}",
    ],
    "仿佛……": [
        r"仿佛[^。！？]{4,60}",
    ],
    "似乎……": [
        r"似乎[^。！？]{4,60}",
    ],
    "只见……": [
        r"只见[^。]{2,60}",
    ],
    "只听得……": [
        r"只听得[^。]{2,60}",
    ],
    "不由得……": [
        r"不由得[^。]{2,40}",
    ],
    "不禁……": [
        r"不禁[^。]{2,40}",
    ],
    "忍不住……": [
        r"忍不住[^。]{2,40}",
    ],
    "这一切都说明……": [
        r"这一切都说明[^。]{2,80}",
    ],
    "从那天起……": [
        r"从那天起[^。]{2,80}",
    ],
    "此后……": [
        r"此后[，,][^。]{2,80}",
        r"此后[^。]{2,60}",
    ],
    "与此同时……": [
        r"与此同时[，,][^。]{2,80}",
        r"与此同时[^。]{2,60}",
    ],
    "另一方面……": [
        r"另一方面[，,][^。]{2,80}",
    ],
    "很愤怒": [
        r"很愤怒",
    ],
    "感到悲伤": [
        r"感到悲伤",
    ],
    "感到恐惧": [
        r"感到恐惧",
    ],
    "显得很……": [
        r"显得很[^。]{1,30}",
    ],
    "他的眼中……": [
        r"他的眼中[^。]{2,60}",
        r"她的眼中[^。]{2,60}",
    ],
    "她的心里……": [
        r"她的心里[^。]{2,60}",
        r"他的心里[^。]{2,60}",
    ],
    "深深地": [
        r"深深地[^。]{1,30}",
    ],
    "无比": [
        r"无比[^。]{1,30}",
    ],
    "极其": [
        r"极其[^。]{1,30}",
    ],
    "一股……": [
        r"一股[^。]{1,30}",
    ],
    "一种……的感觉": [
        r"一种[^。]{1,30}的感觉",
    ],
    "令人……": [
        r"令人[^。]{1,20}",
    ],
    "让人……": [
        r"让人[^。]{1,20}",
    ],
    "充满了": [
        r"充满了[^。]{1,30}",
    ],
    "充斥着": [
        r"充斥着[^。]{1,30}",
    ],
    "缓缓地": [
        r"缓缓地",
    ],
    "默默地": [
        r"默默地",
    ],
    "静静地": [
        r"静静地",
    ],
    "淡淡地": [
        r"淡淡地",
    ],
    "微微……": [
        r"微微[一][^。]{1,20}",
        r"微微[^一。]{1,10}",
    ],
    "然而": [
        r"[，。！？\n]然而[^。]{2,60}",
        r"^然而[^。]{2,60}",
    ],
    "于是": [
        r"[，。！？\n]于是[^。]{2,60}",
    ],
    "突然": [
        r"突然[^。]{2,50}",
    ],
    "忽然": [
        r"忽然[^。]{2,50}",
    ],
    "终于": [
        r"终于[^。]{2,40}",
    ],
    "其实": [
        r"[，。！？\n]其实[^。]{2,60}",
    ],
    "总之": [
        r"总之[，,][^。]{2,60}",
    ],
    "无论如何": [
        r"无论如何[^。]{2,60}",
    ],
    "毋庸置疑": [
        r"毋庸置疑[^。]{2,60}",
    ],
    "某种程度上": [
        r"某种程度上[^。]{2,60}",
    ],
    "某种意义上": [
        r"某种意义上[^。]{2,60}",
    ],
    "彰显": [
        r"彰显[^。]{1,30}",
    ],
    "诠释": [
        r"诠释[^。]{1,30}",
    ],
    "油然而生": [
        r"油然而生",
    ],
    "心潮澎湃": [
        r"心潮澎湃",
    ],
    "这一刻": [
        r"这一刻[^。]{1,30}",
    ],
    "宛如": [
        r"宛如[^。]{1,30}",
    ],
    "由此可见": [
        r"由此可见[^。]{1,60}",
    ],
    "值得注意的是": [
        r"值得注意的是[^。]{1,60}",
    ],
}



def _project_forbidden_patterns(project: Project) -> list[str]:
    raw = (project.forbidden_sentence_patterns or DEFAULT_FORBIDDEN_SENTENCE_PATTERNS).strip()
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _generic_forbidden_regex(pattern: str) -> Optional[str]:
    if "……" not in pattern:
        return None
    pieces = [piece for piece in pattern.split("……") if piece]
    if not pieces:
        return None
    return r"[\s\S]{0,80}?".join(re.escape(piece) for piece in pieces)


def _forbidden_snippet(text: str, start: int, end: int, radius: int = 24) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = text[left:right].replace("\n", "\\n")
    if left > 0:
        snippet = "..." + snippet
    if right < len(text):
        snippet += "..."
    return snippet


def _detect_forbidden_sentence_violations(text: str, project: Project) -> list[dict]:
    if not text:
        return []
    violations: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for pattern in _project_forbidden_patterns(project):
        regexes = FORBIDDEN_SENTENCE_REGEXES.get(pattern, [])
        generic = _generic_forbidden_regex(pattern)
        if generic:
            regexes = [*regexes, generic]
        if not regexes and pattern in text:
            start = text.find(pattern)
            regexes = [re.escape(pattern)]
        for regex in regexes:
            for match in re.finditer(regex, text):
                key = (pattern, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                violations.append({
                    "pattern": pattern,
                    "snippet": _forbidden_snippet(text, match.start(), match.end()),
                    "start": match.start(),
                    "end": match.end(),
                })
                if len(violations) >= 20:
                    return violations
    return violations


def _strip_plain_text_response(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value).strip()
    return value


def _repair_token_budget(text: str, requested_max_tokens: Optional[int]) -> int:
    estimated = max(2048, int(len(text or "") * 1.8))
    if requested_max_tokens:
        estimated = max(estimated, requested_max_tokens)
    return min(24000, estimated)


def _mechanical_repair_forbidden_sentences(text: str) -> str:
    """Last-resort cleanup for the built-in contrast and AI-cliché templates."""

    def clean_tail(value: str) -> str:
        value = value.strip()
        return value[1:] if value.startswith("在") and len(value) > 1 else value

    def replace_not_is(match: re.Match) -> str:
        left = match.group("left").strip()
        right = clean_tail(match.group("right"))
        return f"{left}并非关键，关键在于{right}"

    def replace_rather(match: re.Match) -> str:
        left = match.group("left").strip()
        right = match.group("right").strip()
        return f"{left}这个判断不够准确，{right}更贴近当前情况"

    def remove_prefix(match: re.Match) -> str:
        return match.group("after") if match.group("after") else match.group(0)

    rules = [
        # Original contrast patterns
        (
            r"(?<!是)不是(?P<left>[^。！？!?；;\n]{1,80})[，,、\s]*是(?P<right>[^。！？!?；;\n]{1,80})",
            replace_not_is,
        ),
        (
            r"(?<!是)不是(?P<left>[^。！？!?；;\n]{1,80})[。！？!?；;]\s*是(?P<right>[^。！？!?；;\n]{1,80})",
            replace_not_is,
        ),
        (
            r"(?<!是)不是(?P<left>[^。！？!?；;\n]{1,120})而是(?P<right>[^。！？!?；;\n]{1,120})",
            replace_not_is,
        ),
        (
            r"(?<!是)不是(?P<left>[^。！？!?；;\n]{1,120})却是(?P<right>[^。！？!?；;\n]{1,120})",
            replace_not_is,
        ),
        (
            r"与其说(?P<left>[\s\S]{1,120}?)不如说(?P<right>[\s\S]{1,120}?)",
            replace_rather,
        ),
        # AI cliché repairs — strip filler prefixes
        (
            r"只见(?P<after>[^。！？!?\n]{2,})",
            lambda m: m.group("after").strip(),
        ),
        (
            r"只听得(?P<after>[^。！？!?\n]{2,})",
            lambda m: m.group("after").strip(),
        ),
        (
            r"不由得(?P<after>[^。！？!?\n]{2,})",
            lambda m: f"暗自{m.group('after').strip()}",
        ),
        (
            r"不禁(?P<after>[^。！？!?\n]{2,})",
            lambda m: f"默默{m.group('after').strip()}",
        ),
        # Strip omniscient transitions
        (
            r"这一切都说明[，,]?\s*",
            lambda m: "",
        ),
        (
            r"从那天起[，,]?\s*",
            lambda m: "",
        ),
        (
            r"与此同时[，,]?\s*",
            lambda m: "",
        ),
        (
            r"另一方面[，,]?\s*",
            lambda m: "",
        ),
        # Defuse emotion labels
        (
            r"很愤怒",
            lambda m: "攥紧了拳头",
        ),
        (
            r"感到悲伤",
            lambda m: "喉头发紧",
        ),
        (
            r"感到恐惧",
            lambda m: "后背发凉",
        ),
        (
            r"显得很(?P<after>[^。，,]{1,10})",
            lambda m: f"{m.group('after').strip()}",
        ),
        # Defuse eye-of-god framing
        (
            r"[他她]的眼中[，,]?\s*",
            lambda m: "",
        ),
        (
            r"[他她]的心里[，,]?\s*",
            lambda m: "",
        ),
    ]
    repaired = text
    for regex, replacer in rules:
        repaired = re.sub(regex, replacer, repaired)
    return repaired


async def _repair_forbidden_sentence_text(
    text: str,
    project: Project,
    model: Optional[str],
    max_tokens: Optional[int] = None,
) -> tuple[str, list[dict], list[dict]]:
    """Rewrite text only when it violates project-level forbidden sentence rules."""
    before = _detect_forbidden_sentence_violations(text, project)
    if not before:
        return text, [], []

    repaired = text
    remaining = before
    patterns = _project_forbidden_patterns(project)
    for _attempt in range(2):
        hit_list = "\n".join(
            f"- {item['pattern']}：{item['snippet']}" for item in remaining[:12]
        )
        messages = build_style_repair_messages(repaired, patterns, hit_list)
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.1,
            max_tokens=_repair_token_budget(repaired, max_tokens),
            retry=1,
        )
        candidate = _strip_plain_text_response(result.get("content", ""))
        if candidate:
            repaired = candidate
        remaining = _detect_forbidden_sentence_violations(repaired, project)
        if not remaining:
            break
    if remaining:
        repaired = _mechanical_repair_forbidden_sentences(repaired)
        remaining = _detect_forbidden_sentence_violations(repaired, project)
    return repaired, before, remaining


async def _repair_assistant_parsed_style(
    parsed: dict,
    project: Project,
    model: Optional[str],
    max_tokens: Optional[int] = None,
) -> list[dict]:
    """Repair visible assistant reply and generated chapter draft fields in-place."""
    reports: list[dict] = []

    async def repair_field(owner: dict, key: str, field_name: str) -> None:
        value = str(owner.get(key) or "")
        if not value.strip():
            return
        repaired, before, remaining = await _repair_forbidden_sentence_text(value, project, model, max_tokens)
        if before:
            owner[key] = repaired
            reports.append({
                "field": field_name,
                "fixed": not remaining,
                "violations": before[:8],
                "remaining": remaining[:8],
            })

    await repair_field(parsed, "reply", "reply")
    draft = parsed.get("chapter_draft")
    if isinstance(draft, dict):
        await repair_field(draft, "content", "chapter_draft.content")
        await repair_field(draft, "summary", "chapter_draft.summary")
    return reports
