"""Persistent author review and confirmation for generated outline proposals."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.architecture.uow import commit_session
from app.modules.story.domain.outline_contract import OUTLINE_PROPOSAL_MAX_NODES


class PendingOutlineDraftConflict(RuntimeError):
    """A proposal already owns the project's outline review slot."""

    def __init__(self, draft: Any):
        super().__init__("a pending outline draft already exists")
        self.draft = draft


def _project_lock(db: Any, project_id: str) -> Any | None:
    from ...database.models import Project

    return db.query(Project).filter(Project.id == project_id).with_for_update().first()


def _outline_tree_hash(db: Any, project_id: str) -> str:
    """Fingerprint the complete formal outline before an author reviews a proposal."""
    from ...database.models import OutlineNode

    rows = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id)
        .order_by(OutlineNode.id.asc())
        .all()
    )
    payload = [
        {
            "id": row.id,
            "parent_id": row.parent_id,
            "node_type": row.node_type,
            "title": row.title,
            "summary": row.summary,
            "status": row.status,
            "source_chapter_id": row.source_chapter_id,
            "actual_summary": row.actual_summary,
            "planned_summary": row.planned_summary,
            "metadata_json": row.metadata_json,
            "cataloging_status": row.cataloging_status,
            "sort_order": row.sort_order,
            "linked_characters": sorted(
                (
                    {
                        "character_id": link.character_id,
                        "role_in_scene": link.role_in_scene,
                    }
                    for link in row.linked_characters
                ),
                key=lambda value: (
                    str(value["character_id"] or ""),
                    str(value["role_in_scene"] or ""),
                ),
            ),
        }
        for row in rows
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def find_outline_draft(db: Any, project_id: str, draft_id: str) -> Any | None:
    from ...database.models import OutlineDraft

    return (
        db.query(OutlineDraft)
        .filter(OutlineDraft.id == draft_id, OutlineDraft.project_id == project_id)
        .first()
    )


def latest_pending_outline_draft(db: Any, project_id: str) -> Any | None:
    from ...database.models import OutlineDraft

    return (
        db.query(OutlineDraft)
        .filter(OutlineDraft.project_id == project_id, OutlineDraft.status == "pending")
        .order_by(OutlineDraft.updated_at.desc(), OutlineDraft.created_at.desc())
        .first()
    )


def outline_draft_result_data(draft: Any) -> dict[str, Any]:
    nodes = [dict(node) for node in (draft.nodes_json or []) if isinstance(node, dict)]
    saved_ids = [str(value) for value in (draft.saved_outline_node_ids or []) if str(value)]
    chapter_ids = [
        str(node.get("id"))
        for node in nodes
        if node.get("id") and str(node.get("node_type") or "") == "chapter"
    ]
    if saved_ids and not chapter_ids:
        chapter_ids = saved_ids[:1]
    return {
        "draft_id": str(draft.id),
        "project_id": str(draft.project_id),
        "context_manifest_id": draft.context_manifest_id,
        "parent_id": draft.parent_id,
        "insert_after_id": draft.insert_after_id,
        "draft_status": str(draft.status or "pending"),
        "nodes": nodes,
        "design_notes": str(draft.design_notes or ""),
        "context_selection_digest": str(draft.context_selection_digest or ""),
        "base_outline_hash": str(draft.base_outline_hash or ""),
        "saved_outline_node_ids": saved_ids,
        "chapter_outline_node_ids": chapter_ids,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
        "next_actions": (
            ["edit", "confirm", "confirm_and_write", "regenerate", "discard"]
            if draft.status == "pending"
            else []
        ),
    }


def _resolve_position(
    db: Any,
    project_id: str,
    parent_id: str | None,
    insert_after_id: str | None,
) -> tuple[str | None, Any | None]:
    from ...core.exceptions import ValidationError
    from ...database.models import OutlineNode

    parent = None
    if parent_id:
        parent = (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project_id, OutlineNode.id == parent_id)
            .first()
        )
        if parent is None:
            raise ValidationError("大纲草稿的父节点已不存在，请重新规划")

    insert_after = None
    if insert_after_id:
        insert_after = (
            db.query(OutlineNode)
            .filter(
                OutlineNode.project_id == project_id,
                OutlineNode.id == insert_after_id,
            )
            .first()
        )
        if insert_after is None:
            raise ValidationError("大纲草稿的插入锚点已不存在，请重新规划")
        inferred_parent_id = str(insert_after.parent_id or "") or None
        if parent_id and inferred_parent_id != parent_id:
            raise ValidationError("大纲草稿的插入锚点已移动到其他父节点，请重新规划")
        if not parent_id:
            parent_id = inferred_parent_id
    return parent_id, insert_after


