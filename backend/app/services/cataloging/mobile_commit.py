"""Replay a completed mobile plan through the sole PC cataloging applier.

The client supplies no derived domain records or completion flag. IDs, exact
chapter version/content, archive preconditions and the complete plan are checked
before the same chapter transaction used by the native worker is applied.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from ...architecture.uow import commit_session
from ...database.models import CatalogingChapterRun, CatalogingJob, Chapter
from ...modules.continuity.domain.mobile_cataloging import MOBILE_CATALOGING_GUARD_FIELDS
from ...schemas.cataloging import MobileCatalogingCommit
from ..gateway_legacy_replication import domain_snapshot_for_entity
from ..operation_runtime import ensure_operation
from .applier import apply_candidates_for_run
from .candidate_store import create_candidate_from_raw
from .constants import JOB_RUNNING_STATUSES
from .job_control import complete_cataloging_job, refresh_job_progress
from .launcher import _serialized_cataloging_launch
from .plan_validation import validate_complete_plan


def _fingerprint(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _normalized(value):
    # Public projections may represent an unset optional text/object as null.
    # Treat only empty representations as equal, never coerce actual values.
    if isinstance(value, dict):
        value = {
            key: normalized
            for key, item in value.items()
            if (normalized := _normalized(item)) is not None
        }
    return None if value in (None, "", [], {}) else value


def _check_guards(db: Session, project_id: str, payload: MobileCatalogingCommit) -> None:
    guards = {}
    for guard in payload.archive_guards:
        entity_type, identity, fields = (
            guard.get("entity_type"),
            guard.get("id"),
            guard.get("fields"),
        )
        record_type = guard.get("record_type")
        required = MOBILE_CATALOGING_GUARD_FIELDS.get(record_type)
        if (
            entity_type
            not in {
                "character",
                "world",
                "outline",
                "character_relation",
                "foreshadowing",
                "governance",
            }
            or not isinstance(fields, dict)
            or not required
            or set(fields) != set(required)
        ):
            raise ValueError("无效的建档档案校验快照")
        actual = domain_snapshot_for_entity(
            db, project_id=project_id, entity_type=entity_type, entity_id=identity
        )
        if actual is None or actual.get("_record_type") != record_type:
            raise ValueError(f"建档来源档案 {identity} 不存在或不属于本作品")
        if record_type == "character":
            from ...database.models import Character
            from .snapshots import character_snapshot

            actual["ai_config"] = character_snapshot(db.get(Character, identity))["ai_config"]
        for field, expected in fields.items():
            if _normalized(actual.get(field)) != _normalized(expected):
                raise ValueError(
                    f"档案 {identity} 的 {field} 在手机建档后已变化；"
                    "未覆盖 PC 资料，请先处理同步冲突"
                )
        guards[entity_type, identity] = fields
    plan = next((row for row in payload.candidates if row.get("type") == "chapter_summary"), {})
    for field, entity_type in (
        ("character_bindings", "character"),
        ("worldbuilding_bindings", "world"),
    ):
        for binding in plan.get(field, []):
            if binding.get("decision") == "existing" and not guards.get(
                (entity_type, binding.get("id"))
            ):
                raise ValueError("手机建档回放缺少已有实体的来源快照")
    for candidate in payload.candidates:
        if candidate.get("type") in {"outline_create", "outline_update"}:
            for field in ("id", "parent_id"):
                identity = candidate.get(field)
                if identity and not guards.get(("outline", identity)):
                    raise ValueError("手机建档回放缺少大纲目标或父级的来源快照")


@_serialized_cataloging_launch
def commit_mobile_cataloging(db: Session, project_id: str, payload: MobileCatalogingCommit) -> dict:
    job_id = str(
        uuid5(NAMESPACE_URL, f"siming.mobile.cataloging:{project_id}:{payload.request_id}")
    )
    digest = _fingerprint(payload.model_dump())
    job = db.get(CatalogingJob, job_id)
    run = db.query(CatalogingChapterRun).filter_by(job_id=job_id).first() if job else None
    if job:
        if run is None or json.loads(run.raw_output or "{}").get("mobile_request_sha256") != digest:
            raise ValueError("手机建档请求键已用于不同内容")
        if job.status == "completed":
            return {"job_id": job.id, "status": "completed", "replayed": True}
        if job.status not in {"failed", "cancelled"}:
            raise ValueError(job.error or "该手机建档回执未完成，请先处理原任务")
    chapter = db.query(Chapter).filter_by(id=payload.chapter_id, project_id=project_id).first()
    if chapter is None:
        raise ValueError("建档章节不存在或不属于本作品")
    if (
        db.query(CatalogingJob)
        .filter(
            CatalogingJob.project_id == project_id, CatalogingJob.status.in_(JOB_RUNNING_STATUSES)
        )
        .first()
    ):
        raise ValueError("作品已有未结束的 PC 建档任务，请处理后同步手机建档结果")
    if job is None:
        job = CatalogingJob(
            id=job_id,
            project_id=project_id,
            status="running",
            execution_mode="auto",
            execution_backend="mobile_direct_api",
            model=payload.model,
            model_source="mobile_direct_api",
            provider="android_direct_api",
            total_chapters=1,
            current_chapter_id=chapter.id,
        )
        db.add(job)
    job.status, job.error, job.completed_at = "running", None, None
    db.flush()
    operation = ensure_operation(
        db,
        source_kind="cataloging",
        source_id=job.id,
        project_id=project_id,
        title=f"手机建档同步 · {chapter.title}",
        status="running",
        phase="cataloging",
        message="正在校验手机已完成的建档计划",
        tool_mode="mobile_direct_api",
        can_pause=False,
        can_cancel=False,
        can_retry=False,
        progress_total=1,
        progress_current=0,
    )
    job.operation_id = operation.id
    if run is None:
        run = CatalogingChapterRun(
            job_id=job.id,
            project_id=project_id,
            chapter_id=chapter.id,
            chapter_version=payload.chapter_version,
            status="pending",
            raw_output=json.dumps(
                {"mobile_request_sha256": digest, "request_id": payload.request_id},
                ensure_ascii=False,
            ),
        )
        db.add(run)
    run.status, run.error, run.completed_at = "pending", None, None
    db.flush()
    try:
        with db.begin_nested():
            if (
                chapter.current_version != payload.chapter_version
                or hashlib.sha256((chapter.content or "").encode()).hexdigest()
                != payload.content_sha256
            ):
                raise ValueError("章节正文或版本已变化；手机建档回执不能覆盖当前版本")
            _check_guards(db, project_id, payload)
            for index, raw in enumerate(payload.candidates):
                result = create_candidate_from_raw(
                    db, job, run, raw, index, source_task="mobile_cataloging"
                )
                if result.get("error") or result.get("skipped"):
                    raise ValueError(
                        result.get("error") or result.get("reason") or "手机建档候选未通过校验"
                    )
            validate_complete_plan(db, run)
            events = apply_candidates_for_run(db, job, run)
            failures = [
                event.get("error")
                for event in events
                if event.get("type") == "candidate_apply_failed"
            ]
            if failures:
                raise ValueError("；".join(str(error) for error in failures))
            run.status = "completed"
            run.completed_at = datetime.utcnow()
            job.last_completed_chapter_id = chapter.id
            complete_cataloging_job(db, job)
            from ...modules.story.application.content_sync import enqueue_project_sync

            enqueue_project_sync(db, project_id, source="mobile_cataloging")
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = datetime.utcnow()
        run.status = "failed"
        run.error = str(exc)
        refresh_job_progress(db, job)
        commit_session(db)
        raise ValueError(f"手机建档同步未通过校验：{exc}") from exc
    commit_session(db)
    return {"job_id": job.id, "status": "completed", "replayed": False}
