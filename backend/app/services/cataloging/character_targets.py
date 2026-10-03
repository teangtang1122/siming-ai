"""Validate explicit character create/update targets without guessing identity."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from ...database.models import CatalogingCandidate, Character


class ArchiveValueMismatch(ValueError):
    """An exact-value guard with machine-readable correction context."""

    def __init__(
        self, message: str, character: Character, field: str, expected: str, provided: Any,
        *, preserve_and_append: bool = False,
    ):
        if isinstance(provided, str):
            offset = next((i for i, pair in enumerate(zip(expected, provided, strict=False))
                           if pair[0] != pair[1]), min(len(expected), len(provided)))
            message += (
                f"；当前原值 {len(expected)} 字符，提交值 {len(provided)} 字符，"
                f"首个差异在第 {offset + 1} 个字符（标点和空白也须保留）"
            )
            if preserve_and_append:
                message += (
                    f"；差异字符：原值 {expected[offset:offset + 1]!r}，"
                    f"新值 {provided[offset:offset + 1]!r}"
                )
        super().__init__(message)
        self.repair_context = {
            "target_id": character.id, "field": field, "expected_value": expected,
        }
        if preserve_and_append:
            self.repair_context["requirement"] = "preserve_and_append"


def validate_character_profile_target(
    db: Session, project_id: str, item_type: str, payload: dict[str, Any],
) -> Character | None:
    if item_type not in {"character_create", "character_update"}:
        return None
    name = payload.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise ValueError("角色 name 必须是非空字符串")
    name = name.strip() if name else None
    aliases = payload.get("aliases")
    if aliases is not None and (
        not isinstance(aliases, list)
        or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
    ):
        raise ValueError("角色 aliases 必须是独立字符串数组，不拆分组合姓名")
    target_id = payload.get("id")
    if item_type == "character_create":
        if target_id is not None:
            raise ValueError("character_create 不接受已有角色 id；已有角色必须使用 character_update")
        if not name:
            raise ValueError("character_create 必须填写角色 name")
        client_id = payload.get("client_id")
        if client_id is not None:
            try:
                if not isinstance(client_id, str) or str(UUID(client_id)) != client_id:
                    raise ValueError
            except (ValueError, AttributeError):
                raise ValueError("新角色 client_id 必须是规范 UUID，供同批大纲 character_ids 引用") from None
            if db.get(Character, client_id) is not None:
                raise ValueError("新角色 client_id 已存在；已有角色必须使用 character_update")
        existing = db.query(Character).filter(
            Character.project_id == project_id, Character.name == name,
        ).first()
        if existing:
            raise ValueError(
                f"角色 {name} 已存在，不能用 character_create 覆盖；"
                f"请读取档案并使用 character_update，id={existing.id}"
            )
        return None
    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("character_update 必须填写已读取的真实角色 id，不按姓名或别名猜测目标")
    character = db.query(Character).filter(
        Character.project_id == project_id, Character.id == target_id,
    ).first()
    if not character:
        raise ValueError("character_update 的 id 不属于当前作品中的角色，不会回退创建或按姓名查找")
    if name and name != character.name and db.query(Character).filter(
        Character.project_id == project_id, Character.name == name,
        Character.id != character.id,
    ).first():
        raise ValueError("角色更新的新 name 已被当前作品的另一角色使用")

    # Automatic cataloging treats ``background`` as a full replacement field.
    # Require the model to acknowledge and preserve the exact current value so
    # a short chapter-specific recap cannot silently erase the durable profile.
    # Revision reconciliation carries its own before/after snapshots and
    # preserves author or later-chapter edits in ``character_ops``.
    incoming_background = payload.get("background")
    is_revision_reconciliation = all(
        isinstance(payload.get(key), dict)
        for key in (
            "_cataloging_previous_payload",
            "_cataloging_previous_old_snapshot",
            "_cataloging_previous_new_snapshot",
        )
    )
    if (
        "background" in payload
        and incoming_background not in (None, "")
        and not is_revision_reconciliation
    ):
        if not isinstance(incoming_background, str):
            raise ValueError("character_update.background 必须是字符串")
        current_background = str(character.background or "")
        if current_background and incoming_background != current_background:
            acknowledged = payload.get("background_before")
            if acknowledged != current_background:
                raise ArchiveValueMismatch(
                    f"角色 {character.name} 的 background 与当前档案不同；自动建档修改时"
                    "必须用 background_before 逐字复制当前完整值",
                    character, "background_before", current_background, acknowledged,
                )
            if current_background not in incoming_background:
                raise ArchiveValueMismatch(
                    f"角色 {character.name} 的 background 是稳定档案整字段替换；新值必须"
                    "逐字保留当前完整值（含末尾标点和空白）并追加正文确认的稳定信息。"
                    "确需重写或删除时请由作者通过角色编辑接口复核后修改",
                    character, "background", current_background, incoming_background,
                    preserve_and_append=True,
                )
    return character


def validate_character_state_target(
    db: Session,
    project_id: str,
    item_type: str,
    payload: dict[str, Any],
    *,
    chapter_content: str | None = None,
    chapter_run_id: str | None = None,
) -> Character | None:
    """Protect full-replacement state fields from stale or partial model writes.

    The model still decides which assets belong to a character.  This boundary
    only requires it to acknowledge the exact current value and retain that
    value verbatim before an automatic cataloging write can append new state.
    Intentional destructive replacement remains an explicit author edit.
    """

    if item_type != "character_state_update":
        return None
    target_id = payload.get("id")
    query = db.query(Character).filter(Character.project_id == project_id)
    if target_id:
        character = query.filter(Character.id == str(target_id)).first()
    else:
        raise ValueError("character_state_update.id 必须使用当前计划中模型选择的真实 ID")
    # A newly staged character is not in the formal archive yet. Validate its
    # state against the create candidate that will be applied first, so the
    # model can repair missing evidence before the chapter transaction starts.
    if character is None and chapter_run_id:
        from .candidate_io import candidate_payload

        creates = db.query(CatalogingCandidate).filter(
            CatalogingCandidate.chapter_run_id == chapter_run_id,
            CatalogingCandidate.item_type == "character_create",
            CatalogingCandidate.status != "rejected",
        ).all()
        for candidate in creates:
            initial = candidate_payload(candidate)
            if initial.get("client_id") == str(target_id):
                character = Character(
                    id=str(target_id),
                    project_id=project_id,
                    name=str(initial.get("name") or ""),
                    appearance=initial.get("appearance"),
                    age=initial.get("age"),
                    items_or_assets=initial.get("items_or_assets"),
                )
                break
    # An unbound ID is still rejected by the candidate store and plan contract.
    if character is None:
        return character

    for field, label in (("appearance", "appearance"), ("age", "age")):
        if field not in payload or payload.get(field) in (None, ""):
            continue
        incoming = payload.get(field)
        if not isinstance(incoming, str):
            raise ValueError(f"character_state_update.{field} 必须是字符串")
        current = str(getattr(character, field) or "")
        if incoming == current:
            continue
        before_key = f"{field}_before"
        evidence_key = f"{field}_evidence"
        acknowledged = payload.get(before_key)
        if current and acknowledged != current:
            raise ArchiveValueMismatch(
                f"角色 {character.name} 的 {field} 与当前档案不同；本章确有{label}变化时，"
                f"必须用 {before_key} 逐字复制当前值，否则省略 {field} 以保留旧值",
                character, before_key, current, acknowledged,
            )
        evidence = payload.get(evidence_key)
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(
                f"角色 {character.name} 的 {field} 变化缺少 {evidence_key}；"
                "请逐字引用本章正文，未明确变化时省略该字段"
            )
        if chapter_content is None or evidence.strip() not in chapter_content:
            raise ValueError(
                f"角色 {character.name} 的 {evidence_key} 不是本章正文的逐字摘录；"
                f"无法证明{label}变化时省略 {field}"
            )

    if "items_or_assets" not in payload:
        return character

    incoming = payload.get("items_or_assets")
    if incoming in (None, ""):
        return character
    if not isinstance(incoming, str):
        raise ValueError("character_state_update.items_or_assets 必须是字符串")
    current = str(character.items_or_assets or "")
    acknowledged = payload.get("items_or_assets_before")
    if acknowledged is not None and acknowledged != current:
        raise ArchiveValueMismatch(
            f"角色 {character.name} 的 items_or_assets_before 与当前档案不一致；"
            "请重新读取完整角色卡后再提交",
            character, "items_or_assets_before", current, acknowledged,
        )
    if incoming == current or not current:
        return character
    if acknowledged is None:
        raise ArchiveValueMismatch(
            f"角色 {character.name} 已有非空 items_or_assets；自动建档修改时必须提供"
            "逐字复制的 items_or_assets_before",
            character, "items_or_assets_before", current, acknowledged,
        )
    if current not in incoming:
        raise ArchiveValueMismatch(
            f"角色 {character.name} 的 items_or_assets 是整字段替换；新值必须逐字保留"
            "当前完整值（含末尾标点和空白）并追加本章变化。确需删除时请由作者"
            "通过角色编辑接口复核后修改",
            character, "items_or_assets", current, incoming, preserve_and_append=True,
        )
    return character
