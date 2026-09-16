"""Apply shared authoring contracts when replaying a phone's ordered command journal."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.modules.story.infrastructure.chapters import SqlAlchemyChapterWorkspace
from app.modules.story.infrastructure.entities import (
    Chapter,
    ChapterSnapshot,
    CharacterRelationship,
)
from app.services.persistence.character_workspace import SqlAlchemyCharacterWorkspace
from app.services.workspace.generated_drafts import (
    discard_chapter_draft,
    find_chapter_draft,
    mark_chapter_draft_saved,
    update_chapter_draft,
)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: Literal[
        "chapter_reorder",
        "chapter_restore",
        "character_relationships",
        "chapter_draft_edit",
        "chapter_draft_status",
    ]
    arguments: dict
    expected: dict


class Reorder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chapter_ids: list[str]


class Restore(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chapter_id: str
    snapshot_version: int = Field(ge=1)
    snapshot_content: str


class ChapterGuard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    current_version: int = Field(ge=1)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
    id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$")
    source_character_id: str = Field(alias="from")
    target_character_id: str = Field(alias="to")
    relationship_type: str = Field(min_length=1, max_length=100)
    description: str = ""


class Relationships(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    character_id: str
    relationships: list[Edge]


class DraftGuard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class DraftEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    draft_id: str
    title: str = Field(min_length=1, max_length=200)
    content: str
    outline_node_id: str | None = None


class DraftStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    draft_id: str
    status: Literal["saved", "discarded"]
    saved_chapter_id: str | None = None
    saved_content_hash: str | None = None


def relationship_guard(db: Session, project_id: str, character_id: str) -> list[dict]:
    rows = (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.project_id == project_id,
            (CharacterRelationship.character_a_id == character_id)
            | (CharacterRelationship.character_b_id == character_id),
        )
        .all()
    )
    return sorted(
        [
            {
                "from": row.character_a_id,
                "to": row.character_b_id,
                "relationship_type": row.relationship_type or "",
                "description": row.description or "",
            }
            for row in rows
        ],
        key=lambda row: (row["from"], row["to"]),
    )


def apply_authoring_command(db: Session, project_id: str, payload: dict) -> None:
    command = Command.model_validate(payload)
    chapters = SqlAlchemyChapterWorkspace(db)
    if command.command == "chapter_reorder":
        request = Reorder.model_validate(command.arguments)
        expected = Reorder.model_validate(command.expected)
        current = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id)
            .order_by(
                Chapter.sort_order,
                Chapter.created_at,
                Chapter.id,
            )
            .all()
        )
        if [chapter.id for chapter in current] != expected.chapter_ids:
            raise ValidationError("章节顺序已在另一设备变化；手机排序已保留，请核对后同步")
        chapters.reorder(project_id, request.chapter_ids)
    elif command.command == "chapter_restore":
        request = Restore.model_validate(command.arguments)
        expected = ChapterGuard.model_validate(command.expected)
        chapter = chapters.detail(project_id, request.chapter_id)
        digest = hashlib.sha256((chapter.get("content") or "").encode("utf-8")).hexdigest()
        if (
            chapter["current_version"] != expected.current_version
            or digest != expected.content_hash
        ):
            raise ValidationError("正式章节已在另一设备变化；手机恢复版本已保留，请核对后同步")
        snapshot = (
            db.query(ChapterSnapshot)
            .filter(
                ChapterSnapshot.chapter_id == request.chapter_id,
                ChapterSnapshot.version_number == request.snapshot_version,
                ChapterSnapshot.content == request.snapshot_content,
            )
            .first()
        )
        if snapshot is None:
            raise ValidationError("恢复来源快照不存在或与手机记录不一致")
        chapters.restore(project_id, request.chapter_id, snapshot.id)
    elif command.command == "character_relationships":
        request = Relationships.model_validate(command.arguments)
        if (
            set(command.expected) != {"relationships"}
            or relationship_guard(db, project_id, request.character_id)
            != command.expected["relationships"]
        ):
            raise ValidationError("角色关系已在另一设备变化；手机关系已保留，请核对后同步")
        if len({edge.id for edge in request.relationships}) != len(request.relationships):
            raise ValidationError("关系 ID 不能重复")
        for edge in request.relationships:
            existing = db.get(CharacterRelationship, edge.id)
            if existing is not None and (
                existing.project_id != project_id
                or request.character_id not in {existing.character_a_id, existing.character_b_id}
            ):
                raise ValidationError("不能覆盖其他角色或作品的关系")
        SqlAlchemyCharacterWorkspace(db).replace_relationships(
            project_id, request.character_id, request.relationships
        )
    else:
        request = (
            DraftEdit if command.command == "chapter_draft_edit" else DraftStatus
        ).model_validate(command.arguments)
        expected = DraftGuard.model_validate(command.expected)
        draft = find_chapter_draft(db, project_id, request.draft_id)
        if (
            draft is None
            or draft.status != "pending"
            or hashlib.sha256((draft.content or "").encode()).hexdigest() != expected.content_hash
        ):
            raise ValidationError("章节草稿已在另一设备变化，手机修改已保留")
        if isinstance(request, DraftEdit):
            if request.outline_node_id != draft.outline_node_id:
                raise ValidationError("手机编辑草稿不能改挂大纲")
            update_chapter_draft(
                db,
                project_id,
                request.draft_id,
                title=request.title,
                content=request.content,
                outline_node_id=request.outline_node_id,
            )
        elif request.status == "discarded":
            discard_chapter_draft(db, project_id, request.draft_id)
        else:
            chapter = chapters.detail(project_id, request.saved_chapter_id or "")
            if (
                hashlib.sha256((chapter.get("content") or "").encode()).hexdigest()
                != request.saved_content_hash
            ):
                raise ValidationError("已保存正文与手机确认结果不一致")
            if (
                draft.draft_kind == "revision"
                and draft.target_chapter_id != request.saved_chapter_id
            ):
                raise ValidationError("修订候选必须保存到其原目标章节")
            if (draft.outline_node_id or "") != (chapter.get("outline_node_id") or ""):
                raise ValidationError("已保存章节的大纲与草稿不一致")
            mark_chapter_draft_saved(db, draft, request.saved_chapter_id)
    db.flush()
