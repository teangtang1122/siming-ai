"""Short-lived generated draft cache for workspace tools.

The assistant model should not have to copy a full chapter body back into a
tool-call argument. Tool-call arguments are a common place for long text to get
truncated, so writers store the full text here and write tools can resolve it by
draft id or by matching a provided prefix.

Drafts are persisted to SQLite (chapter_drafts table) so they survive server
restarts. The in-memory OrderedDict acts as an L1 cache for fast lookups.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.architecture.uow import commit_session, session_commits_deferred

from ...core.utils import count_words

MAX_CHAPTER_DRAFTS = 64

_CHAPTER_DRAFTS: OrderedDict[str, dict[str, Any]] = OrderedDict()


class PendingChapterDraftConflict(RuntimeError):
    """Another generated draft already owns the author's editor slot."""

    def __init__(self, draft: Any):
        super().__init__("a pending chapter draft already exists")
        self.draft = draft


class ChapterDraftOutlineConflict(RuntimeError):
    """The target outline acquired formal prose while generation was running."""

    def __init__(self, chapter: Any):
        super().__init__("the target outline already has a formal chapter")
        self.chapter = chapter


class ChapterDraftTargetConflict(RuntimeError):
    """The explicitly selected revision target disappeared or changed identity."""

    def __init__(self, target_chapter_id: str):
        super().__init__("the revision target no longer matches the selected outline")
        self.target_chapter_id = target_chapter_id


def _cache_chapter_draft(
    *,
    draft_id: str,
    project_id: str,
    title: str,
    outline_node_id: str | None,
    context_manifest_id: str | None,
    saved_chapter_id: str | None,
    status: str,
    content: str,
    created_at: datetime,
    draft_kind: str = "new",
    target_chapter_id: str | None = None,
    base_chapter_version: int | None = None,
) -> None:
    _CHAPTER_DRAFTS[draft_id] = {
        "project_id": project_id,
        "title": title,
        "outline_node_id": outline_node_id or "",
        "context_manifest_id": context_manifest_id or "",
        "saved_chapter_id": saved_chapter_id or "",
        "draft_kind": draft_kind or "new",
        "target_chapter_id": target_chapter_id or "",
        "base_chapter_version": base_chapter_version,
        "status": status,
        "content": content,
        "created_at": created_at,
    }
    _CHAPTER_DRAFTS.move_to_end(draft_id)
    while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
        _CHAPTER_DRAFTS.popitem(last=False)


def _set_chapter_draft_status(draft: Any, status: str, *, db: Any) -> None:
    draft.status = status
    draft.updated_at = datetime.utcnow()
    if session_commits_deferred(db):
        # A cache miss is safe across either commit or rollback; retaining a
        # pre-transaction "pending" entry after commit is not.
        _CHAPTER_DRAFTS.pop(str(draft.id), None)
        return
    cached = _CHAPTER_DRAFTS.get(str(draft.id))
    if cached:
        cached["status"] = status


def _set_chapter_draft_superseded(draft: Any, *, db: Any) -> None:
    _set_chapter_draft_status(draft, "superseded", db=db)


def lock_chapter_draft_project(db: Any, project_id: str) -> Any | None:
    """Serialize formal chapter creation and generated-draft finalization."""
    from ...database.models import Project

    return (
        db.query(Project)
        .filter(Project.id == project_id)
        .with_for_update()
        .first()
    )


