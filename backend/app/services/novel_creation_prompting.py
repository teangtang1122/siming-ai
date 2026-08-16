"""Canonical model messages shared by desktop novel creation and mobile export."""

from __future__ import annotations

import json
from typing import Any

COMPACT_CONCEPT_SHAPE: dict[str, Any] = {
    "concepts": [{
        "title": "不超过20字的标题",
        "subtitle": "一句定位",
        "logline": "不超过120字的一句话梗概",
        "protagonist_seed": {
            "name": "主角名",
            "identity": "身份",
            "goal": "即时目标",
            "lack": "内在缺口",
        },
        "world_hook": "不超过100字的世界钩子",
        "core_conflict": "不超过100字的核心冲突",
        "story_engine": "持续推进故事的机制",
        "opening_hook": "不超过100字的开篇钩子",
        "differentiators": ["差异点一", "差异点二"],
        "risks": ["一个创作风险"],
    }],
}


CREATION_STAGE_TASK_RULES = (
    "只深化当前阶段的 baseline，顶层只返回 data 字段；"
    "保留作者原文、锁定要求、已确认事实和专名，不提前生成下游阶段。"
    "调整要求只作用于当前阶段；没有明确授权时不得改动其他内容。"
    "如果 entity_target 存在，只生成或修改其中指定类型的对象，其他对象必须保持原样；"
    "新增数量必须根据作者本次调整要求判断，作者未给固定数字时按语义生成最合适的少量对象。"
)

CONCEPT_TASK_KINDS = {
    "author_led": "整理作者方案",
    "explore": "生成一套可持续调整的创意方向",
}
CONCEPT_TASK_RULES = {
    "author_led": (
        "只生成恰好一张作者方案卡，不生成替代故事。作者原文、专名、因果、结局方向和锁定要求都是不可改写的事实；只补全空白。"
        "如果提供了调整要求，只调整当前创意阶段，不影响其他阶段。"
    ),
    "explore": (
        "只生成恰好一张轻量创意卡，不生成完整世界观、配角表、卷纲或章节细纲。方案必须遵守作者约束，并适合作者随后通过对话持续局部调整。"
        "如果提供了调整要求，只调整当前创意阶段，不影响其他阶段。"
    ),
}
CONCEPT_USER_INTROS = {
    "author_led": (
        "请严格返回恰好1张作者方案卡，字段必须与下列 JSON 结构一致。"
        "方案必须忠实整理作者已经想好的内容，不得随机替换故事。\n"
    ),
    "explore": (
        "请严格返回恰好1张创意卡，字段必须与下列 JSON 结构一致。"
        "创意卡应在数百字内可读完，并保留通过后续对话调整的空间。\n"
    ),
}

CREATION_STAGE_USER_PREFIX = (
    "当前阶段：{stage_label}\n"
    "结构契约：{stage_contract}\n"
    "请在保留作者约束和已确认事实的前提下，深化 baseline；不要改变已经确认的专名。\n"
)

CREATION_REPAIR_SYSTEM_PROMPT = (
    "你是司命的阶段结构修复器。只修复 JSON 语法和结构契约，不改写作者事实、专名、"
    "已确认内容或创作方向。只输出一个 JSON 对象，不要解释。"
)
CREATION_REPAIR_USER_TEMPLATE = (
    "结构契约：{contract}\n"
    "校验错误：{error}\n"
    "请把下面的模型原始输出修复为合法结构；无法确定的内容保持原样，不要另写故事。\n"
    "原始输出：{raw}"
)


def build_compact_concept_messages(
    *,
    author_led: bool,
    context: dict[str, Any],
) -> list[dict[str, str]]:
    """Build the canonical single-card concept-generation messages."""
    from app.modules.creation.interfaces.dependencies import render_creation_prompt

    system = render_creation_prompt(
        task_kind=CONCEPT_TASK_KINDS["author_led" if author_led else "explore"],
        task_rules=CONCEPT_TASK_RULES["author_led" if author_led else "explore"],
    )
    mode = "author_led" if author_led else "explore"
    user = (
        CONCEPT_USER_INTROS[mode]
        + f"输出结构：{json.dumps(COMPACT_CONCEPT_SHAPE, ensure_ascii=False)}\n"
        f"作者上下文：{json.dumps(context, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_creation_stage_messages(
    *,
    stage: str,
    stage_label: str,
    stage_contract: str,
    context: dict[str, Any],
    instruction: str = "",
) -> list[dict[str, str]]:
    """Build the canonical message pair for one structured creation artifact."""
    from app.modules.creation.interfaces.dependencies import render_creation_prompt

    system = render_creation_prompt(
        task_kind=f"深化阶段：{stage_label}",
        task_rules=CREATION_STAGE_TASK_RULES,
    )
    user = (
        CREATION_STAGE_USER_PREFIX.format(
            stage_label=stage_label,
            stage_contract=stage_contract,
        )
        + (f"作者本次调整要求：{instruction}\n" if instruction else "")
        + f"上下文：{json.dumps(context, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


__all__ = [
    "COMPACT_CONCEPT_SHAPE",
    "CONCEPT_TASK_KINDS",
    "CONCEPT_TASK_RULES",
    "CONCEPT_USER_INTROS",
    "CREATION_STAGE_TASK_RULES",
    "CREATION_STAGE_USER_PREFIX",
    "CREATION_REPAIR_SYSTEM_PROMPT",
    "CREATION_REPAIR_USER_TEMPLATE",
    "build_compact_concept_messages",
    "build_creation_stage_messages",
]
