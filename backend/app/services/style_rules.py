"""Project writing style rules and forbidden sentence repair."""
from __future__ import annotations

import re
from typing import Optional

from ..ai.gateway import LLMGateway
from ..database.models import Project


STYLE_OPTIONS = ["vivid", "concise", "serious", "humorous", "poetic"]
STYLE_PROMPTS = {
    "vivid": "请用生动形象、富有画面感的语言改写。要求：优先使用具体动作、感官细节和场景调度制造画面感；不要依赖密集比喻或华丽排比；将抽象概括转化为具体场景。",
    "concise": "请用简洁精炼的语言改写，去除冗余。要求：删除重复表述和空洞修饰词；合并可归并的句子；用精准动词和名词替代冗长形容结构；提高信息密度。",
    "serious": "请用严肃庄重的语言改写。要求：句式规整，避免口语化和俏皮话；用词精准克制，不夸张不煽情；保持客观冷静的叙事距离。",
    "humorous": "请用幽默诙谐的语言改写。要求：可运用反讽、夸张、反差、双关等手法；节奏轻快；幽默应为角色和剧情服务，而非单纯搞笑。",
    "poetic": "请用富有诗意的语言改写。要求：注重语句的韵律感和节奏美；善用意象和留白；情感含蓄有层次，避免直白抒情。",
}

DEFAULT_FORBIDDEN_SENTENCE_PATTERNS = "\n".join([
    "不是……是……",
    "不是……而是……",
    "不是……却是……",
    "与其说……不如说……",
])

DEFAULT_RHETORIC_GUIDELINES = (
    "克制使用比喻、拟人、排比等修辞，禁止连续堆叠比喻。"
    "优先用具体动作、感官细节、因果推进和角色反应来表达画面与情绪。"
    "非必要不使用抽象概念比喻；同一段落不要出现多个比喻。"
)

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
}



def _build_style_context(project: Project) -> str:
    perspective_map = {
        "first_person": "第一人称",
        "third_person": "第三人称",
        "omniscient": "上帝视角",
    }
    style_map = {
        "natural": "自然",
        "vivid": "华丽生动",
        "concise": "白描简洁",
        "serious": "严肃",
        "humorous": "幽默",
        "poetic": "诗意",
    }
    perspective = perspective_map.get(project.narrative_perspective, "第三人称")
    style = style_map.get(project.writing_style, "自然")
    forbidden_patterns = (project.forbidden_sentence_patterns or DEFAULT_FORBIDDEN_SENTENCE_PATTERNS).strip()
    rhetoric_guidelines = (project.rhetoric_guidelines or DEFAULT_RHETORIC_GUIDELINES).strip()
    parts = [f"叙事视角：{perspective}", f"文风偏好：{style}"]
    if forbidden_patterns:
        patterns = [line.strip() for line in forbidden_patterns.splitlines() if line.strip()]
        if patterns:
            parts.append("禁用句式：\n" + "\n".join(f"- {pattern}" for pattern in patterns))
            parts.append("生成或改写时必须主动避开上述句式，包括同义变体和近似模板。")
            parts.append(
                "硬性句式检查：交付前必须自查并改掉所有禁用句式。"
                "跨句变体也禁止，例如“不是A。是B。”、“不是A，而是B。”、“与其说A，不如说B。”。"
            )
    if rhetoric_guidelines:
        parts.append(f"修辞限制：{rhetoric_guidelines}")
    return "\n".join(parts)


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
    """Last-resort cleanup for the built-in contrast templates."""

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

    rules = [
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
        messages = [
            {
                "role": "system",
                "content": (
                    "你是小说正文句式审校器。你的任务只做一件事："
                    "在不改变剧情事实、角色行动、信息顺序、叙事视角和语气的前提下，"
                    "删除或改写命中的禁用句式。"
                    "不要解释，不要加标题，不要输出清单，只输出修订后的完整正文。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "禁用句式如下，包含跨句变体也禁止：\n"
                    + "\n".join(f"- {pattern}" for pattern in patterns)
                    + "\n\n已经命中的片段：\n"
                    + hit_list
                    + "\n\n请修订下面全文。要求：保留原有剧情、人物、设定和段落顺序；"
                    "只把命中的句式改成普通因果、递进或判断句；避免大量比喻。\n\n"
                    f"{repaired}"
                ),
            },
        ]
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

