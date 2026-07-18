"""SQLAlchemy outline application adapter."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....core.db_helpers import get_project_or_404
from ....core.exceptions import NotFoundError, ValidationError
from ....services.outline_service import (
    ensure_no_cycle,
    extract_character_links,
    node_to_dict,
    outline_payload,
    replace_character_links,
)
from ..application.results import StoryMutation
from ..domain.content_sync import ContentSyncIntent, ContentSyncTarget
from .entities import OutlineNode


class SqlAlchemyOutlineWorkspace:
    """Own outline persistence details while exposing JSON-ready results."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _node(self, project_id: str, node_id: str) -> OutlineNode:
        node = (
            self._session.query(OutlineNode)
            .filter(OutlineNode.id == node_id, OutlineNode.project_id == project_id)
            .first()
        )
        if not node:
            raise NotFoundError("大纲节点不存在")
        return node

    def _parent(self, project_id: str, parent_id: str | None) -> OutlineNode | None:
        return self._node(project_id, parent_id) if parent_id else None

    def read(self, project_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        return outline_payload(self._session, project_id)

    def create(self, project_id: str, payload: dict[str, Any]) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        self._parent(project_id, payload.get("parent_id"))
        node = OutlineNode(
            project_id=project_id,
            parent_id=payload.get("parent_id"),
            node_type=payload["node_type"],
            title=payload["title"],
            summary=payload.get("summary"),
            status=payload.get("status"),
            sort_order=payload.get("sort_order", 0),
            metadata_json=payload.get("metadata"),
        )
        self._session.add(node)
        self._session.flush()
        links = extract_character_links(
            payload.get("character_ids"), payload.get("characters")
        )
        replace_character_links(self._session, project_id, node, links or [])
        return StoryMutation(
            data=node_to_dict(node),
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.OUTLINE,
                    entity_id=node.id,
                )
            ],
        )

    def reorder(self, project_id: str, items: list[dict[str, Any]]) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        if not items:
            raise ValidationError("未提供排序数据")
        touched_ids = [str(item["id"]) for item in items]
        nodes = (
            self._session.query(OutlineNode)
            .filter(
                OutlineNode.project_id == project_id,
                OutlineNode.id.in_(touched_ids),
            )
            .all()
        )
        node_by_id = {node.id: node for node in nodes}
        if len(node_by_id) != len(set(touched_ids)):
            raise ValidationError("排序节点必须属于当前作品")
        for item in items:
            node = node_by_id[str(item["id"])]
            parent_id = item.get("parent_id")
            self._parent(project_id, parent_id)
            ensure_no_cycle(self._session, project_id, node.id, parent_id)
            node.parent_id = parent_id
            node.sort_order = int(item.get("sort_order", 0))
        self._session.flush()
        return StoryMutation(
            data=outline_payload(self._session, project_id),
            sync_intents=[
                ContentSyncIntent(project_id=project_id, target=ContentSyncTarget.OUTLINE)
            ],
        )

    def update(
        self, project_id: str, node_id: str, payload: dict[str, Any]
    ) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        node = self._node(project_id, node_id)
        if not payload:
            raise ValidationError("未提供任何更新字段")
        links = extract_character_links(
            payload.pop("character_ids", None), payload.pop("characters", None)
        )
        if "metadata" in payload:
            payload["metadata_json"] = payload.pop("metadata")
        if "parent_id" in payload:
            self._parent(project_id, payload["parent_id"])
            ensure_no_cycle(self._session, project_id, node.id, payload["parent_id"])
        for field, value in payload.items():
            setattr(node, field, value)
        replace_character_links(self._session, project_id, node, links)
        self._session.flush()
        return StoryMutation(
            data=node_to_dict(node),
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.OUTLINE,
                    entity_id=node.id,
                )
            ],
        )

    def delete(self, project_id: str, node_id: str) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        node = self._node(project_id, node_id)
        self._session.delete(node)
        self._session.flush()
        return StoryMutation(
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.OUTLINE,
                    entity_id=node_id,
                )
            ]
        )


__all__ = ["SqlAlchemyOutlineWorkspace"]
