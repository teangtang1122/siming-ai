"""Outline tree CRUD, reorder, and AI suggestion endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.db_helpers import get_project_or_404
from ..core.exceptions import NotFoundError, ValidationError
from ..core.response import ApiResponse
from ..database.models import OutlineNode
from ..database.session import get_db
from ..schemas.outline import (
    OutlineNodeCreate,
    OutlineNodeUpdate,
    OutlineReorderItem,
    OutlineReorderRequest,
)
from ..services.outline_service import (
    ensure_no_cycle,
    extract_character_links,
    node_to_dict,
    outline_payload,
    replace_character_links,
)

router = APIRouter(tags=["outline"])


def _get_node_or_404(db: Session, project_id: str, node_id: str) -> OutlineNode:
    node = (
        db.query(OutlineNode)
        .filter(OutlineNode.id == node_id, OutlineNode.project_id == project_id)
        .first()
    )
    if not node:
        raise NotFoundError("大纲节点不存在")
    return node


def _get_parent_or_404(db: Session, project_id: str, parent_id: str | None) -> OutlineNode | None:
    if not parent_id:
        return None
    return _get_node_or_404(db, project_id, parent_id)


def _normalize_reorder_items(payload: OutlineReorderRequest) -> list[OutlineReorderItem]:
    if payload.sort_order is not None:
        return [
            OutlineReorderItem(id=node_id, parent_id=payload.parent_id, sort_order=index)
            for index, node_id in enumerate(payload.sort_order)
        ]
    return payload.items


@router.get("/projects/{project_id}/outline")
def get_outline(project_id: str, db: Session = Depends(get_db)):
    """Get the full outline tree for a project."""
    get_project_or_404(db, project_id)
    return ApiResponse.success(data=outline_payload(db, project_id))


@router.post("/projects/{project_id}/outline")
def create_outline_node(
    project_id: str,
    payload: OutlineNodeCreate,
    db: Session = Depends(get_db),
):
    """Create an outline node."""
    get_project_or_404(db, project_id)
    _get_parent_or_404(db, project_id, payload.parent_id)

    node = OutlineNode(
        project_id=project_id,
        parent_id=payload.parent_id,
        node_type=payload.node_type,
        title=payload.title,
        summary=payload.summary,
        status=payload.status,
        sort_order=payload.sort_order,
    )
    db.add(node)
    db.flush()
    links = extract_character_links(payload.character_ids, payload.characters)
    replace_character_links(db, project_id, node, links or [])
    db.commit()
    db.refresh(node)
    return ApiResponse.success(data=node_to_dict(node), message="大纲节点已创建")


@router.put("/projects/{project_id}/outline/reorder")
def reorder_outline(
    project_id: str,
    payload: OutlineReorderRequest,
    db: Session = Depends(get_db),
):
    """Reorder outline nodes and optionally move them to a new parent."""
    get_project_or_404(db, project_id)
    items = _normalize_reorder_items(payload)
    if not items:
        raise ValidationError("未提供排序数据")

    touched_ids = [item.id for item in items]
    nodes = (
        db.query(OutlineNode)
        .filter(OutlineNode.project_id == project_id, OutlineNode.id.in_(touched_ids))
        .all()
    )
    node_by_id = {node.id: node for node in nodes}
    if len(node_by_id) != len(set(touched_ids)):
        raise ValidationError("排序节点必须属于当前作品")

    for item in items:
        node = node_by_id[item.id]
        _get_parent_or_404(db, project_id, item.parent_id)
        ensure_no_cycle(db, project_id, node.id, item.parent_id)
        node.parent_id = item.parent_id
        node.sort_order = item.sort_order

    db.commit()
    return ApiResponse.success(data=outline_payload(db, project_id), message="大纲排序已更新")


@router.put("/projects/{project_id}/outline/{node_id}")
def update_outline_node(
    project_id: str,
    node_id: str,
    payload: OutlineNodeUpdate,
    db: Session = Depends(get_db),
):
    """Update an outline node and its linked characters."""
    get_project_or_404(db, project_id)
    node = _get_node_or_404(db, project_id, node_id)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        raise ValidationError("未提供任何更新字段")

    links = extract_character_links(update_data.pop("character_ids", None), update_data.pop("characters", None))
    if "parent_id" in update_data:
        _get_parent_or_404(db, project_id, update_data["parent_id"])
        ensure_no_cycle(db, project_id, node.id, update_data["parent_id"])

    for field, value in update_data.items():
        setattr(node, field, value)
    replace_character_links(db, project_id, node, links)

    db.commit()
    db.refresh(node)
    return ApiResponse.success(data=node_to_dict(node), message="大纲节点已更新")


@router.delete("/projects/{project_id}/outline/{node_id}")
def delete_outline_node(project_id: str, node_id: str, db: Session = Depends(get_db)):
    """Delete an outline node and its descendants."""
    get_project_or_404(db, project_id)
    node = _get_node_or_404(db, project_id, node_id)
    db.delete(node)
    db.commit()
    return ApiResponse.success(message="大纲节点已删除")
