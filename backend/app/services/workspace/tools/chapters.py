"""Chapter workspace tools."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ....core.utils import count_words
from ....database.models import (
    Chapter,
    ChapterCharacter,
    ChapterSnapshot,
    ChapterSummary,
    ChapterWriteClaim,
    Character,
    CharacterChangeLog,
    CharacterTimeline,
    OperationRun,
    Project,
)
from ....modules.story.application.content_sync import queue_content_sync
from ....modules.story.domain.content_sync import ContentSyncIntent, ContentSyncTarget
from ....services.chapter_ordering import next_chapter_sort_order
from ....services.chapter_service import (
    create_snapshot,
    diff_snapshots,
    ensure_current_snapshot,
    restore_chapter_from_snapshot,
    snapshot_to_item,
)
from ....services.narrative_governance import (
    create_narrative_checkpoint,
    mark_governance_items_stale_for_chapter,
)
from ....services.cataloging.launcher import (
    AUTO_CHAPTER_WRITE_SOURCE,
    create_and_queue_cataloging_job,
    resolve_write_cataloging_route,
)
from ....services.narrative_ledger import restore_ledger_checkpoint
from ....services.style_rules import _repair_forbidden_sentence_text
from ...operation_runtime import current_operation_id
from ..generated_drafts import get_chapter_draft_meta, resolve_chapter_draft_content
from ..idempotency import (
    acquire_chapter_write_claim,
    chapter_write_target_key,
    check_idempotency,
    complete_chapter_write_claim,
    fail_chapter_write_claim,
    generate_idempotency_key,
    validate_chapter_write_claim,
)
from ..utils import find_outline_by_title_or_id


def _character_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(name) for name in value if name]


def _context_write_gate(
    db: Session,
    project_id: str,
    context_manifest_id: str | None,
    *,
    require_manifest: bool = False,
) -> tuple[bool, str, object | None]:
    """Validate a governed generation before it becomes project state."""
    if not context_manifest_id:
        if require_manifest:
            return False, "External Agent formal writes require a prepared context manifest and verified evidence.", None
        # Old drafts and manual writes predate manifests. Keep them readable and
        # writable; new external flows always attach a manifest at draft time.
        return True, "", None
    from ....services.context_orchestrator import manifest_is_usable

    ok, detail, manifest = manifest_is_usable(
        db,
        context_manifest_id,
        project_id=project_id,
        require_external_evidence=False,
    )
    # Resolve the external-evidence requirement after we have the manifest.
    if manifest is not None:
        ok, detail, manifest = manifest_is_usable(
            db,
            context_manifest_id,
            project_id=project_id,
            # The caller route is authoritative here. An MCP Agent must not
            # be able to bypass evidence submission by supplying a manifest
            # initially prepared by an internal API or workspace path.
            require_external_evidence=(
                require_manifest
                or getattr(manifest, "execution_route", "") in {"external_mcp", "local_cli_agent"}
            ),
        )
    return ok, detail, manifest


def _context_gate_status(manifest: object | None) -> str:
    """Map an unusable manifest to an actionable workspace result status."""
    status = str(getattr(manifest, "status", "") or "")
    if status in {"needs_confirmation", "blocked_rebuild", "stale"}:
        return status
    # A ready Manifest can still be rejected because an external Agent has not
    # supplied evidence for every required anchor. That is a confirmation
    # action, not a successful `ready` tool result.
    return "needs_confirmation"


def _raise_if_chapter_write_cancelled(
    db: Session,
    claim_id: str | None,
    claim_token: str | None,
) -> None:
    """Fence the final mutation against task, operation, and claim cancellation."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError

    operation_ids = {str(current_operation_id() or "").strip()}
    if claim_id:
        claim = (
            db.query(ChapterWriteClaim)
            .filter(ChapterWriteClaim.id == claim_id)
            .populate_existing()
            .first()
        )
        if (
            claim is None
            or claim.status != "running"
            or not claim_token
            or claim.claim_token != claim_token
        ):
            raise asyncio.CancelledError
        operation_ids.add(str(claim.operation_id or "").strip())

    for operation_id in operation_ids - {""}:
        operation = (
            db.query(OperationRun)
            .filter(OperationRun.id == operation_id)
            .populate_existing()
            .first()
        )
        if operation is not None and operation.status not in {"queued", "running"}:
            raise asyncio.CancelledError


