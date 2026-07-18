"""SQLAlchemy project workspace adapter."""
from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ....core.exceptions import NotFoundError, ValidationError
from ....services.storage_contract import storage_health
from ....services.workspace import execute_workspace_action
from ..application.results import StoryMutation
from ..domain.content_sync import ContentSyncIntent, ContentSyncTarget
from .entities import Project


def _project_data(project: Project, *, compact: bool = False) -> dict[str, Any]:
    data = {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "tags": project.tags,
        "storage_mode": project.storage_mode,
        "folder_path": project.folder_path,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
    if not compact:
        data.update(
            {
                "narrative_perspective": project.narrative_perspective,
                "writing_style": project.writing_style,
                "forbidden_sentence_patterns": project.forbidden_sentence_patterns,
                "rhetoric_guidelines": project.rhetoric_guidelines,
                "short_sentences": bool(project.short_sentences),
                "custom_style_prompt": project.custom_style_prompt,
                "daily_word_goal": project.daily_word_goal,
                "content_migrated_at": project.content_migrated_at,
            }
        )
    return data


class SqlAlchemyProjectWorkspace:
    """Own project persistence while returning transport-neutral dictionaries."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _project(self, project_id: str) -> Project:
        project = self._session.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise NotFoundError("作品不存在")
        return project

    def list(self, query: str | None = None) -> dict:
        statement = self._session.query(Project)
        if query:
            keyword = f"%{query}%"
            statement = statement.filter(
                or_(Project.title.like(keyword), Project.description.like(keyword))
            )
        projects = statement.order_by(Project.updated_at.desc()).all()
        items = [_project_data(project, compact=True) for project in projects]
        return {"items": items, "total": len(items)}

    def get(self, project_id: str) -> dict:
        return _project_data(self._project(project_id))

    def create(self, payload: dict[str, Any]) -> StoryMutation:
        data = dict(payload)
        if data.get("tags") is not None:
            data["tags"] = json.dumps(data["tags"], ensure_ascii=False)
        project = Project(**data)
        self._session.add(project)
        self._session.flush()
        return StoryMutation(
            data=_project_data(project),
            sync_intents=[
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.PROJECT,
                    entity_id=project.id,
                )
            ],
        )

    def update(self, project_id: str, payload: dict[str, Any]) -> StoryMutation:
        project = self._project(project_id)
        data = dict(payload)
        if not data:
            raise ValidationError("未提供任何更新字段")
        if "tags" in data and data["tags"] is not None:
            data["tags"] = json.dumps(data["tags"], ensure_ascii=False)
        for field, value in data.items():
            setattr(project, field, value)
        self._session.flush()
        return StoryMutation(
            data=_project_data(project),
            sync_intents=[
                ContentSyncIntent(
                    project_id=project.id,
                    target=ContentSyncTarget.PROJECT_MANIFEST,
                    entity_id=project.id,
                )
            ],
        )

    def storage_health(self, project_id: str) -> dict:
        return storage_health(self._session, self._project(project_id))

    async def repair_storage(
        self,
        project_id: str,
        action: Literal["import_orphans", "refresh_mirror"],
    ) -> dict:
        self._project(project_id)
        arguments = (
            {"direction": "import", "confirm_import_from_files": True}
            if action == "import_orphans"
            else {"direction": "db_to_files"}
        )
        result = await execute_workspace_action(
            self._session,
            project_id,
            {"tool": "sync_project_files", "arguments": arguments},
        )
        data = dict(result.get("data") or {})
        data["tool_status"] = result.get("status")
        data["tool_detail"] = result.get("detail")
        return data

    def delete(self, project_id: str) -> StoryMutation:
        project = self._project(project_id)
        folder_path = project.folder_path
        self._session.delete(project)
        self._session.flush()
        return StoryMutation(
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.PROJECT_DELETE,
                    entity_id=project_id,
                    payload={"folder_path": folder_path},
                )
            ]
        )


__all__ = ["SqlAlchemyProjectWorkspace"]