def _validated_nodes(
    db: Any,
    project_id: str,
    parent_id: str | None,
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and topologically order one editable proposal batch."""
    from ...core.exceptions import ValidationError
    from ...database.models import OutlineNode

    if not isinstance(nodes, list):
        raise ValidationError("大纲草稿 nodes 必须是原生数组，不能编码成 JSON 字符串")
    if len(nodes) > OUTLINE_PROPOSAL_MAX_NODES:
        raise ValidationError(
            f"单次大纲草稿最多包含 {OUTLINE_PROPOSAL_MAX_NODES} 个节点"
        )
    if any(not isinstance(node, dict) for node in nodes):
        raise ValidationError("大纲草稿节点格式无效")
    values = [dict(node) for node in nodes]
    if not values:
        raise ValidationError("大纲草稿至少需要一个节点")
    allowed_types = {"volume", "chapter", "section"}
    by_title: dict[str, dict[str, Any]] = {}
    for node in values:
        title = str(node.get("title") or "").strip()
        node_type = str(node.get("node_type") or "chapter").strip()
        if not title:
            raise ValidationError("大纲草稿节点标题不能为空")
        if len(title) > 200:
            raise ValidationError("大纲草稿节点标题不能超过 200 个字符")
        if title in by_title:
            raise ValidationError(f"大纲草稿节点标题重复：{title}")
        if node_type not in allowed_types:
            raise ValidationError(f"大纲草稿节点类型无效：{node_type}")
        node["title"] = title
        node["node_type"] = node_type
        node["status"] = "pending"
        node["summary"] = str(node.get("summary") or "").strip()
        node["planned_summary"] = node["summary"]
        node["actual_summary"] = ""
        node.pop("source_chapter_id", None)
        node.pop("cataloging_status", None)
        by_title[title] = node

    formal_parent = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.id == parent_id)
        .first()
        if parent_id
        else None
    )
    if parent_id and formal_parent is None:
        raise ValidationError("大纲草稿的父节点已不存在，请重新规划")
    allowed_child_types = {
        None: {"volume", "chapter"},
        "volume": {"chapter"},
        "chapter": {"section"},
        "section": set(),
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[dict[str, Any]] = []

    def visit(title: str) -> None:
        if title in visited:
            return
        if title in visiting:
            raise ValidationError("大纲草稿父子关系形成循环")
        visiting.add(title)
        node = by_title[title]
        parent_title = str(node.get("parent_title") or "").strip()
        if parent_title:
            draft_parent = by_title.get(parent_title)
            if draft_parent is None and formal_parent is not None and parent_title in {
                str(formal_parent.id),
                str(formal_parent.title),
            }:
                # Models naturally repeat the already selected formal parent
                # on every top-level proposal node. Treat that redundant value
                # as the explicit parent_id instead of rejecting a valid batch.
                parent_type = str(formal_parent.node_type)
                node.pop("parent_title", None)
                parent_title = ""
            elif draft_parent is None:
                raise ValidationError(f"大纲草稿引用了不存在的父标题：{parent_title}")
            else:
                visit(parent_title)
                parent_type = str(draft_parent.get("node_type") or "")
                node["parent_title"] = parent_title
        else:
            parent_type = str(formal_parent.node_type) if formal_parent is not None else None
            node.pop("parent_title", None)
        if node["node_type"] not in allowed_child_types.get(parent_type, set()):
            parent_label = parent_title or (
                formal_parent.title if formal_parent is not None else "根大纲"
            )
            raise ValidationError(
                f"{parent_label} 下不能创建 {node['node_type']} 类型节点"
            )
        visiting.remove(title)
        visited.add(title)
        ordered.append(node)

    for title in by_title:
        visit(title)

    top_titles = {
        str(node["title"])
        for node in ordered
        if not str(node.get("parent_title") or "").strip()
    }
    existing_titles = {
        str(value)
        for (value,) in db.query(OutlineNode.title)
        .filter(
            OutlineNode.project_id == project_id,
            OutlineNode.parent_id == parent_id,
            OutlineNode.title.in_(top_titles),
        )
        .all()
    }
    if existing_titles:
        raise ValidationError(
            "正式大纲中已存在同名节点：" + "、".join(sorted(existing_titles))
        )
    return ordered


def outline_proposal_batch_count(manifest: Any) -> int:
    """Use the model-selected structured request, never infer count from prose."""
    from ...core.exceptions import ValidationError

    if manifest is None or manifest.task_type != "outline_planning":
        raise ValidationError("大纲规划上下文不存在或类型不正确，请重新规划")
    query = manifest.query_json if isinstance(manifest.query_json, dict) else {}
    arguments = query.get("arguments") if isinstance(query.get("arguments"), dict) else {}
    count = arguments.get("batch_count", 1)
    if (
        type(count) is not int
        or not 1 <= count <= OUTLINE_PROPOSAL_MAX_NODES
    ):
        raise ValidationError(
            "大纲规划 batch_count 必须为 1 至 "
            f"{OUTLINE_PROPOSAL_MAX_NODES} 的整数，请重新规划"
        )
    return count


def validate_generated_outline_proposal(
    db: Any,
    *,
    project_id: str,
    manifest: Any,
    parent_id: str | None,
    insert_after_id: str | None,
    nodes: Any,
) -> tuple[str | None, list[dict[str, Any]]]:
    from ...core.exceptions import ValidationError

    count = outline_proposal_batch_count(manifest)
    if not isinstance(nodes, list):
        raise ValidationError("大纲草稿 nodes 必须是原生数组，不能编码成 JSON 字符串")
    if len(nodes) != count:
        raise ValidationError(f"本次规划要求 {count} 个节点，实际提交 {len(nodes)} 个；请完整提交，不能缩减批次")
    resolved_parent_id, _ = _resolve_position(db, project_id, parent_id, insert_after_id)
    return resolved_parent_id, _validated_nodes(db, project_id, resolved_parent_id, nodes)


def store_outline_draft(
    db: Any,
    *,
    project_id: str,
    context_manifest_id: str,
    parent_id: str | None,
    insert_after_id: str | None,
    nodes: list[dict[str, Any]],
    design_notes: str,
    context_selection_token: str,
) -> Any:
    """Persist a proposal without replacing an author-visible pending draft."""
    from ...database.models import ContextManifest, OutlineDraft

    commit_session(db)
    _project_lock(db, project_id)
    manifest = db.query(ContextManifest).filter_by(id=context_manifest_id, project_id=project_id).first()
    resolved_parent_id, validated_nodes = validate_generated_outline_proposal(
        db, project_id=project_id, manifest=manifest, parent_id=parent_id,
        insert_after_id=insert_after_id, nodes=nodes,
    )
    pending = latest_pending_outline_draft(db, project_id)
    if pending:
        db.rollback()
        raise PendingOutlineDraftConflict(pending)

    row = OutlineDraft(
        id=str(uuid4()),
        project_id=project_id,
        context_manifest_id=context_manifest_id,
        parent_id=resolved_parent_id,
        insert_after_id=insert_after_id,
        status="pending",
        nodes_json=validated_nodes,
        design_notes=design_notes,
        context_selection_digest=hashlib.sha256(
            context_selection_token.encode("utf-8")
        ).hexdigest(),
        base_outline_hash=_outline_tree_hash(db, project_id),
        created_at=datetime.utcnow(),
    )
    db.add(row)
    try:
        commit_session(db)
    except IntegrityError:
        db.rollback()
        concurrent = latest_pending_outline_draft(db, project_id)
        if concurrent:
            raise PendingOutlineDraftConflict(concurrent) from None
        raise
    return row


def update_outline_draft(
    db: Any,
    project_id: str,
    draft_id: str,
    *,
    nodes: list[dict[str, Any]],
    design_notes: str,
) -> Any:
    from ...core.exceptions import ValidationError

    row = find_outline_draft(db, project_id, draft_id)
    if row is None:
        raise ValidationError("大纲草稿不存在")
    if row.status != "pending":
        raise ValidationError("该大纲草稿已确认或丢弃，不能继续编辑")
    row.nodes_json = _validated_nodes(
        db,
        project_id,
        str(row.parent_id or "") or None,
        nodes,
    )
    row.design_notes = design_notes
    row.updated_at = datetime.utcnow()
    commit_session(db)
    return row


def _close_outline_draft(
    db: Any,
    project_id: str,
    draft_id: str,
    *,
    status: str,
) -> Any:
    from ...core.exceptions import ValidationError

    row = find_outline_draft(db, project_id, draft_id)
    if row is None:
        raise ValidationError("大纲草稿不存在")
    if row.status == "pending":
        row.status = status
        row.updated_at = datetime.utcnow()
        commit_session(db)
    return row


def discard_outline_draft(db: Any, project_id: str, draft_id: str) -> Any:
    return _close_outline_draft(
        db,
        project_id,
        draft_id,
        status="discarded",
    )


def supersede_outline_draft(db: Any, project_id: str, draft_id: str) -> Any:
    return _close_outline_draft(
        db,
        project_id,
        draft_id,
        status="superseded",
    )


async def confirm_outline_draft(db: Any, project_id: str, draft_id: str) -> dict[str, Any]:
    """Atomically promote a reviewed proposal to formal outline nodes."""
    from ...core.exceptions import ValidationError
    from ...database.models import OutlineNode
    from .tools.outline import create_outline_nodes
    from .utils import find_character_by_name_or_id

    _project_lock(db, project_id)
    row = find_outline_draft(db, project_id, draft_id)
    if row is None:
        raise ValidationError("大纲草稿不存在")
    if row.status == "confirmed":
        return outline_draft_result_data(row)
    if row.status != "pending":
        raise ValidationError("该大纲草稿不能确认")
    if _outline_tree_hash(db, project_id) != str(row.base_outline_hash or ""):
        raise ValidationError("正式大纲在提案生成后已变化，请重新生成后再确认")

    parent_id, insert_after = _resolve_position(
        db,
        project_id,
        str(row.parent_id or "") or None,
        str(row.insert_after_id or "") or None,
    )
    nodes = _validated_nodes(
        db,
        project_id,
        parent_id,
        [dict(node) for node in (row.nodes_json or []) if isinstance(node, dict)],
    )

    top_level = [node for node in nodes if not str(node.get("parent_title") or "").strip()]
    if insert_after is not None:
        first_sort = int(insert_after.sort_order or 0) + 1
    else:
        siblings = (
            db.query(OutlineNode)
            .filter(
                OutlineNode.project_id == project_id,
                OutlineNode.parent_id == parent_id,
            )
            .order_by(OutlineNode.sort_order.asc())
            .all()
        )
        first_sort = max((int(node.sort_order or 0) for node in siblings), default=-1) + 1
    if insert_after is not None and top_level:
        siblings_to_shift = (
            db.query(OutlineNode)
            .filter(
                OutlineNode.project_id == project_id,
                OutlineNode.parent_id == parent_id,
                OutlineNode.sort_order >= first_sort,
            )
            .all()
        )
        for sibling in siblings_to_shift:
            sibling.sort_order = int(sibling.sort_order or 0) + len(top_level)

    next_sort = first_sort
    prepared: list[dict[str, Any]] = []
    for node in nodes:
        item = dict(node)
        planned_names = item.get("character_names", [])
        if not isinstance(planned_names, list):
            raise ValidationError("大纲草稿的关联角色必须是名称或 ID 列表")
        linked_names: list[str] = []
        all_planned_names: list[str] = []
        unlinked_planned_names: list[str] = []
        for value in planned_names:
            submitted_name = str(value or "").strip()
            if not submitted_name:
                raise ValidationError("大纲草稿的关联角色名称不能为空")
            character = find_character_by_name_or_id(db, project_id, submitted_name)
            canonical_name = str(character.name) if character is not None else submitted_name
            if canonical_name not in all_planned_names:
                all_planned_names.append(canonical_name)
            if character is not None:
                if canonical_name not in linked_names:
                    linked_names.append(canonical_name)
            elif submitted_name not in unlinked_planned_names:
                unlinked_planned_names.append(submitted_name)
        metadata = dict(item.get("metadata")) if isinstance(item.get("metadata"), dict) else {}
        metadata["planned_character_names"] = all_planned_names
        metadata["unlinked_planned_character_names"] = unlinked_planned_names
        item["metadata"] = metadata
        # A reviewed future outline may name people who have not entered the
        # story yet. Link only existing records; keep the remaining names in
        # metadata so confirmation never creates speculative character files.
        item["character_names"] = linked_names
        if not str(item.get("parent_title") or "").strip():
            item["parent_id"] = parent_id
            item["sort_order"] = next_sort
            next_sort += 1
        prepared.append(item)

    result = await create_outline_nodes(db, project_id, {"nodes": prepared})
    result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
    skipped = result_data.get("skipped") if isinstance(result_data, dict) else []
    if result.get("status") != "ok" or skipped:
        db.rollback()
        raise ValidationError(str(result.get("detail") or "大纲保存失败"))
    created = [
        dict(item)
        for item in result_data.get("nodes", [])
        if isinstance(item, dict)
    ]
    saved_ids = [str(item.get("id")) for item in created if item.get("id")]
    if len(created) != len(prepared) or len(saved_ids) != len(prepared):
        db.rollback()
        raise ValidationError("大纲保存结果不完整，已回滚全部节点")

    row.status = "confirmed"
    row.nodes_json = created
    row.saved_outline_node_ids = saved_ids
    row.confirmed_at = datetime.utcnow()
    row.updated_at = datetime.utcnow()
    commit_session(db)
    return outline_draft_result_data(row)


def pending_outline_draft_block_result(tool: str, draft: Any) -> dict[str, Any]:
    from .turn_control import AssistantTurnDirective, apply_turn_directive

    return apply_turn_directive(
        {
            "tool": tool,
            "status": "blocked",
            "detail": "已有一份大纲草稿等待作者处理，本轮未生成新的规划。",
            "data": outline_draft_result_data(draft),
        },
        AssistantTurnDirective.BLOCKED_ON_OUTLINE_DRAFT,
    )


__all__ = [
    "PendingOutlineDraftConflict",
    "confirm_outline_draft",
    "discard_outline_draft",
    "find_outline_draft",
    "latest_pending_outline_draft",
    "outline_draft_result_data",
    "pending_outline_draft_block_result",
    "store_outline_draft",
    "supersede_outline_draft",
    "update_outline_draft",
]