def _link_chapter_characters(
    db: Session,
    project_id: str,
    chapter_id: str,
    names: list[str],
    label: str,
) -> None:
    if not names:
        return
    characters = (
        db.query(Character)
        .filter(Character.project_id == project_id, Character.name.in_(names))
        .all()
    )
    for character in characters:
        db.add(ChapterCharacter(
            chapter_id=chapter_id,
            character_id=character.id,
            appearance_type="涉及",
            description=label,
        ))


def _find_chapter(db: Session, project_id: str, args: dict[str, Any]) -> Chapter | None:
    for ref in (args.get("id"), args.get("chapter_id")):
        text = str(ref or "").strip()
        if text:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id,
                Chapter.id == text,
            ).first()
            if chapter:
                return chapter
    title_ref = str(args.get("title") or args.get("chapter_title") or "").strip()
    if title_ref:
        chapter = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.title == title_ref)
            .order_by(Chapter.created_at.desc())
            .first()
        )
        if chapter:
            return chapter
    outline_node = None
    for ref in (args.get("outline_node_id"), args.get("outline_node_title"), args.get("outline_title")):
        outline_node = find_outline_by_title_or_id(db, project_id, ref, node_type="chapter")
        if outline_node:
            break
    if outline_node:
        return (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.outline_node_id == outline_node.id)
            .order_by(Chapter.created_at.desc())
            .first()
        )
    return None


def _chapter_version_data(chapter: Chapter) -> dict[str, Any]:
    return {
        "id": chapter.id,
        "chapter_id": chapter.id,
        "title": chapter.title,
        "word_count": chapter.word_count or 0,
        "current_version": chapter.current_version or 1,
        "updated_at": chapter.updated_at.isoformat() if chapter.updated_at else None,
    }


