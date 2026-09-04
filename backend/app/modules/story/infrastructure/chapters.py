"""SQLAlchemy chapter and snapshot application adapter."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ....architecture.uow import SqlAlchemyUnitOfWork
from ....core.db_helpers import get_outline_node_or_404, get_project_or_404
from ....core.exceptions import ConflictError, NotFoundError, ValidationError
from ....core.utils import count_words
from ....services.cataloging.chapter_rollback import (
    pop_delete_rollback_result,
    rollback_cataloging_from_chapter,
)
from ....services.chapter_ordering import next_chapter_sort_order
from ....services.chapter_service import (
    chapter_to_detail,
    chapter_to_list_item,
    create_snapshot,
    diff_snapshots,
    ensure_current_snapshot,
    restore_chapter_from_snapshot,
    snapshot_to_item,
)
from ....services.narrative_governance import create_narrative_checkpoint
from ....services.outline_service import load_outline_nodes, outline_sort_context
from ..application.results import StoryMutation
from ..domain.content_sync import ContentSyncIntent, ContentSyncTarget
from .entities import Chapter, ChapterSnapshot, OutlineNode


class SqlAlchemyChapterWorkspace:
    """Keep chapter persistence, snapshots, cataloging rollback and mirror intents atomic."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _chapter(self, project_id: str, chapter_id: str) -> Chapter:
        chapter = (
            self._session.query(Chapter)
            .filter(Chapter.id == chapter_id, Chapter.project_id == project_id)
            .first()
        )
        if not chapter:
            raise NotFoundError("章节不存在")
        return chapter

    def _snapshot(
        self, project_id: str, chapter_id: str, snapshot_id: str
    ) -> ChapterSnapshot:
        snapshot = (
            self._session.query(ChapterSnapshot)
            .join(Chapter, Chapter.id == ChapterSnapshot.chapter_id)
            .filter(
                Chapter.project_id == project_id,
                Chapter.id == chapter_id,
                ChapterSnapshot.id == snapshot_id,
            )
            .first()
        )
        if not snapshot:
            raise NotFoundError("章节快照不存在")
        return snapshot

    def _outline_context(self, project_id: str) -> dict:
        return outline_sort_context(load_outline_nodes(self._session, project_id))

    def chapter_outline_exists(self, project_id: str, outline_node_id: str) -> bool:
        return (
            self._session.query(OutlineNode.id)
            .filter(
                OutlineNode.id == outline_node_id,
                OutlineNode.project_id == project_id,
                OutlineNode.node_type == "chapter",
            )
            .first()
            is not None
        )

    def _validate_manifest_reference(
        self, project_id: str, manifest_id: str | None
    ) -> None:
        if not manifest_id:
            return
        from ....database.models import ContextManifest

        # Freshness is an execution-time guard: generation must not start from
        # stale context. Once a draft has been generated and handed to the
        # author, the manifest is immutable provenance for that draft. Later
        # source edits must not prevent the author from promoting reviewed text.
        manifest = (
            self._session.query(ContextManifest)
            .filter(
                ContextManifest.id == manifest_id,
                ContextManifest.project_id == project_id,
            )
            .first()
        )
        if manifest is None:
            raise ValidationError("上下文清单不存在或不属于当前作品")

    def create_narrative_checkpoint(
        self,
        project_id: str,
        *,
        chapter_id: str | None,
        label: str,
        trigger_type: str,
    ) -> dict:
        chapter = self._chapter(project_id, chapter_id) if chapter_id else None
        with SqlAlchemyUnitOfWork.from_session(self._session) as uow:
            row = create_narrative_checkpoint(
                self._session,
                project_id,
                chapter=chapter,
                label=label,
                trigger_type=trigger_type,
            )
            uow.commit()
        return {
            "id": row.id,
            "sequence": row.sequence,
            "label": row.label,
            "chapter_snapshot_id": row.chapter_snapshot_id,
        }

    def list(self, project_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        outline_context = self._outline_context(project_id)
        chapters = (
            self._session.query(Chapter)
            .filter(Chapter.project_id == project_id)
            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())
            .all()
        )
        items = [chapter_to_list_item(chapter, outline_context) for chapter in chapters]
        return {"items": items, "total": len(items)}

    def detail(self, project_id: str, chapter_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        return chapter_to_detail(
            self._chapter(project_id, chapter_id), self._outline_context(project_id)
        )

    def create(self, project_id: str, payload: dict[str, Any]) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        get_outline_node_or_404(self._session, project_id, payload.get("outline_node_id"))
        self._validate_manifest_reference(project_id, payload.get("context_manifest_id"))
        content = payload.get("content") or ""
        chapter = Chapter(
            project_id=project_id,
            outline_node_id=payload.get("outline_node_id"),
            title=payload["title"],
            content=content,
            word_count=count_words(content),
            current_version=1,
            cataloging_required=bool(str(payload.get("content") or "").strip()),
            sort_order=next_chapter_sort_order(self._session, project_id),
            context_manifest_id=payload.get("context_manifest_id"),
        )
        self._session.add(chapter)
        self._session.flush()
        self._session.add(create_snapshot(chapter, "manual_save"))
        create_narrative_checkpoint(
            self._session,
            project_id,
            chapter=chapter,
            label=f"{chapter.title} 创建",
            trigger_type="chapter_create",
        )
        self._session.flush()
        self._session.refresh(chapter)
        return StoryMutation(
            data=chapter_to_detail(chapter, self._outline_context(project_id)),
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.CHAPTER,
                    entity_id=chapter.id,
                )
            ],
        )

    def save(
        self, project_id: str, chapter_id: str, payload: dict[str, Any]
    ) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        chapter = self._chapter(project_id, chapter_id)
        data = dict(payload)
        trigger_type = data.pop("trigger_type", "manual_save")
        expected_version = data.pop("expected_version", None)
        cataloging_impact = str(
            data.pop("cataloging_impact", "semantic") or "semantic"
        ).strip().lower()
        if cataloging_impact not in {"semantic", "style_only"}:
            raise ValidationError("cataloging_impact 必须是 semantic 或 style_only")
        if (
            expected_version is not None
            and int(expected_version) != int(chapter.current_version or 1)
        ):
            raise ConflictError(
                f"章节已从 v{expected_version} 更新为 v{chapter.current_version or 1}；"
                "已保留你的编辑内容，请重新载入并对比后再保存"
            )
        if not data:
            raise ValidationError("未提供任何更新字段")

        ensure_current_snapshot(self._session, chapter, "manual_save")
        content_changed = (
            "content" in data
            and (data.get("content") or "") != (chapter.content or "")
        )
        title_changed = "title" in data and data.get("title") != chapter.title
        outline_changed = (
            "outline_node_id" in data
            and data.get("outline_node_id") != chapter.outline_node_id
        )
        chapter_text_changed = content_changed or title_changed or outline_changed
        if cataloging_impact == "style_only" and (title_changed or outline_changed):
            raise ValidationError("仅润色模式只能修改正文表达，不能修改章节标题或大纲关联")

        rollback = None
        semantic_change = chapter_text_changed and cataloging_impact == "semantic"
        if semantic_change:
            rollback = rollback_cataloging_from_chapter(
                self._session,
                project_id,
                chapter.id,
                reason=f"《{chapter.title}》发生剧情或事实修改；该章及后续章节的旧建档投影已回退",
            )

        if "outline_node_id" in data:
            get_outline_node_or_404(
                self._session, project_id, data["outline_node_id"]
            )
            chapter.outline_node_id = data["outline_node_id"]
        if "title" in data:
            chapter.title = data["title"]
        if "content" in data:
            chapter.content = data["content"] or ""
        if "context_manifest_id" in data:
            self._validate_manifest_reference(project_id, data["context_manifest_id"])
            chapter.context_manifest_id = data["context_manifest_id"]
        chapter.word_count = count_words(chapter.content or "")
        chapter.current_version = (chapter.current_version or 1) + 1
        if semantic_change:
            chapter.cataloging_required = bool((chapter.content or "").strip())

        self._session.add(create_snapshot(chapter, trigger_type))
        create_narrative_checkpoint(
            self._session,
            project_id,
            chapter=chapter,
            label=f"{chapter.title} v{chapter.current_version}",
            trigger_type=trigger_type,
        )
        self._session.flush()
        self._session.refresh(chapter)
        detail = chapter_to_detail(chapter, self._outline_context(project_id))
        detail["chapter_text_changed"] = chapter_text_changed
        detail["narrative_content_changed"] = semantic_change
        detail["cataloging_impact"] = cataloging_impact
        detail["cataloging_rollback"] = rollback
        detail["recatalog_required_chapter_ids"] = (
            rollback.get("recatalog_required_chapter_ids", []) if rollback else []
        )
        detail["governance_invalidated_count"] = (
            rollback.get("governance_invalidated_count", 0) if rollback else 0
        )
        sync_intents = [
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHAPTER,
                entity_id=chapter.id,
            )
        ]
        if rollback:
            sync_intents.append(
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.PROJECT,
                    source="chapter_cataloging_rollback",
                )
            )
        return StoryMutation(data=detail, sync_intents=sync_intents)

    def reorder(self, project_id: str, chapter_ids: list[str]) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        chapters = (
            self._session.query(Chapter)
            .filter(Chapter.project_id == project_id)
            .order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc())
            .all()
        )
        existing_ids = [chapter.id for chapter in chapters]
        requested_ids = [str(chapter_id) for chapter_id in chapter_ids]
        if len(requested_ids) != len(set(requested_ids)):
            raise ValidationError("章节排序中不能包含重复章节")
        if set(requested_ids) != set(existing_ids):
            raise ValidationError("章节排序必须包含当前作品的全部章节")

        by_id = {chapter.id: chapter for chapter in chapters}
        for index, chapter_id in enumerate(requested_ids, start=1):
            by_id[chapter_id].sort_order = index * 1000
        self._session.flush()
        return StoryMutation(
            data=self.list(project_id),
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.PROJECT,
                    source="chapter_reorder",
                )
            ],
        )

    def delete(self, project_id: str, chapter_id: str) -> StoryMutation:
        project = get_project_or_404(self._session, project_id)
        chapter = self._chapter(project_id, chapter_id)
        content_file_path = chapter.content_file_path
        title = chapter.title
        self._session.delete(chapter)
        self._session.flush()
        rollback = pop_delete_rollback_result(self._session, chapter_id) or {
            "affected_chapter_ids": [chapter_id],
            "recatalog_required_chapter_ids": [],
            "warnings": [],
        }
        return StoryMutation(
            data={
                "deleted_chapter_id": chapter_id,
                "deleted_chapter_title": title,
                "cataloging_rollback": rollback,
                "recatalog_required_chapter_ids": rollback.get(
                    "recatalog_required_chapter_ids", []
                ),
            },
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.FILE_DELETE,
                    entity_id=chapter_id,
                    payload={
                        "folder_path": project.folder_path,
                        "relative_path": content_file_path,
                    },
                ),
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.PROJECT,
                    source="chapter_cataloging_rollback",
                ),
            ],
        )

    def snapshots(self, project_id: str, chapter_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        chapter = self._chapter(project_id, chapter_id)
        snapshots = (
            self._session.query(ChapterSnapshot)
            .filter(ChapterSnapshot.chapter_id == chapter.id)
            .order_by(
                ChapterSnapshot.version_number.desc(),
                ChapterSnapshot.created_at.desc(),
            )
            .all()
        )
        items = [snapshot_to_item(snapshot) for snapshot in snapshots]
        return {"items": items, "total": len(items)}

    def snapshot(self, project_id: str, chapter_id: str, snapshot_id: str) -> dict:
        get_project_or_404(self._session, project_id)
        snapshot = self._snapshot(project_id, chapter_id, snapshot_id)
        data = snapshot_to_item(snapshot)
        data["content"] = snapshot.content or ""
        return data

    def diff(
        self,
        project_id: str,
        chapter_id: str,
        from_snapshot_id: str,
        to_snapshot_id: str,
    ) -> dict:
        get_project_or_404(self._session, project_id)
        self._chapter(project_id, chapter_id)
        return diff_snapshots(
            self._snapshot(project_id, chapter_id, from_snapshot_id),
            self._snapshot(project_id, chapter_id, to_snapshot_id),
        )

    def restore(
        self, project_id: str, chapter_id: str, snapshot_id: str
    ) -> StoryMutation:
        get_project_or_404(self._session, project_id)
        chapter = self._chapter(project_id, chapter_id)
        snapshot = self._snapshot(project_id, chapter_id, snapshot_id)
        rollback = rollback_cataloging_from_chapter(
            self._session,
            project_id,
            chapter.id,
            reason=f"《{chapter.title}》恢复了历史正文；该章及后续章节的旧建档投影已回退",
        )
        restore_chapter_from_snapshot(self._session, chapter, snapshot)
        chapter.cataloging_required = bool((chapter.content or "").strip())
        create_narrative_checkpoint(
            self._session,
            project_id,
            chapter=chapter,
            label=f"{chapter.title} 恢复历史版本",
            trigger_type="restore",
        )
        self._session.flush()
        self._session.refresh(chapter)
        data = chapter_to_detail(chapter, self._outline_context(project_id))
        data.update(
            {
                "cataloging_rollback": rollback,
                "recatalog_required_chapter_ids": rollback.get(
                    "recatalog_required_chapter_ids", []
                ),
                "governance_invalidated_count": rollback.get(
                    "governance_invalidated_count", 0
                ),
            }
        )
        return StoryMutation(
            data=data,
            sync_intents=[
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.CHAPTER,
                    entity_id=chapter.id,
                ),
                ContentSyncIntent(
                    project_id=project_id,
                    target=ContentSyncTarget.PROJECT,
                    source="chapter_cataloging_rollback",
                ),
            ],
        )


__all__ = ["SqlAlchemyChapterWorkspace"]