def store_chapter_draft(
    *,
    project_id: str,
    content: str,
    title: str = "",
    outline_node_id: str | None = None,
    context_manifest_id: str | None = None,
    target_chapter_id: str | None = None,
    base_chapter_version: int | None = None,
    db: Any = None,
) -> str:
    """Persist one author-visible draft without replacing an existing draft.

    A model call can take minutes, so all checks made before generation are
    stale by definition. The transaction is completed here before checking the
    two authoritative conflicts again. The database partial unique index is the
    final guard when two completions reach this function at the same time.
    """
    draft_id = str(uuid4())
    created_at = datetime.utcnow()
    draft_kind = "revision" if target_chapter_id else "new"
    if db is None:
        _cache_chapter_draft(
            draft_id=draft_id,
            project_id=project_id,
            title=title or "",
            outline_node_id=outline_node_id,
            context_manifest_id=context_manifest_id,
            saved_chapter_id=None,
            draft_kind=draft_kind,
            target_chapter_id=target_chapter_id,
            base_chapter_version=base_chapter_version,
            status="pending",
            content=content,
            created_at=created_at,
        )
        return draft_id

    from ...database.models import Chapter, ChapterDraft

    # End the pre-generation transaction so the checks below see chapters and
    # drafts committed while the model request was in flight.
    commit_session(db)
    lock_chapter_draft_project(db, project_id)

    target_chapter = None
    if target_chapter_id:
        target_chapter = db.query(Chapter).filter(
            Chapter.project_id == project_id,
            Chapter.id == target_chapter_id,
        ).first()
        if not target_chapter:
            db.rollback()
            raise ChapterDraftTargetConflict(target_chapter_id)
        if outline_node_id and str(target_chapter.outline_node_id or "") != outline_node_id:
            db.rollback()
            raise ChapterDraftTargetConflict(target_chapter_id)
        outline_node_id = str(target_chapter.outline_node_id or outline_node_id or "") or None
        if base_chapter_version is None:
            base_chapter_version = int(target_chapter.current_version or 1)
    elif outline_node_id:
        existing_chapter = db.query(Chapter).filter(
            Chapter.project_id == project_id,
            Chapter.outline_node_id == outline_node_id,
        ).first()
        if existing_chapter:
            db.rollback()
            raise ChapterDraftOutlineConflict(existing_chapter)

    existing_draft = (
        db.query(ChapterDraft)
        .filter(
            ChapterDraft.project_id == project_id,
            ChapterDraft.status == "pending",
        )
        .order_by(ChapterDraft.updated_at.desc(), ChapterDraft.created_at.desc())
        .first()
    )
    if existing_draft:
        db.rollback()
        raise PendingChapterDraftConflict(existing_draft)

    row = ChapterDraft(
        id=draft_id,
        project_id=project_id,
        title=title or "",
        outline_node_id=outline_node_id or None,
        context_manifest_id=context_manifest_id or None,
        draft_kind=draft_kind,
        target_chapter_id=target_chapter_id or None,
        base_chapter_version=base_chapter_version,
        status="pending",
        content=content,
        created_at=created_at,
    )
    db.add(row)
    try:
        commit_session(db)
    except IntegrityError:
        db.rollback()
        concurrent_draft = (
            db.query(ChapterDraft)
            .filter(
                ChapterDraft.project_id == project_id,
                ChapterDraft.status == "pending",
            )
            .order_by(ChapterDraft.updated_at.desc(), ChapterDraft.created_at.desc())
            .first()
        )
        if concurrent_draft:
            raise PendingChapterDraftConflict(concurrent_draft) from None
        if outline_node_id and not target_chapter_id:
            concurrent_chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id,
                Chapter.outline_node_id == outline_node_id,
            ).first()
            if concurrent_chapter:
                raise ChapterDraftOutlineConflict(concurrent_chapter) from None
        raise

    if not session_commits_deferred(db):
        _cache_chapter_draft(
            draft_id=draft_id,
            project_id=project_id,
            title=title or "",
            outline_node_id=outline_node_id,
            context_manifest_id=context_manifest_id,
            saved_chapter_id=None,
            draft_kind=draft_kind,
            target_chapter_id=target_chapter_id,
            base_chapter_version=base_chapter_version,
            status="pending",
            content=content,
            created_at=row.created_at or created_at,
        )

    return draft_id