def _attach_automatic_cataloging(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Launch canonical single-chapter cataloging after a successful write."""

    data = result.get("data")
    if result.get("status") != "ok" or not isinstance(data, dict):
        return result
    chapter_data = data.get("chapter") if isinstance(data.get("chapter"), dict) else data
    chapter_id = str(chapter_data.get("chapter_id") or chapter_data.get("id") or "").strip()
    if not chapter_id or int(chapter_data.get("word_count") or 0) <= 0:
        return result
    try:
        model_override, backend_override, provider_override = resolve_write_cataloging_route(
            db,
            args,
            project_id=project_id,
        )
        _job, launch = create_and_queue_cataloging_job(
            db,
            project_id,
            [chapter_id],
            execution_mode="auto",
            model_override=model_override,
            backend_override=backend_override,
            provider_override=provider_override,
            trigger_source=AUTO_CHAPTER_WRITE_SOURCE,
            run_now=True,
        )
        data["cataloging_job"] = launch
        result["detail"] = f"{result.get('detail') or '章节已保存'}；已启动正式建档"
    except Exception as exc:
        # The chapter is already safely committed.  Surface a retryable task
        # warning instead of rolling back or pretending cataloging succeeded.
        data["cataloging_job"] = {
            "auto_started": False,
            "status": "failed_to_start",
            "error": str(exc)[:2000],
        }
        result["detail"] = f"{result.get('detail') or '章节已保存'}；正式建档启动失败，可在任务列表重试"
    return result


def _chapter_snapshots(db: Session, chapter: Chapter) -> list[ChapterSnapshot]:
    return (
        db.query(ChapterSnapshot)
        .filter(ChapterSnapshot.chapter_id == chapter.id)
        .order_by(ChapterSnapshot.version_number.desc(), ChapterSnapshot.created_at.desc())
        .all()
    )


def _find_snapshot(db: Session, chapter: Chapter, args: dict[str, Any]) -> ChapterSnapshot | None:
    snapshot_id = str(args.get("snapshot_id") or args.get("version_id") or "").strip()
    if snapshot_id:
        return (
            db.query(ChapterSnapshot)
            .filter(ChapterSnapshot.chapter_id == chapter.id, ChapterSnapshot.id == snapshot_id)
            .first()
        )
    raw_version = args.get("version_number")
    if raw_version in (None, ""):
        raw_version = args.get("version")
    if raw_version not in (None, ""):
        try:
            version_number = int(raw_version)
        except (TypeError, ValueError):
            version_number = None
        if version_number:
            return (
                db.query(ChapterSnapshot)
                .filter(
                    ChapterSnapshot.chapter_id == chapter.id,
                    ChapterSnapshot.version_number == version_number,
                )
                .order_by(ChapterSnapshot.created_at.desc())
                .first()
            )
    snapshots = _chapter_snapshots(db, chapter)
    target = str(args.get("target") or "previous").strip().lower()
    if target in {"first", "initial", "oldest", "最初", "初版", "第一版"}:
        return snapshots[-1] if snapshots else None
    if target in {"latest", "newest", "最新"}:
        return snapshots[0] if snapshots else None
    current_version = chapter.current_version or 1
    for snapshot in snapshots:
        if (snapshot.version_number or 0) < current_version:
            return snapshot
    return None


def _chapter_write_candidates(
    db: Session,
    project_id: str,
    outline_node: object | None,
    title: str,
) -> list[Chapter]:
    query = db.query(Chapter).filter(Chapter.project_id == project_id)
    if outline_node:
        query = query.filter(Chapter.outline_node_id == getattr(outline_node, "id"))
    elif title:
        query = query.filter(Chapter.title == title[:200])
    else:
        return []
    return query.order_by(Chapter.sort_order.asc(), Chapter.created_at.asc(), Chapter.id.asc()).all()


def _save_chapter_summary(db: Session, chapter: Chapter, args: dict[str, Any]) -> None:
    summary_text = str(args.get("summary") or "").strip()
    if not summary_text:
        return
    model = str(args.get("model") or "") or None
    if chapter.summary:
        chapter.summary.summary_text = summary_text[:20000]
        chapter.summary.token_count = len(summary_text)
        chapter.summary.ai_model = model
        chapter.summary.updated_at = datetime.utcnow()
        return
    db.add(ChapterSummary(
        chapter_id=chapter.id,
        summary_text=summary_text[:20000],
        token_count=len(summary_text),
        ai_model=model,
    ))


def _queue_chapter_sync(db: Session, project_id: str, chapter_id: str, project: Project | None) -> None:
    if project:
        queue_content_sync(db, ContentSyncIntent(
            project_id=project_id,
            target=ContentSyncTarget.CHAPTER,
            entity_id=chapter_id,
            source="workspace_tool",
        ))


async def _persist_created_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    *,
    project: Project | None,
    outline_node: object | None,
    title: str,
    content: str,
    context_manifest_id: str | None,
    claim_id: str | None,
    claim_token: str | None,
) -> dict:
    skip_repair = bool(args.get("skip_style_repair") or args.get("skip_forbidden_repair"))
    if project and content.strip() and not skip_repair:
        content, _violations, _remaining = await _repair_forbidden_sentence_text(
            content, project, str(args.get("model") or "") or None
        )
    _raise_if_chapter_write_cancelled(db, claim_id, claim_token)
    if not title or not content.strip():
        fail_chapter_write_claim(
            db,
            claim_id,
            claim_token,
            error="章节标题或正文为空，本轮未创建章节",
        )
        return {
            "tool": "create_chapter",
            "status": "error",
            "detail": "章节标题或正文为空，本轮未创建章节",
        }
    candidates = _chapter_write_candidates(db, project_id, outline_node, title)
    existing_non_empty = next((item for item in candidates if str(item.content or "").strip()), None)
    existing_chapter = existing_non_empty or (candidates[0] if candidates else None)
    if existing_non_empty:
        result = {
            "tool": "create_chapter",
            "status": "ok",
            "detail": "该大纲节点已有正文，已打开现有章节",
            "data": {
                "id": existing_non_empty.id,
                "chapter_id": existing_non_empty.id,
                "title": existing_non_empty.title,
                "word_count": existing_non_empty.word_count or count_words(existing_non_empty.content or ""),
                "current_version": existing_non_empty.current_version or 1,
            },
        }
        _raise_if_chapter_write_cancelled(db, claim_id, claim_token)
        if not complete_chapter_write_claim(
            db, claim_id, claim_token, chapter_id=existing_non_empty.id, result=result
        ):
            raise RuntimeError("章节写作占用已失效，未写入重复内容")
        commit_session(db)
        return result
    reused_empty = existing_chapter is not None
    stale_count = 0
    if existing_chapter:
        chapter = existing_chapter
        ensure_current_snapshot(db, chapter, "manual_save")
        chapter.current_version = max(1, chapter.current_version or 1) + 1
        chapter.title = title[:200]
        chapter.content = content
        chapter.word_count = count_words(content)
        chapter.context_manifest_id = context_manifest_id
        chapter.updated_at = datetime.utcnow()
        stale_count = mark_governance_items_stale_for_chapter(
            db,
            project_id,
            chapter.id,
            reason=f"{chapter.title} 已由写作工具补全，旧治理结论需要复检",
            actor="ai_insert",
        )
        db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).delete()
    else:
        chapter = Chapter(
            project_id=project_id,
            outline_node_id=getattr(outline_node, "id", None),
            title=title[:200],
            content=content,
            word_count=count_words(content),
            current_version=1,
            sort_order=next_chapter_sort_order(db, project_id),
            context_manifest_id=context_manifest_id,
        )
        db.add(chapter)
        db.flush()

    db.add(create_snapshot(chapter, "ai_insert"))
    db.flush()
    checkpoint = create_narrative_checkpoint(
        db, project_id, chapter=chapter,
        label=f"{chapter.title} {'补全' if reused_empty else '创建'}",
        trigger_type="ai_insert",
    )
    _save_chapter_summary(db, chapter, args)
    _link_chapter_characters(
        db, project_id, chapter.id, _character_names(args.get("involved_characters")),
        f"由AI助手关联至章节「{title[:50]}」",
    )
    _queue_chapter_sync(db, project_id, chapter.id, project)
    result = {
        "tool": "create_chapter",
        "status": "ok",
        "detail": f"已{'补全' if reused_empty else '创建'}章节：{chapter.title}（{count_words(content)} 字）",
        "data": {
            "id": chapter.id,
            "chapter_id": chapter.id,
            "title": chapter.title,
            "word_count": count_words(content),
            "current_version": chapter.current_version or 1,
            "snapshot_count": len(chapter.snapshots),
            "narrative_checkpoint_id": checkpoint.id,
            "context_manifest_id": context_manifest_id,
            "reused_empty_chapter": reused_empty,
            "governance_invalidated_count": stale_count,
        },
    }
    _raise_if_chapter_write_cancelled(db, claim_id, claim_token)
    if not complete_chapter_write_claim(
        db, claim_id, claim_token, chapter_id=chapter.id, result=result
    ):
        raise RuntimeError("章节写作占用已失效，已回滚本次写入")
    commit_session(db)
    return result


async def create_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    draft_id = str(args.get("draft_id") or args.get("content_ref") or "").strip() or None
    draft_meta = get_chapter_draft_meta(project_id, draft_id, db=db) if draft_id else None
    context_manifest_id = str(
        args.get("context_manifest_id") or (draft_meta or {}).get("context_manifest_id") or ""
    ).strip() or None
    external_execution = str(args.get("_context_execution_route") or "").strip() in {
        "external_mcp", "local_cli_agent",
    }
    context_ok, context_detail, context_manifest = _context_write_gate(
        db, project_id, context_manifest_id, require_manifest=external_execution,
    )
    if not context_ok:
        return {
            "tool": "create_chapter",
            "status": _context_gate_status(context_manifest),
            "detail": context_detail,
            "data": {"context_manifest_id": context_manifest_id},
        }

    manifest_arguments = (
        (getattr(context_manifest, "query_json", None) or {}).get("arguments", {})
        if context_manifest is not None
        else {}
    )
    manifest_outline_ref = (
        manifest_arguments.get("outline_node_id")
        if isinstance(manifest_arguments, dict)
        else None
    )

    title = str(args.get("title") or (draft_meta or {}).get("title") or "").strip()
    content = resolve_chapter_draft_content(
        project_id=project_id,
        provided_content=str(args.get("content") or ""),
        draft_id=draft_id,
        outline_node_id=str(
            args.get("outline_node_id")
            or (draft_meta or {}).get("outline_node_id")
            or manifest_outline_ref
            or ""
        ).strip() or None,
        db=db,
    )
    outline_node = None
    for ref in (
        args.get("outline_node_id") or (draft_meta or {}).get("outline_node_id"),
        manifest_outline_ref,
        args.get("outline_node_title"),
        args.get("outline_title"),
    ):
        outline_node = find_outline_by_title_or_id(db, project_id, ref, node_type="chapter")
        if outline_node:
            break
    if not outline_node:
        return {
            "tool": "create_chapter",
            "status": "error",
            "detail": "未找到当前作品中的章节大纲，本轮未创建章节。请先选择或创建章节大纲。",
        }
    if not title:
        title = str(outline_node.title or "").strip()
    idem_key = generate_idempotency_key(
        db,
        "create_chapter",
        project_id,
        {"outline_node_id": outline_node.id},
    )
    target_key = chapter_write_target_key(project_id, outline_node_id=outline_node.id)
    if idem_key:
        existing = check_idempotency(db, project_id, idem_key)
        if existing:
            return existing
        injected_claim_id = str(args.get("_chapter_claim_id") or "").strip() or None
        injected_claim_token = str(args.get("_chapter_claim_token") or "").strip() or None
        if injected_claim_id or injected_claim_token:
            if not validate_chapter_write_claim(
                db,
                project_id=project_id,
                target_key=target_key or "",
                idempotency_key=idem_key,
                claim_id=injected_claim_id,
                claim_token=injected_claim_token,
            ):
                return {
                    "tool": "create_chapter",
                    "status": "error",
                    "detail": "章节写作占用已失效，本轮未写入正文，请重新执行任务。",
                }
            reservation = {
                "state": "acquired",
                "claim_id": injected_claim_id,
                "claim_token": injected_claim_token,
                "result": None,
            }
        else:
            reservation = acquire_chapter_write_claim(
                db,
                project_id=project_id,
                target_key=target_key or "",
                idempotency_key=idem_key,
            )
    else:
        reservation = {
            "state": "acquired", "claim_id": None, "claim_token": None, "result": None,
        }
    if reservation["state"] != "acquired":
        return reservation["result"]

    claim_id = reservation.get("claim_id")
    claim_token = reservation.get("claim_token")
    project = db.query(Project).filter(Project.id == project_id).first()
    try:
        result = await _persist_created_chapter(
            db,
            project_id,
            args,
            project=project,
            outline_node=outline_node,
            title=title,
            content=content,
            context_manifest_id=context_manifest_id,
            claim_id=claim_id,
            claim_token=claim_token,
        )
        # The "opened existing chapter" idempotent path did not change the
        # chapter and therefore must not create another cataloging job.
        if isinstance(result.get("data"), dict) and "reused_empty_chapter" in result["data"]:
            return _attach_automatic_cataloging(db, project_id, args, result)
        return result
    except asyncio.CancelledError:
        db.rollback()
        fail_chapter_write_claim(
            db, claim_id, claim_token, status="cancelled", error="章节写作已取消",
        )
        raise
    except Exception as exc:
        db.rollback()
        fail_chapter_write_claim(db, claim_id, claim_token, error=str(exc))
        raise


def _persist_updated_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    *,
    project: Project | None,
    chapter: Chapter,
    outline_node: object | None,
    new_content: str | None,
    context_manifest_id: str | None,
    rewrite: bool,
    idempotency_key: str | None,
    claim_id: str | None,
    claim_token: str | None,
) -> dict:
    _raise_if_chapter_write_cancelled(db, claim_id, claim_token)
    ensure_current_snapshot(db, chapter, "manual_save")
    previous_title = chapter.title
    previous_content = chapter.content or ""
    previous_outline_id = chapter.outline_node_id
    if args.get("title"):
        chapter.title = str(args.get("title")).strip()[:200]
    if new_content is not None:
        chapter.content = new_content
        chapter.word_count = count_words(chapter.content)
    if context_manifest_id:
        chapter.context_manifest_id = context_manifest_id
    if outline_node:
        chapter.outline_node_id = getattr(outline_node, "id")

    chapter.current_version = max(1, chapter.current_version or 1) + 1
    chapter.updated_at = datetime.utcnow()
    trigger_type = (str(args.get("trigger_type") or "ai_insert").strip() or "ai_insert")[:50]
    db.add(create_snapshot(chapter, trigger_type))
    narrative_content_changed = (
        chapter.title != previous_title
        or (chapter.content or "") != previous_content
        or chapter.outline_node_id != previous_outline_id
    )
    stale_count = 0
    if narrative_content_changed:
        stale_count = mark_governance_items_stale_for_chapter(
            db,
            project_id,
            chapter.id,
            reason=f"{chapter.title} 已更新为 v{chapter.current_version}，旧治理结论需要复检",
            actor=trigger_type,
        )
    checkpoint = create_narrative_checkpoint(
        db,
        project_id,
        chapter=chapter,
        label=f"{chapter.title} v{chapter.current_version}",
        trigger_type=trigger_type,
    )
    _save_chapter_summary(db, chapter, args)
    if "involved_characters" in args:
        db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).delete()
        _link_chapter_characters(
            db,
            project_id,
            chapter.id,
            _character_names(args.get("involved_characters")),
            f"由AI助手更新章节「{chapter.title[:50]}」",
        )
    _queue_chapter_sync(db, project_id, chapter.id, project)

    result = {
        "tool": "update_chapter",
        "status": "ok",
        "detail": (
            f"已将章节重写为新版本：{chapter.title}（{count_words(chapter.content or '')} 字）"
            if rewrite
            else f"已更新章节：{chapter.title}（{count_words(chapter.content or '')} 字）"
        ),
        "data": {
            "id": chapter.id,
            "chapter_id": chapter.id,
            "title": chapter.title,
            "word_count": count_words(chapter.content or ""),
            "current_version": chapter.current_version or 1,
            "narrative_checkpoint_id": checkpoint.id,
            "rewritten": rewrite,
            "idempotency_key": idempotency_key,
            "governance_invalidated_count": stale_count,
            "narrative_content_changed": narrative_content_changed,
        },
    }
    _raise_if_chapter_write_cancelled(db, claim_id, claim_token)
    if rewrite and not complete_chapter_write_claim(
        db, claim_id, claim_token, chapter_id=chapter.id, result=result,
    ):
        raise RuntimeError("章节重写占用已失效，已回滚本次写入")
    return result


async def update_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "update_chapter", "status": "error", "detail": "未找到章节，本轮未修改正文"}

    rewrite = bool(args.get("rewrite"))
    draft_id = str(args.get("draft_id") or args.get("content_ref") or "").strip() or None
    draft_meta = get_chapter_draft_meta(project_id, draft_id, db=db) if draft_id else None
    context_manifest_id = str(
        args.get("context_manifest_id")
        or (draft_meta or {}).get("context_manifest_id")
        or getattr(chapter, "context_manifest_id", "")
        or ""
    ).strip() or None
    external_execution = str(args.get("_context_execution_route") or "").strip() in {
        "external_mcp", "local_cli_agent",
    }
    context_ok, context_detail, context_manifest = _context_write_gate(
        db, project_id, context_manifest_id, require_manifest=external_execution,
    )
    if not context_ok:
        return {
            "tool": "update_chapter",
            "status": _context_gate_status(context_manifest),
            "detail": context_detail,
            "data": {"context_manifest_id": context_manifest_id},
        }

    outline_node = None
    requested_outline_refs = (
        args.get("outline_node_id") or (draft_meta or {}).get("outline_node_id"),
        args.get("outline_node_title"),
        args.get("outline_title"),
    )
    for ref in requested_outline_refs:
        outline_node = find_outline_by_title_or_id(
            db, project_id, ref, node_type="chapter",
        )
        if outline_node:
            break
    if not outline_node and any(str(ref or "").strip() for ref in requested_outline_refs):
        return {
            "tool": "update_chapter",
            "status": "error",
            "detail": "指定的大纲不属于当前作品或不是章节节点，本轮未修改正文。",
        }
    if not outline_node:
        outline_node = find_outline_by_title_or_id(
            db, project_id, chapter.outline_node_id, node_type="chapter",
        )

    new_content: str | None = None
    if "content" in args or draft_id:
        new_content = resolve_chapter_draft_content(
            project_id=project_id,
            provided_content=str(args.get("content") or ""),
            draft_id=draft_id,
            outline_node_id=str(args.get("outline_node_id") or "").strip() or None,
            db=db,
        )
        skip_repair = bool(args.get("skip_style_repair") or args.get("skip_forbidden_repair"))
        if project and new_content.strip() and not skip_repair:
            new_content, _violations, _remaining = await _repair_forbidden_sentence_text(
                new_content, project, str(args.get("model") or "") or None
            )
    if not rewrite and new_content is not None and not str(new_content).strip():
        return {
            "tool": "update_chapter",
            "status": "error",
            "detail": "章节正文为空，本轮未修改章节",
        }
    if rewrite and not outline_node:
        return {
            "tool": "update_chapter",
            "status": "error",
            "detail": "未找到当前作品中的章节大纲，本轮未修改正文。请先关联章节大纲。",
        }

    claim_id: str | None = None
    claim_token: str | None = None
    idempotency_key: str | None = None
    if rewrite:
        idempotency_args = {
            "rewrite": True,
            "chapter_id": None if outline_node else chapter.id,
            "outline_node_id": getattr(outline_node, "id", None),
            "rewrite_request_id": args.get("rewrite_request_id"),
            "draft_id": draft_id,
            "content_ref": args.get("content_ref"),
            "content": new_content,
            "expected_version": args.get("expected_version"),
        }
        idempotency_key = generate_idempotency_key(
            db,
            "update_chapter",
            project_id,
            idempotency_args,
        )
        if not idempotency_key:
            return {
                "tool": "update_chapter",
                "status": "error",
                "detail": "重写请求缺少稳定请求编号，本轮未修改章节",
            }
        existing = check_idempotency(db, project_id, idempotency_key)
        if existing:
            return existing
        target_key = chapter_write_target_key(
            project_id,
            outline_node_id=getattr(outline_node, "id", None),
            chapter_id=chapter.id,
        ) or ""
        injected_claim_id = str(args.get("_chapter_claim_id") or "").strip() or None
        injected_claim_token = str(args.get("_chapter_claim_token") or "").strip() or None
        if injected_claim_id or injected_claim_token:
            if not validate_chapter_write_claim(
                db,
                project_id=project_id,
                target_key=target_key,
                idempotency_key=idempotency_key,
                claim_id=injected_claim_id,
                claim_token=injected_claim_token,
            ):
                return {
                    "tool": "update_chapter",
                    "status": "error",
                    "detail": "章节重写占用已失效，本轮未修改正文，请重新执行任务。",
                }
            reservation = {
                "state": "acquired",
                "claim_id": injected_claim_id,
                "claim_token": injected_claim_token,
                "result": None,
            }
        else:
            reservation = acquire_chapter_write_claim(
                db,
                project_id=project_id,
                target_key=target_key,
                idempotency_key=idempotency_key,
            )
        if reservation["state"] != "acquired":
            return reservation["result"]
        claim_id = reservation.get("claim_id")
        claim_token = reservation.get("claim_token")

        if not str(new_content or "").strip():
            fail_chapter_write_claim(
                db,
                claim_id,
                claim_token,
                error="重写正文为空，本轮未修改章节",
            )
            return {
                "tool": "update_chapter",
                "status": "error",
                "detail": "重写正文为空，本轮未修改章节",
            }

    try:
        result = _persist_updated_chapter(
            db,
            project_id,
            args,
            project=project,
            chapter=chapter,
            outline_node=outline_node,
            new_content=new_content,
            context_manifest_id=context_manifest_id,
            rewrite=rewrite,
            idempotency_key=idempotency_key,
            claim_id=claim_id,
            claim_token=claim_token,
        )
        commit_session(db)
        if bool((result.get("data") or {}).get("narrative_content_changed")):
            return _attach_automatic_cataloging(db, project_id, args, result)
        return result
    except asyncio.CancelledError:
        db.rollback()
        if rewrite:
            fail_chapter_write_claim(
                db, claim_id, claim_token, status="cancelled", error="章节重写已取消",
            )
        raise
    except Exception as exc:
        db.rollback()
        if rewrite:
            fail_chapter_write_claim(db, claim_id, claim_token, error=str(exc))
        raise

async def list_chapter_versions(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "list_chapter_versions", "status": "skipped", "detail": "未找到章节", "data": None}
    snapshots = _chapter_snapshots(db, chapter)
    items = [snapshot_to_item(snapshot) for snapshot in snapshots]
    return {
        "tool": "list_chapter_versions",
        "status": "ok",
        "detail": f"章节「{chapter.title}」共有 {len(items)} 个版本快照，当前 v{chapter.current_version or 1}",
        "data": {
            "chapter": _chapter_version_data(chapter),
            "items": items,
            "total": len(items),
        },
    }


async def restore_chapter_version(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "restore_chapter_version", "status": "skipped", "detail": "未找到章节", "data": None}
    snapshot = _find_snapshot(db, chapter, args)
    if not snapshot:
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "没有找到可恢复的版本；请先调用 list_chapter_versions 查看可用快照",
            "data": {"chapter": _chapter_version_data(chapter), "items": [snapshot_to_item(s) for s in _chapter_snapshots(db, chapter)]},
        }
    if (snapshot.version_number or 0) >= (chapter.current_version or 1) and not (
        args.get("snapshot_id") or args.get("version_id") or args.get("version_number")
    ):
        return {
            "tool": "restore_chapter_version",
            "status": "skipped",
            "detail": "当前章节没有更早的可回退版本",
            "data": {"chapter": _chapter_version_data(chapter), "items": [snapshot_to_item(s) for s in _chapter_snapshots(db, chapter)]},
        }
    restored = restore_chapter_from_snapshot(db, chapter, snapshot)
    ledger_restore = restore_ledger_checkpoint(db, project_id, chapter, snapshot.id)
    stale_count = mark_governance_items_stale_for_chapter(
        db,
        project_id,
        chapter.id,
        reason=f"{chapter.title} 已恢复历史版本，原治理结论需要复检",
        actor="chapter_restore",
    )
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.CHAPTER,
                entity_id=chapter.id,
                source="workspace_tool",
            ),
        )
    result = {
        "tool": "restore_chapter_version",
        "status": "ok",
        "detail": f"已将「{chapter.title}」恢复到 v{snapshot.version_number}，当前记录为 v{chapter.current_version or 1}",
        "data": {
            "chapter": _chapter_version_data(chapter),
            "restored_from": snapshot_to_item(snapshot),
            "restore_snapshot": snapshot_to_item(restored),
            "content_preview": (chapter.content or "")[:500],
            "ledger_checkpoint_id": ledger_restore["ledger_checkpoint_id"],
            "ledger_restored_count": ledger_restore["restored_count"],
            "ledger_conflicts": ledger_restore["conflicts"],
            "governance_invalidated_count": stale_count,
        },
    }
    commit_session(db)
    return _attach_automatic_cataloging(db, project_id, args, result)


async def diff_chapter_versions(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    chapter = _find_chapter(db, project_id, args)
    if not chapter:
        return {"tool": "diff_chapter_versions", "status": "skipped", "detail": "未找到章节", "data": None}
    from_args = dict(args)
    to_args = dict(args)
    from_args["snapshot_id"] = args.get("from_snapshot_id") or args.get("base_snapshot_id")
    to_args["snapshot_id"] = args.get("to_snapshot_id") or args.get("target_snapshot_id")
    if not from_args["snapshot_id"]:
        from_args["version_number"] = args.get("from_version")
    if not to_args["snapshot_id"]:
        to_args["version_number"] = args.get("to_version")
    from_snapshot = _find_snapshot(db, chapter, from_args)
    to_snapshot = _find_snapshot(db, chapter, to_args)
    if not from_snapshot or not to_snapshot:
        return {
            "tool": "diff_chapter_versions",
            "status": "skipped",
            "detail": "需要两个可识别的版本；请先调用 list_chapter_versions",
            "data": {"chapter": _chapter_version_data(chapter), "items": [snapshot_to_item(s) for s in _chapter_snapshots(db, chapter)]},
        }
    return {
        "tool": "diff_chapter_versions",
        "status": "ok",
        "detail": f"已对比 v{from_snapshot.version_number} 与 v{to_snapshot.version_number}",
        "data": diff_snapshots(from_snapshot, to_snapshot),
    }


async def delete_chapter(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    project = db.query(Project).filter(Project.id == project_id).first()
    chapter = None
    for ref in (args.get("id"), args.get("chapter_id")):
        text = str(ref or "").strip()
        if text:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id, Chapter.id == text
            ).first()
            if chapter:
                break
    if not chapter:
        title_ref = str(args.get("title") or args.get("chapter_title") or "").strip()
        if title_ref:
            chapter = db.query(Chapter).filter(
                Chapter.project_id == project_id, Chapter.title == title_ref
            ).first()
    if not chapter:
        return {"tool": "delete_chapter", "status": "skipped", "detail": "未找到章节"}

    title = chapter.title
    content_file_path = chapter.content_file_path

    # Revert character changes introduced in this chapter
    change_logs = db.query(CharacterChangeLog).filter(
        CharacterChangeLog.chapter_id == chapter.id, CharacterChangeLog.confirmed == True
    ).all()
    reverted: list[str] = []
    for log_entry in change_logs:
        character = db.query(Character).filter(Character.id == log_entry.character_id).first()
        if character and log_entry.field_name in ("abilities", "personality", "background", "appearance"):
            old_val = log_entry.old_value
            if old_val and old_val != "（档案中无记录）":
                setattr(character, log_entry.field_name, old_val)
                reverted.append(character.name)
    if reverted:
        db.flush()

    db.query(CharacterChangeLog).filter(CharacterChangeLog.chapter_id == chapter.id).delete()
    db.query(CharacterTimeline).filter(CharacterTimeline.chapter_id == chapter.id).delete()
    db.query(ChapterCharacter).filter(ChapterCharacter.chapter_id == chapter.id).delete()
    db.query(ChapterSummary).filter(ChapterSummary.chapter_id == chapter.id).delete()
    db.delete(chapter)
    if project:
        queue_content_sync(
            db,
            ContentSyncIntent(
                project_id=project_id,
                target=ContentSyncTarget.FILE_DELETE,
                entity_id=chapter.id,
                payload={
                    "folder_path": project.folder_path,
                    "relative_path": content_file_path,
                },
                source="workspace_tool",
            ),
        )

    detail = f"已删除章节：{title}"
    if reverted:
        detail += f"，已回退 {len(reverted)} 个角色的状态（{', '.join(reverted)}）"
    return {"tool": "delete_chapter", "status": "ok", "detail": detail}
