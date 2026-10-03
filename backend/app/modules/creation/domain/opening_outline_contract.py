"""Canonical opening-outline bodies and explicit, session-owned parent references."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SCENE_METADATA_FIELDS = (
    "scene_number",
    "purpose",
    "location",
    "timeline",
    "pov_character",
    "characters",
    "entry_state",
    "exit_state",
    "emotional_residue",
    "unresolved_actions",
)
CHAPTER_METADATA_FIELDS = (
    "chapter_number",
    "goal",
    "purpose",
    "scenes",
    "characters",
    "key_events",
    "chapter_hook",
    "word_count_suggestion",
)
OPENING_OUTLINE_INSTRUCTION = (
    "返回顶层 chapters、sections 数组。完整阶段恰好包含第1至{count}章；"
    "每章包含 client_id、chapter_number（正整数）、title、summary、volume_id。"
    "volume_id 从上下文 volume_index 中选择真实卷 ID，chapter_number 必须位于该卷章节范围内。"
    "每章和每个场景在顶层提供 character_ids 数组，从 character_index 逐字选择本会话角色 ID；"
    "无已建档人物时明确写 []。characters 是展示用人物描述，不能代替 character_ids 绑定。"
    "每章有2至6个场景，场景只放在顶层 sections，用 parent_client_id 引用章节 client_id；"
    "每个场景包含 client_id、parent_client_id、title、summary 及 "
    "metadata.scene_number/purpose/location/timeline/pov_character/characters/entry_state/"
    "exit_state/emotional_residue/unresolved_actions；scene_number 在每章内从1连续编号。"
    "所有 client_id 唯一，修订时保留原 ID。章节和场景的 summary 必须为非空可读细纲正文，"
    "交代事件推进、选择、代价与结果，不能只把内容放进 goal/key_events/chapter_hook 等补充字段。"
    "不返回 parent_index、parent_title 或 chapter 内的 sections/scene_outline。"
    "单实体修订只返回目标集合的对象；其引用仍须指向真实上游 ID。"
)


def _reject(reason: str, path: str) -> None:
    from .generation_errors import CreationGenerationError

    raise CreationGenerationError(reason, path)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_opening_outline(
    data: dict[str, Any],
    *,
    volume_index: list[dict[str, Any]] | None = None,
    character_index: list[dict[str, Any]] | None = None,
    partial: bool = False,
) -> None:
    """Validate shape separately from owned IDs; writes always supply the current index."""
    structure = "creation_opening_structure_invalid"
    parent_error = "creation_opening_parent_invalid"
    count_error = "creation_opening_count_invalid"
    rows: dict[str, list[dict[str, Any]]] = {}
    ids: set[str] = set()
    known_characters = (
        {row["id"] for row in character_index} if character_index is not None else None
    )
    for field in ("chapters", "sections"):
        value = data.get(field, [] if partial else None)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            _reject(structure, f"$.data.{field}")
        rows[field] = value
        for index, item in enumerate(value):
            path = f"$.data.{field}[{index}]"
            for name in ("client_id", "title"):
                if not _text(item.get(name)):
                    _reject(structure, f"{path}.{name}")
            if item["client_id"] in ids:
                _reject(structure, f"{path}.client_id")
            ids.add(item["client_id"])
            if not _text(item.get("summary")):
                _reject("creation_opening_summary_missing", f"{path}.summary")
            if "planned_summary" in item and not isinstance(item["planned_summary"], str):
                _reject(structure, f"{path}.planned_summary")
            for obsolete in ("parent_index", "parent_title", "sections", "scene_outline"):
                if obsolete in item:
                    _reject(structure, f"{path}.{obsolete}")
            expected_type = "chapter" if field == "chapters" else "section"
            if item.get("node_type", expected_type) != expected_type:
                _reject(structure, f"{path}.node_type")
            references = item.get("character_ids")
            if (
                not isinstance(references, list)
                or any(not _text(value) for value in references)
                or len(references) != len(set(references))
                or (
                    known_characters is not None
                    and not set(references).issubset(known_characters)
                )
            ):
                _reject("creation_opening_characters_invalid", f"{path}.character_ids")
    volumes = {row["id"]: row for row in volume_index} if volume_index is not None else None
    for index, chapter in enumerate(rows["chapters"]):
        path = f"$.data.chapters[{index}]"
        number = chapter.get("chapter_number")
        if type(number) is not int or number <= 0:
            _reject(structure, f"{path}.chapter_number")
        if not _text(chapter.get("volume_id")):
            _reject(parent_error, f"{path}.volume_id")
        if volumes is not None:
            volume = volumes.get(chapter["volume_id"])
            if not volume or not (
                type(volume.get("start_chapter")) is int
                and type(volume.get("end_chapter")) is int
                and volume["start_chapter"] <= number <= volume["end_chapter"]
            ):
                _reject(parent_error, f"{path}.volume_id")
    chapter_ids = {row["client_id"] for row in rows["chapters"]}
    scenes: dict[str, list[int]] = {key: [] for key in chapter_ids}
    for index, section in enumerate(rows["sections"]):
        path = f"$.data.sections[{index}]"
        parent = section.get("parent_client_id")
        if not _text(parent) or (not partial and parent not in chapter_ids):
            _reject(parent_error, f"{path}.parent_client_id")
        metadata = section.get("metadata")
        if not isinstance(metadata, dict) or not set(SCENE_METADATA_FIELDS).issubset(metadata):
            _reject(structure, f"{path}.metadata")
        number = metadata["scene_number"]
        if type(number) is not int or number not in range(1, 7):
            _reject(count_error, f"{path}.metadata.scene_number")
        scenes.setdefault(parent, []).append(number)
    if not partial:
        count = 15 if data.get("opening_chapter_count") == 15 else 3
        if sorted(row["chapter_number"] for row in rows["chapters"]) != list(range(1, count + 1)):
            _reject(count_error, "$.data.chapters")
        for numbers in scenes.values():
            if len(numbers) not in range(2, 7) or sorted(numbers) != list(
                range(1, len(numbers) + 1)
            ):
                _reject(count_error, "$.data.sections")


def normalize_opening_outline(data: dict[str, Any]) -> dict[str, Any]:
    """Add storage fields only; missing content or references must be fixed by the model."""
    validate_opening_outline(data, partial=True)
    result = deepcopy(data)
    for field, kind in (("chapters", "chapter"), ("sections", "section")):
        for row in result.get(field, []):
            row["node_type"] = kind
            if not _text(row.get("planned_summary")):
                row["planned_summary"] = row["summary"]
            row["sort_order"] = (
                row["chapter_number"] if kind == "chapter" else row["metadata"]["scene_number"]
            )
    return result


def outline_metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = deepcopy(row.get("metadata") or {})
    for field in (*CHAPTER_METADATA_FIELDS, *SCENE_METADATA_FIELDS):
        if field in row and field not in metadata:
            metadata[field] = deepcopy(row[field])
    return metadata