def get_chapter_draft(project_id: str, draft_id: str | None, *, db: Any = None) -> str | None:
    if not draft_id:
        return None
    entry = _CHAPTER_DRAFTS.get(str(draft_id))
    if entry and entry.get("project_id") == project_id:
        _CHAPTER_DRAFTS.move_to_end(str(draft_id))
        return str(entry.get("content") or "")

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            row = (
                db.query(ChapterDraft)
                .filter(ChapterDraft.id == str(draft_id), ChapterDraft.project_id == project_id)
                .first()
            )
            if row:
                content = str(row.content or "")
                _CHAPTER_DRAFTS[str(draft_id)] = {
                    "project_id": project_id,
                    "title": row.title or "",
                    "outline_node_id": row.outline_node_id or "",
                    "context_manifest_id": row.context_manifest_id or "",
                    "saved_chapter_id": row.saved_chapter_id or "",
                    "draft_kind": row.draft_kind or "new",
                    "target_chapter_id": row.target_chapter_id or "",
                    "base_chapter_version": row.base_chapter_version,
                    "status": row.status or "pending",
                    "content": content,
                    "created_at": row.created_at,
                }
                _CHAPTER_DRAFTS.move_to_end(str(draft_id))
                while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
                    _CHAPTER_DRAFTS.popitem(last=False)
                return content
        except Exception:
            pass

    return None


def get_chapter_draft_meta(
    project_id: str,
    draft_id: str | None,
    *,
    db: Any = None,
) -> dict[str, Any] | None:
    if not draft_id:
        return None
    entry = _CHAPTER_DRAFTS.get(str(draft_id))
    if entry and entry.get("project_id") == project_id:
        return {
            "title": str(entry.get("title") or ""),
            "outline_node_id": str(entry.get("outline_node_id") or ""),
            "context_manifest_id": str(entry.get("context_manifest_id") or ""),
            "saved_chapter_id": str(entry.get("saved_chapter_id") or ""),
            "draft_kind": str(entry.get("draft_kind") or "new"),
            "target_chapter_id": str(entry.get("target_chapter_id") or ""),
            "base_chapter_version": entry.get("base_chapter_version"),
            "status": str(entry.get("status") or "pending"),
            "content": str(entry.get("content") or ""),
        }

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            row = (
                db.query(ChapterDraft)
                .filter(ChapterDraft.id == str(draft_id), ChapterDraft.project_id == project_id)
                .first()
            )
            if row:
                return {
                    "title": row.title or "",
                    "outline_node_id": row.outline_node_id or "",
                    "context_manifest_id": row.context_manifest_id or "",
                    "saved_chapter_id": row.saved_chapter_id or "",
                    "draft_kind": row.draft_kind or "new",
                    "target_chapter_id": row.target_chapter_id or "",
                    "base_chapter_version": row.base_chapter_version,
                    "status": row.status or "pending",
                    "content": row.content or "",
                }
        except Exception:
            pass
    return None


def _looks_like_prefix(prefix: str, full: str) -> bool:
    prefix = prefix.strip()
    full = full.strip()
    if not prefix:
        return True
    if len(full) <= len(prefix):
        return False
    head = full[: max(200, min(len(prefix), 1200))]
    return head.startswith(prefix[: len(head)]) or prefix[:200] in full[:1200]


def resolve_chapter_draft_content(
    *,
    project_id: str,
    provided_content: str = "",
    draft_id: str | None = None,
    outline_node_id: str | None = None,
    db: Any = None,
) -> str:
    """Return the best full chapter content for a write/evaluation action."""
    provided = provided_content or ""
    direct = get_chapter_draft(project_id, draft_id, db=db)
    if direct and len(direct.strip()) > len(provided.strip()):
        return direct

    outline_id = str(outline_node_id or "").strip()
    for _id, entry in reversed(_CHAPTER_DRAFTS.items()):
        if entry.get("project_id") != project_id:
            continue
        if outline_id and str(entry.get("outline_node_id") or "") != outline_id:
            continue
        content = str(entry.get("content") or "")
        if content and _looks_like_prefix(provided, content):
            return content

    if db is not None:
        try:
            from ...database.models import ChapterDraft
            query = db.query(ChapterDraft).filter(
                ChapterDraft.project_id == project_id,
                ChapterDraft.status == "pending",
            )
            if outline_id:
                query = query.filter(ChapterDraft.outline_node_id == outline_id)
            rows = query.order_by(ChapterDraft.created_at.desc()).limit(10).all()
            for row in rows:
                content = str(row.content or "")
                if content and _looks_like_prefix(provided, content):
                    _CHAPTER_DRAFTS[str(row.id)] = {
                        "project_id": project_id,
                        "title": row.title or "",
                        "outline_node_id": row.outline_node_id or "",
                        "context_manifest_id": row.context_manifest_id or "",
                        "saved_chapter_id": row.saved_chapter_id or "",
                        "draft_kind": row.draft_kind or "new",
                        "target_chapter_id": row.target_chapter_id or "",
                        "base_chapter_version": row.base_chapter_version,
                        "status": row.status or "pending",
                        "content": content,
                        "created_at": row.created_at,
                    }
                    _CHAPTER_DRAFTS.move_to_end(str(row.id))
                    while len(_CHAPTER_DRAFTS) > MAX_CHAPTER_DRAFTS:
                        _CHAPTER_DRAFTS.popitem(last=False)
                    return content
        except Exception:
            pass

    return provided


