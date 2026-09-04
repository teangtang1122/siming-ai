"""Validate explicit character create/update targets without guessing identity."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ...database.models import Character


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
                raise ValueError(
                    f"角色 {character.name} 的 background 与当前档案不同；自动建档修改时"
                    "必须用 background_before 逐字复制当前完整值"
                )
            if current_background not in incoming_background:
                raise ValueError(
                    f"角色 {character.name} 的 background 是稳定档案整字段替换；新值必须"
                    "逐字保留当前完整值并追加正文确认的稳定信息，禁止用本章概述截短旧背景。"
                    "确需重写或删除时请由作者通过角色编辑接口复核后修改"
                )
    return character


def validate_character_state_target(
    db: Session,
    project_id: str,
    item_type: str,
    payload: dict[str, Any],
    *,
    chapter_content: str | None = None,
) -> Character | None:
    """Protect full-replacement state fields from stale or partial model writes.

    The model still decides which assets belong to a character.  This boundary
    only requires it to acknowledge the exact current value and retain that
    value verbatim before an automatic cataloging write can append new state.
    Intentional destructive replacement remains an explicit author edit.
    """

    if item_type != "character_state_update":
        return None
    target_id = payload.get("id") or payload.get("character_id")
    name = payload.get("name") or payload.get("character_name")
    query = db.query(Character).filter(Character.project_id == project_id)
    if target_id:
        character = query.filter(Character.id == str(target_id)).first()
    elif isinstance(name, str) and name.strip():
        character = query.filter(Character.name == name.strip()).first()
    else:
        character = None
    # A new character may be staged in the same transaction before its state.
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
            raise ValueError(
                f"角色 {character.name} 的 {field} 与当前档案不同；本章确有{label}变化时，"
                f"必须用 {before_key} 逐字复制当前值，否则省略 {field} 以保留旧值"
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
        raise ValueError(
            f"角色 {character.name} 的 items_or_assets_before 与当前档案不一致；"
            "请重新读取完整角色卡后再提交"
        )
    if incoming == current or not current:
        return character
    if acknowledged is None:
        raise ValueError(
            f"角色 {character.name} 已有非空 items_or_assets；自动建档修改时必须提供"
            "逐字复制的 items_or_assets_before"
        )
    if current not in incoming:
        raise ValueError(
            f"角色 {character.name} 的 items_or_assets 是整字段替换；新值必须逐字保留"
            "当前完整值并追加本章变化，禁止用本章短列表静默覆盖。确需删除时请由作者"
            "通过角色编辑接口复核后修改"
        )
    return character
