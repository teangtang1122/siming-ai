"""Shared creation-output diagnostics without validator import cycles."""

from __future__ import annotations

from typing import Any

OPENING_OUTLINE_DETAILS = {
    "creation_opening_characters_invalid": (
        "章节和场景必须提供不重复的 character_ids 字符串数组，且所有 ID 必须属于当前会话的 "
        "character_index；无已建档人物时显式填写 []。请由模型读取目录并选择真实 ID，本次未写入。"
    ),
    "creation_opening_locked_changed": (
        "生成结果修改了已锁定的细纲字段；请保留该 JSON Pointer 对应的原值后修正输出。本次未写入。"
    ),
    "creation_opening_structure_invalid": (
        "细纲必须使用 chapters、sections 对象数组及唯一 client_id；章节须含正整数 "
        "chapter_number、title、volume_id，场景须含 title、parent_client_id 和完整 metadata。"
        "场景只能放在顶层 sections；不接受 parent_index、parent_title 或嵌套场景。"
    ),
    "creation_opening_summary_missing": (
        "章节和场景都必须有非空字符串 summary，写明可读的细纲内容；"
        "goal、key_events、chapter_hook 等补充字段不能代替 summary。本次未写入。"
    ),
    "creation_opening_parent_invalid": (
        "章节 volume_id 必须是当前会话 volume_index 中的真实卷 ID，且章节序号须在该卷范围内；"
        "场景 parent_client_id 必须引用本阶段真实章节 client_id。请读取索引后由模型选择 ID，"
        "不得用数组位置、卷名或其他会话的 ID 代替。本次未写入。"
    ),
    "creation_opening_count_invalid": (
        "开篇细纲必须包含约定的连续章节（默认第 1 至 3 章），每章有 2 至 6 个场景，"
        "场景 metadata.scene_number 须在所属章节内连续且唯一。本次未写入。"
    ),
}
CREATION_GENERATION_DETAILS = {
    **OPENING_OUTLINE_DETAILS,
    "creation_artifact_locked_changed": (
        "生成结果修改了已锁定的资料字段；请保留该 JSON Pointer 对应的原值后修正输出。本次未写入。"
    ),
    "creation_generated_collection_invalid": (
        "模型没有在目标阶段的原生集合中返回非空对象数组。"
        "请按目标实体输出契约将对象放入 data 内 field 指定的数组；本次生成未写入。"
    ),
    "creation_generated_dimension_invalid": (
        "生成条目的 dimension 不符合目标类型：地点 location 必须为 geography，"
        "势力 faction 必须为 factions；仅写 type 或 entity_type 不能代替 dimension。"
        "请按此结构修正生成要求后再调用；本次生成未写入。"
    ),
    "creation_generated_count_invalid": (
        "指定既有实体的修订必须恰好返回一个目标对象；本次生成未写入。"
    ),
    "creation_generated_stage_fields_missing": (
        "全书主线与卷纲必须同时包含非空字符串 story_overview、core_conflict、"
        "ending_direction，以及 volumes 分卷规划。首次生成卷实体时也必须返回这些顶层字段。"
        "请按此契约修正模型输出；本阶段生成结果未写入。"
    ),
}


class CreationGenerationError(ValueError):
    """A schema failure with repository-owned text, never provider exception text."""

    failure_class = "invalid_model_output"

    def __init__(self, reason: str, path: str, *, attempt: int = 1):
        super().__init__(f"{path}：{CREATION_GENERATION_DETAILS[reason]}")
        self.reason = reason
        self.path = path
        self.attempt = attempt

    def tool_result(self, tool: str) -> dict[str, Any]:
        return {
            "tool": tool,
            "status": "error",
            "detail": CREATION_GENERATION_DETAILS[self.reason],
            "data": {
                "reason": self.reason,
                "path": self.path,
                "retryable": True,
            },
        }