def find_pending_chapter_draft(
    db: Any,
    project_id: str,
) -> Any | None:
    """Return the one author-visible draft that blocks further generation."""
    from ...database.models import ChapterDraft

    release_stale_pending_chapter_drafts(db, project_id)
    return (
        db.query(ChapterDraft)
        .filter(
            ChapterDraft.project_id == project_id,
            ChapterDraft.status == "pending",
        )
        .order_by(ChapterDraft.updated_at.desc(), ChapterDraft.created_at.desc())
        .first()
    )


def latest_pending_chapter_draft(db: Any, project_id: str) -> Any | None:
    from ...database.models import ChapterDraft

    release_stale_pending_chapter_drafts(db, project_id)
    return (
        db.query(ChapterDraft)
        .filter(
            ChapterDraft.project_id == project_id,
            ChapterDraft.status == "pending",
        )
        .order_by(ChapterDraft.updated_at.desc(), ChapterDraft.created_at.desc())
        .first()
    )


def find_chapter_draft(db: Any, project_id: str, draft_id: str) -> Any | None:
    from ...database.models import ChapterDraft

    return db.query(ChapterDraft).filter(
        ChapterDraft.id == draft_id,
        ChapterDraft.project_id == project_id,
    ).first()


def discard_chapter_draft(db: Any, project_id: str, draft_id: str) -> Any:
    """Release an unsaved author draft without touching formal chapter prose."""
    from ...core.exceptions import ValidationError

    lock_chapter_draft_project(db, project_id)
    draft = find_chapter_draft(db, project_id, draft_id)
    if draft is None:
        raise ValidationError("章节草稿不存在")
    if draft.status == "saved":
        raise ValidationError("该草稿已保存为正式章节；如需删除，请删除对应正式章节")
    if draft.status == "discarded":
        return draft
    if draft.status != "pending":
        raise ValidationError("该章节草稿已失效，不能再丢弃")
    _set_chapter_draft_status(draft, "discarded", db=db)
    commit_session(db)
    return draft


def release_stale_pending_chapter_drafts(db: Any, project_id: str) -> int:
    """Release legacy pending drafts whose outline already has formal prose."""
    from ...database.models import Chapter, ChapterDraft

    pending = db.query(ChapterDraft).filter(
        ChapterDraft.project_id == project_id,
        ChapterDraft.status == "pending",
        ChapterDraft.target_chapter_id.is_(None),
        ChapterDraft.outline_node_id.isnot(None),
    ).all()
    outline_ids = {str(draft.outline_node_id) for draft in pending if draft.outline_node_id}
    if not outline_ids:
        return 0

    used_outline_ids = {
        str(row[0])
        for row in db.query(Chapter.outline_node_id).filter(
            Chapter.project_id == project_id,
            Chapter.outline_node_id.in_(outline_ids),
        ).all()
        if row[0]
    }
    stale = [
        draft for draft in pending
        if str(draft.outline_node_id or "") in used_outline_ids
    ]
    if not stale:
        return 0
    for draft in stale:
        _set_chapter_draft_superseded(draft, db=db)
    commit_session(db)
    return len(stale)


def ensure_generated_draft_outline_is_unused(
    db: Any,
    project_id: str,
    outline_node_id: str | None,
    *,
    draft: Any = None,
) -> None:
    """Reject promotion and release a stale draft when formal prose exists."""
    if not outline_node_id:
        return

    from ...core.exceptions import ValidationError
    from ...database.models import Chapter

    existing = db.query(Chapter).filter(
        Chapter.project_id == project_id,
        Chapter.outline_node_id == outline_node_id,
    ).first()
    if existing:
        if draft is not None and draft.status == "pending":
            _set_chapter_draft_superseded(draft, db=db)
            commit_session(db)
        raise ValidationError(
            "该大纲已在草稿生成期间关联正式章节；"
            "迟到草稿已释放且不会阻塞后续写作，"
            "AI 新章草稿不能覆盖或伪装成已有章节"
        )


def pending_draft_block_result(tool: str, draft: Any) -> dict[str, Any]:
    from .turn_control import AssistantTurnDirective, apply_turn_directive

    return apply_turn_directive(
        {
            "tool": tool,
            "status": "blocked",
            "detail": "当前章节草稿尚未保存并完成建档，本轮未生成下一章。",
            "data": {
                "blocking_draft_id": draft.id,
                "outline_node_id": draft.outline_node_id,
                "allowed_actions": ["save_and_catalog", "save_only", "discard"],
            },
        },
        AssistantTurnDirective.BLOCKED_ON_CATALOGING,
    )


def update_chapter_draft(
    db: Any,
    project_id: str,
    draft_id: str,
    *,
    title: str,
    outline_node_id: str | None,
    content: str,
) -> Any:
    """Persist the editor's current unsaved text before the author saves it."""
    from ...core.exceptions import NotFoundError, ValidationError
    from ...database.models import ChapterDraft

    row = db.query(ChapterDraft).filter(
        ChapterDraft.id == draft_id,
        ChapterDraft.project_id == project_id,
    ).first()
    if not row:
        raise NotFoundError("章节草稿不存在")
    if row.status != "pending":
        raise ValidationError("该章节草稿已经处理或失效，不能重复保存")
    if (
        str(row.draft_kind or "new") == "revision"
        and str(row.outline_node_id or "") != str(outline_node_id or "")
    ):
        raise ValidationError("修订候选必须继续绑定生成时的正式章节，不能改挂到其他大纲")
    row.title = title
    row.outline_node_id = outline_node_id
    row.content = content
    cached = _CHAPTER_DRAFTS.get(draft_id)
    if cached:
        cached.update(
            {
                "title": title,
                "outline_node_id": outline_node_id or "",
                "content": content,
            }
        )
    return row


def mark_chapter_draft_saved(db: Any, draft: Any, chapter_id: str) -> None:
    draft.status = "saved"
    draft.saved_chapter_id = chapter_id
    cached = _CHAPTER_DRAFTS.get(str(draft.id))
    if cached:
        cached["status"] = "saved"
        cached["saved_chapter_id"] = chapter_id


def chapter_draft_result_data(draft: Any, *, db: Any = None) -> dict[str, Any]:
    data = {
        "draft_id": str(draft.id),
        "project_id": str(draft.project_id),
        "content_ref": str(draft.id),
        "title": str(draft.title or ""),
        "outline_node_id": draft.outline_node_id,
        "context_manifest_id": draft.context_manifest_id,
        "saved_chapter_id": draft.saved_chapter_id,
        "draft_kind": str(draft.draft_kind or "new"),
        "target_chapter_id": draft.target_chapter_id,
        "base_chapter_version": draft.base_chapter_version,
        "draft_status": str(draft.status or "pending"),
        "content": str(draft.content or ""),
        "word_count": count_words(str(draft.content or "")),
        "next_actions": (
            ["save_and_catalog", "save_only", "discard"]
            if draft.status == "pending"
            else []
        ),
    }
    if db is None or not draft.target_chapter_id:
        return data

    from ...database.models import Chapter

    target = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == draft.project_id,
            Chapter.id == draft.target_chapter_id,
        )
        .first()
    )
    if target:
        current_version = int(target.current_version or 1)
        data.update(
            {
                "target_chapter_title": target.title,
                "target_chapter_content": target.content or "",
                "target_chapter_current_version": current_version,
                "version_conflict": current_version
                != int(draft.base_chapter_version or 0),
            }
        )
    return data
