"""Workspace tools for the resumable novel creation workbench."""
from __future__ import annotations

import asyncio
import re
import time
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ....core.json_repair import parse_json_object_detailed
from ....core.model_limits import MAX_CONFIGURABLE_LIMIT, default_output_token_limit
from ....database.models import (
    NovelCreationMaterialImport,
    NovelCreationSession,
    NovelCreationStageRun,
    OperationRun,
)
from ....modules.model_runtime.application.execution import model_executor as LLMGateway
from ....modules.operations.interfaces.dependencies import get_operation_service
from ....services.context_orchestrator import activate_context_manifest
from ....services.novel_creation_actions import (
    delete_creation_entity as delete_creation_entity_record,
)
from ....services.novel_creation_actions import (
    patch_creation_entity as patch_creation_entity_record,
)
from ....services.novel_creation_actions import (
    restore_artifact_version as restore_creation_artifact_version_record,
)
from ....services.novel_creation_authoring import (
    _WORLD_STYLE_TEXT_FIELDS,
    _author_context,
    _author_text,
    _dict_rows,
    _looks_like_cli_metadata,
    _opening_outline_chapter_count,
    _stage_contract,
    _validate_compact_concepts,
    _validate_stage,
)
from ....services.novel_creation_consistency import (
    creation_dependency_graph,
    validate_creation_consistency,
)
from ....services.novel_creation_context_projection import (
    build_stage_generation_context,
    compact_creation_snapshot,
    project_creation_artifact,
)
from ....services.novel_creation_contract import OPENING_OUTLINE_CHAPTER_COUNT
from ....services.novel_creation_entities import (
    _extract_records,
    query_creation_entities,
    serialize_creation_entity,
)
from ....services.novel_creation_entities import (
    get_creation_entity as get_creation_entity_record,
)
from ....services.novel_creation_entity_normalization import (
    normalize_characters as _normalize_characters,
)
from ....services.novel_creation_entity_normalization import (
    normalize_locations as _normalize_locations,
)
from ....services.novel_creation_imports import (
    apply_material_import,
    create_material_import,
    run_material_import,
    serialize_material_import,
)
from ....services.novel_creation_prompting import (
    CREATION_REPAIR_SYSTEM_PROMPT,
    CREATION_REPAIR_USER_TEMPLATE,
    build_compact_concept_messages,
    build_creation_stage_messages,
)
from ....services.novel_creation_submission import save_creation_stage_data
from ....services.novel_creation_versions import (
    artifact_version_diff,
    get_artifact_version,
    list_artifact_versions,
    serialize_artifact_version,
)
from ....services.operation_runtime import register_operation_actions
from ...novel_creation_workspace import (
    STAGE_LABELS,
    confirm_run,
    creation_artifact_dependencies,
    patch_creation_artifact,
    patch_session,
    serialize_creation_artifact,
    set_creation_artifact_locks,
    undo_creation_artifact,
)
from ...operation_runtime import current_operation_id, record_operation_signal

STREAM_PROGRESS_INTERVAL_SECONDS = 0.2
STREAM_PROGRESS_PREVIEW_CHARS = 320


def _text(value: Any) -> str:
    return str(value or "").strip()


class StageModelResponseError(RuntimeError):
    """Carries model-attempt metadata into the deterministic fallback path."""

    def __init__(self, message: str, *, attempt: int = 1) -> None:
        super().__init__(message)
        self.attempt = max(1, int(attempt))


def _repair_provenance(raw: str, method: str, warning: str) -> dict[str, Any]:
    return {
        "result_mode": "repaired",
        "warning": warning,
        "repair_method": method,
        "original_response_excerpt": raw[:12_000],
        "_diagnostic_raw": raw,
    }


def _raise_if_task_cancelled() -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


def _ensure_stage_not_cancelled(
    db: Session,
    run: NovelCreationStageRun | None,
) -> None:
    """Fence every model/save boundary against both task and durable cancellation."""
    _raise_if_task_cancelled()
    if run is None:
        return
    db.refresh(run)
    if run.status in {"cancelled", "paused"}:
        raise asyncio.CancelledError
    if run.operation_id:
        operation = (
            db.query(OperationRun)
            .filter(OperationRun.id == run.operation_id)
            .populate_existing()
            .first()
        )
        if operation is not None and operation.status == "cancelled":
            raise asyncio.CancelledError


def _normalize_worldbuilding(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [deepcopy(item) for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key, child in value.items():
        if isinstance(child, dict):
            item = deepcopy(child)
            item.setdefault("title", _text(key))
            item.setdefault("dimension", _text(key))
            if not _text(item.get("content")):
                item["content"] = _author_text(item.get("summary") or item.get("description") or child)
        else:
            item = {"title": _text(key), "dimension": _text(key), "content": _author_text(child)}
        rows.append(item)
    return rows


def _chapter_range(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None, None
    numbers = re.findall(r"\d+", _text(value))
    if len(numbers) >= 2:
        return int(numbers[0]), int(numbers[1])
    return None, None


def _normalize_macro_outline(data: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    normalized = {**deepcopy(baseline), **deepcopy(data)}
    source_volumes = _dict_rows(data.get("volumes"), name_field="title")
    base_volumes = _dict_rows(baseline.get("volumes"), name_field="title")
    if not source_volumes:
        source_volumes = deepcopy(base_volumes)
    volumes: list[dict[str, Any]] = []
    for index, source in enumerate(source_volumes):
        base = base_volumes[index] if index < len(base_volumes) else {}
        item = {**deepcopy(base), **deepcopy(source)}
        parsed_start, parsed_end = _chapter_range(item.get("chapters") or item.get("range"))
        start = item.get("start_chapter") or parsed_start or base.get("start_chapter")
        end = item.get("end_chapter") or parsed_end or base.get("end_chapter")
        try:
            item["start_chapter"] = int(start)
            item["end_chapter"] = int(end)
        except (TypeError, ValueError):
            item["start_chapter"] = 0
            item["end_chapter"] = 0
        item["summary"] = _text(item.get("summary") or item.get("core_function") or item.get("focus") or item.get("climax") or base.get("summary"))
        item["title"] = _text(item.get("title")) or f"第{index + 1}卷"
        volumes.append(item)
    normalized["volumes"] = volumes
    normalized["stage_plan"] = _dict_rows(normalized.get("stage_plan"), name_field="name") or [
        {
            "name": item["title"],
            "range": [item["start_chapter"], item["end_chapter"]],
            "promise": item["summary"],
        }
        for item in volumes
    ]
    return normalized


def _chapter_number(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        numbers = re.findall(r"\d+", _text(value))
        return int(numbers[0]) if numbers else fallback


def _normalize_section(
    section: dict[str, Any],
    base: dict[str, Any],
    *,
    chapter_id: str,
    chapter_number: int,
    scene_number: int,
) -> dict[str, Any]:
    item = {**deepcopy(base), **deepcopy(section)}
    item["client_id"] = _text(item.get("client_id")) or f"{chapter_id}-section-{scene_number}"
    item["parent_client_id"] = chapter_id
    item["node_type"] = "section"
    item["sort_order"] = _chapter_number(item.get("sort_order"), scene_number)
    item["title"] = _text(item.get("title")) or f"第{chapter_number}章 · 场景{scene_number}"
    item["summary"] = _text(item.get("summary") or item.get("planned_summary") or item.get("purpose"))
    item["planned_summary"] = _text(item.get("planned_summary") or item.get("summary"))
    base_metadata = base.get("metadata") if isinstance(base.get("metadata"), dict) else {}
    source_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata = {**deepcopy(base_metadata), **deepcopy(source_metadata)}
    metadata["scene_number"] = _chapter_number(metadata.get("scene_number"), scene_number)
    metadata["purpose"] = _text(metadata.get("purpose") or item.get("purpose") or item.get("summary")) or "推进本章目标"
    metadata["location"] = _text(metadata.get("location")) or "地点待定"
    metadata["timeline"] = _text(metadata.get("timeline")) or f"第{chapter_number}章第{scene_number}场"
    metadata["pov_character"] = _text(metadata.get("pov_character")) or "主角"
    metadata["characters"] = metadata.get("characters") if isinstance(metadata.get("characters"), list) else [metadata["pov_character"]]
    metadata["entry_state"] = _text(metadata.get("entry_state")) or "承接上一场景"
    metadata["exit_state"] = _text(metadata.get("exit_state")) or "产生新的行动压力"
    metadata["emotional_residue"] = _text(metadata.get("emotional_residue")) or "情绪推动下一场景"
    metadata["unresolved_actions"] = metadata.get("unresolved_actions") if isinstance(metadata.get("unresolved_actions"), list) else ["追踪本场景产生的新问题"]
    item["metadata"] = metadata
    return item


def _normalize_opening_outline(data: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    source_chapters = _dict_rows(data.get("chapters"), name_field="title")
    base_chapters = _dict_rows(baseline.get("chapters"), name_field="title")
    if base_chapters:
        source_chapters = (source_chapters + [{} for _ in range(len(base_chapters))])[:len(base_chapters)]
    chapters: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    top_sections = _dict_rows(data.get("sections"), name_field="title")
    base_sections = _dict_rows(baseline.get("sections"), name_field="title")
    for index, source in enumerate(source_chapters):
        base = base_chapters[index] if index < len(base_chapters) else {}
        original_id = _text(source.get("client_id"))
        chapter_number = _chapter_number(source.get("chapter_number") or source.get("chapter") or source.get("number"), index + 1)
        chapter_id = original_id or _text(base.get("client_id")) or f"chapter-{chapter_number:02d}"
        chapter = {**deepcopy(base), **deepcopy(source)}
        nested_sections = _dict_rows(chapter.pop("sections", None), name_field="title")
        chapter["client_id"] = chapter_id
        chapter["chapter_number"] = chapter_number
        chapter["node_type"] = "chapter"
        chapter["sort_order"] = _chapter_number(chapter.get("sort_order"), chapter_number)
        chapter["title"] = _text(chapter.get("title")) or f"第{chapter_number}章 未命名事件"
        chapter["summary"] = _text(chapter.get("summary") or chapter.get("planned_summary") or chapter.get("beat"))
        chapter["planned_summary"] = _text(chapter.get("planned_summary") or chapter.get("summary"))
        chapters.append(chapter)

        chapter_aliases = {chapter_id, str(chapter_number), f"chapter-{chapter_number:02d}"}
        if original_id:
            chapter_aliases.add(original_id)
        matching = nested_sections or [
            item for item in top_sections
            if _text(item.get("parent_client_id")) in chapter_aliases
        ]
        base_chapter_id = _text(base.get("client_id")) or chapter_id
        fallback_sections = [item for item in base_sections if _text(item.get("parent_client_id")) == base_chapter_id]
        if len(matching) not in range(2, 7) and fallback_sections:
            matching = fallback_sections
        for scene_index, raw_section in enumerate(matching[:6], start=1):
            base_section = fallback_sections[scene_index - 1] if scene_index <= len(fallback_sections) else {}
            sections.append(_normalize_section(
                raw_section,
                base_section,
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                scene_number=scene_index,
            ))
    return {
        **deepcopy(baseline),
        **deepcopy(data),
        "opening_chapter_count": len(chapters),
        "chapters": chapters,
        "sections": sections,
        "section_rule": "每章2至6个场景事件",
    }


def _normalize_stage_data(stage: str, data: dict[str, Any], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    base = deepcopy(baseline) if isinstance(baseline, dict) else {}
    source = {} if _looks_like_cli_metadata(data) else deepcopy(data)
    normalized = {**base, **source}
    if stage == "world_style":
        for field in _WORLD_STYLE_TEXT_FIELDS:
            normalized[field] = _author_text(normalized.get(field))
        normalized["worldbuilding"] = _normalize_worldbuilding(normalized.get("worldbuilding"))
    elif stage == "characters":
        normalized = _normalize_characters(source, base)
    elif stage == "locations":
        normalized = _normalize_locations(source, base)
    elif stage == "macro_outline":
        normalized = _normalize_macro_outline(source, base)
    elif stage == "opening_outline":
        normalized = _normalize_opening_outline(source, base)
    return normalized


def _session(db: Session, session_id: str) -> NovelCreationSession | None:
    return db.query(NovelCreationSession).filter(NovelCreationSession.id == session_id).first()


def _creation_output_token_limit(model: str, context_manifest: Any | None) -> int:
    """Use the governed model budget without imposing a stage-specific cap."""

    manifest_limit = int(getattr(context_manifest, "output_reserve_tokens", 0) or 0)
    if manifest_limit > 0:
        return min(manifest_limit, MAX_CONFIGURABLE_LIMIT)
    raw_model = _text(model)
    provider, separator, model_name = raw_model.partition(":")
    if not separator:
        provider, model_name = "", raw_model
    return min(
        default_output_token_limit(provider, model_name),
        MAX_CONFIGURABLE_LIMIT,
    )


async def _stream_model_text(
    *,
    messages: list[dict[str, Any]],
    model: str,
    temperature: float,
    max_tokens: int,
    extra_body: dict[str, Any] | None,
) -> tuple[str, int]:
    operation_id = current_operation_id()
    chunks: list[str] = []
    emitted_chars = 0
    last_report_at = 0.0
    last_reported_chars = 0
    preview = ""
    resume_count = 0

    def report_progress(*, force: bool = False) -> None:
        nonlocal last_report_at, last_reported_chars
        if not operation_id or emitted_chars <= 0:
            return
        now = time.monotonic()
        if not force and now - last_report_at < STREAM_PROGRESS_INTERVAL_SECONDS:
            return
        if emitted_chars == last_reported_chars:
            return
        last_report_at = now
        last_reported_chars = emitted_chars
        readable_preview = re.sub(r"\s+", " ", preview).strip()
        record_operation_signal(
            operation_id,
            "stream_output",
            {
                "kind": "model_output",
                "output_chars": emitted_chars,
                "output_preview": readable_preview,
                "max_output_tokens": max_tokens,
                "attempt": resume_count + 1,
            },
            message=f"模型正在生成并校验立项内容 · 已输出 {emitted_chars:,} 字",
        )

    async def record_resume(payload: dict[str, Any]) -> None:
        nonlocal resume_count
        resume_count = max(resume_count, int(payload.get("resume_attempt") or 0))

    _raise_if_task_cancelled()
    generator = LLMGateway.stream_chat_completion(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=300,
        retry=1,
        extra_body=extra_body,
        resume=8,
        on_resume=record_resume,
    )
    async for chunk in generator:
        _raise_if_task_cancelled()
        chunks.append(chunk)
        emitted_chars += len(chunk)
        preview = (preview + chunk)[-STREAM_PROGRESS_PREVIEW_CHARS:]
        report_progress()
    _raise_if_task_cancelled()
    report_progress(force=True)
    return "".join(chunks), resume_count + 1


async def _repair_json_with_model(
    *,
    raw: str,
    error: Exception,
    model: str,
    contract: str,
    max_tokens: int,
    extra_body: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, int, str | None]:
    _raise_if_task_cancelled()
    system = CREATION_REPAIR_SYSTEM_PROMPT
    user = CREATION_REPAIR_USER_TEMPLATE.format(
        contract=contract,
        error=str(error)[:1000],
        raw=raw[:120_000],
    )
    repaired, attempt = await _stream_model_text(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model,
        temperature=0,
        max_tokens=max_tokens,
        extra_body=extra_body,
    )
    _raise_if_task_cancelled()
    parsed, method = parse_json_object_detailed(repaired)
    return parsed, attempt, method


async def _generate_compact_concepts(
    session: NovelCreationSession,
    model: str,
    *,
    context_manifest: Any | None = None,
    input_snapshot: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generate one or more decision-ready concept seeds for the current stage."""
    draft = deepcopy(input_snapshot) if isinstance(input_snapshot, dict) else (session.draft_json if isinstance(session.draft_json, dict) else {})
    interview = draft.get("interview") if isinstance(draft.get("interview"), dict) else {}
    author = _author_context(draft)
    author_led = author["creation_mode"] == "author_led"
    instruction = _text(draft.get("_refinement_instruction"))
    context = {
        "brief": _text(session.user_brief),
        "form": draft.get("form") or {},
        "author_source": author,
        "current_stage_data": ((draft.get("stages") or {}).get("concepts") or {}).get("data"),
        "interview_history": interview.get("history") or [],
        "interview_reason": _text(interview.get("reason")),
        "refinement_instruction": instruction,
        "entity_target": draft.get("_entity_target"),
    }
    messages = build_compact_concept_messages(author_led=author_led, context=context)
    from ....services.content_store import content_root

    with activate_context_manifest(context_manifest) if context_manifest else nullcontext():
        _raise_if_task_cancelled()
        raw, attempt = await _stream_model_text(
            messages=messages,
            model=model,
            temperature=0.8,
            max_tokens=3200,
            extra_body=LLMGateway.local_cli_extra_body(
                model,
                cwd=str(content_root()),
                base={
                    "moshu_task_type": "planning",
                    "storage_target": "session_draft",
                    "local_cli_retry_attempts": 1,
                    "moshu_context_manifest_rendered": True,
                },
            ),
        )
    _raise_if_task_cancelled()
    try:
        if not raw:
            raise ValueError("模型没有返回轻量创意卡")
        parsed, parse_method = parse_json_object_detailed(raw)
        if not isinstance(parsed, dict):
            raise ValueError("模型返回的轻量创意卡不是有效 JSON")
        payload = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        cards = _validate_compact_concepts(payload.get("concepts"))
        metadata = {"attempt": attempt, "result_mode": "model", "warning": None}
        if parse_method != "direct":
            metadata.update(_repair_provenance(
                raw,
                "deterministic_json",
                "模型原始回复存在 JSON 语法问题，系统已确定性修复并保留可识别内容",
            ))
        return cards, metadata
    except Exception as parse_error:
        try:
            repaired, repair_attempt, repair_parse_method = await _repair_json_with_model(
                raw=raw,
                error=parse_error,
                model=model,
                contract="顶层 concepts 必须是非空数组，每张卡的字段与示例一致，不得为了满足数量而复制方案",
                max_tokens=3200,
                extra_body=LLMGateway.local_cli_extra_body(
                    model,
                    cwd=str(content_root()),
                    base={
                        "moshu_task_type": "planning",
                        "storage_target": "session_draft",
                        "local_cli_retry_attempts": 1,
                    },
                ),
            )
            if not isinstance(repaired, dict):
                raise ValueError("结构修复没有返回 JSON 对象")
            payload = repaired.get("data") if isinstance(repaired.get("data"), dict) else repaired
            cards = _validate_compact_concepts(payload.get("concepts"))
            _raise_if_task_cancelled()
            metadata = {
                "attempt": attempt + repair_attempt,
                **_repair_provenance(raw, "model_json", "模型原始回复格式不合法，已使用同一模型完成一次结构修复"),
            }
            if repair_parse_method not in {None, "direct"}:
                metadata["repair_method"] = "model_json+deterministic_json"
            return cards, metadata
        except Exception as repair_error:
            raise StageModelResponseError(
                f"{parse_error}；同模型结构修复失败：{repair_error}",
                attempt=attempt + 1,
            ) from repair_error


def _validate_generated_entity(
    stage: str,
    data: dict[str, Any],
    target: dict[str, Any] | None,
) -> None:
    if not target:
        return
    target_type = _text(target.get("entity_type"))
    candidates = [
        record for record in _extract_records(stage, data)
        if record["entity_type"] == target_type
    ]
    if not candidates:
        raise ValueError(f"模型没有在阶段集合中返回可用的 {target_type} 实体；不能用旧资料代替生成结果")
    if target.get("mode") == "existing" and len(candidates) != 1:
        raise ValueError("指定实体修订必须恰好返回一个目标对象")


async def _enhance_with_model(
    session: NovelCreationSession,
    stage: str,
    baseline: dict[str, Any],
    model: str,
    *,
    context_manifest: Any | None = None,
    input_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    draft = (
        deepcopy(input_snapshot)
        if isinstance(input_snapshot, dict)
        else (session.draft_json if isinstance(session.draft_json, dict) else {})
    )
    context, entity_target = build_stage_generation_context(draft, baseline)
    instruction = _text(context.get("refinement_instruction"))
    opening_chapter_count = _opening_outline_chapter_count(baseline) if stage == "opening_outline" else None
    stage_contract = _stage_contract(
        stage,
        opening_chapter_count=opening_chapter_count or OPENING_OUTLINE_CHAPTER_COUNT,
    )
    messages = build_creation_stage_messages(
        stage=stage,
        stage_label=STAGE_LABELS.get(stage, stage),
        stage_contract=stage_contract,
        context=context,
        instruction=instruction,
    )
    from ....services.content_store import content_root

    max_output_tokens = _creation_output_token_limit(model, context_manifest)

    with activate_context_manifest(context_manifest) if context_manifest else nullcontext():
        _raise_if_task_cancelled()
        raw, attempt = await _stream_model_text(
            messages=messages,
            model=model,
            temperature=0.65,
            max_tokens=max_output_tokens,
            extra_body=LLMGateway.local_cli_extra_body(
                model,
                cwd=str(content_root()),
                base={
                    "moshu_task_type": "planning",
                    "storage_target": "session_draft",
                    "local_cli_retry_attempts": 1,
                    "moshu_context_manifest_rendered": True,
                },
            ),
        )
    _raise_if_task_cancelled()
    try:
        if not raw:
            raise ValueError("没有收到模型的文字回复")
        parsed, parse_method = parse_json_object_detailed(raw)
        if not isinstance(parsed, dict):
            raise ValueError("模型返回的阶段 JSON 格式不合法")
        data = parsed.get("data") if isinstance(parsed.get("data"), dict) else parsed
        _validate_generated_entity(stage, data, entity_target)
        data = _normalize_stage_data(stage, data, baseline)
        if not entity_target:
            _validate_stage(stage, data)
        metadata = {"attempt": attempt, "result_mode": "model", "warning": None}
        if parse_method != "direct":
            metadata.update(_repair_provenance(
                raw,
                "deterministic_json",
                "模型原始回复存在 JSON 语法问题，系统已确定性修复并保留可识别内容",
            ))
        return data, metadata
    except Exception as parse_error:
        try:
            repaired, repair_attempt, repair_parse_method = await _repair_json_with_model(
                raw=raw,
                error=parse_error,
                model=model,
                contract=stage_contract,
                max_tokens=max_output_tokens,
                extra_body=LLMGateway.local_cli_extra_body(
                    model,
                    cwd=str(content_root()),
                    base={
                        "moshu_task_type": "planning",
                        "storage_target": "session_draft",
                        "local_cli_retry_attempts": 1,
                    },
                ),
            )
            if not isinstance(repaired, dict):
                raise ValueError("结构修复没有返回 JSON 对象")
            data = repaired.get("data") if isinstance(repaired.get("data"), dict) else repaired
            _raise_if_task_cancelled()
            _validate_generated_entity(stage, data, entity_target)
            data = _normalize_stage_data(stage, data, baseline)
            if not entity_target:
                _validate_stage(stage, data)
            metadata = {
                "attempt": attempt + repair_attempt,
                **_repair_provenance(raw, "model_json", "模型原始回复格式不合法，已使用同一模型完成一次结构修复"),
            }
            if repair_parse_method not in {None, "direct"}:
                metadata["repair_method"] = "model_json+deterministic_json"
            return data, metadata
        except Exception as repair_error:
            raise StageModelResponseError(
                f"{parse_error}；同模型结构修复失败：{repair_error}",
                attempt=attempt + 1,
            ) from repair_error


async def get_creation_session(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session_id = _text(args.get("session_id"))
    session = _session(db, session_id)
    if not session:
        return {"tool": "get_creation_session", "status": "skipped", "detail": "Session not found", "data": None}
    return {
        "tool": "get_creation_session",
        "status": "ok",
        "detail": "Novel creation session overview loaded",
        "data": compact_creation_snapshot(session),
    }


async def get_creation_snapshot(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "get_creation_snapshot", "status": "skipped", "detail": "Session not found", "data": None}
    return {
        "tool": "get_creation_snapshot",
        "status": "ok",
        "detail": "Creation snapshot loaded",
        "data": compact_creation_snapshot(session),
    }


async def get_creation_operation(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    operation_id = _text(args.get("operation_id"))
    if not operation_id and _text(args.get("run_id")):
        run = db.query(NovelCreationStageRun).filter(NovelCreationStageRun.id == _text(args.get("run_id"))).first()
        operation_id = _text(getattr(run, "operation_id", ""))
    operation = get_operation_service().get(operation_id, include_events=True) if operation_id else None
    if not operation:
        return {"tool": "get_creation_operation", "status": "skipped", "detail": "Operation not found", "data": None}
    return {"tool": "get_creation_operation", "status": "ok", "detail": "Creation operation loaded", "data": operation}


async def patch_creation_session_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "patch_creation_session", "status": "skipped", "detail": "Session not found", "data": None}
    if args.get("expected_revision") is None or int(args["expected_revision"]) != int(session.revision or 0):
        return _revision_error("patch_creation_session", session)
    changes = args.get("changes") if isinstance(args.get("changes"), dict) else {}
    try:
        patch_session(session, changes, source="assistant")
        commit_session(db)
        return {
            "tool": "patch_creation_session",
            "status": "ok",
            "detail": "Creation session patched",
            "data": {
                "session_id": session.id,
                "revision": int(session.revision or 0),
                "status": session.status,
                "current_stage": session.current_stage,
                "changed_fields": sorted(str(key) for key in changes),
            },
        }
    except Exception as exc:
        db.rollback()
        return {"tool": "patch_creation_session", "status": "error", "detail": str(exc), "data": None}


async def get_creation_artifact(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    stage = _text(args.get("artifact"))
    if not session:
        return {"tool": "get_creation_artifact", "status": "skipped", "detail": "Session not found", "data": None}
    try:
        return {
            "tool": "get_creation_artifact",
            "status": "ok",
            "detail": "Artifact loaded",
            "data": project_creation_artifact(session, stage),
        }
    except ValueError as exc:
        return {"tool": "get_creation_artifact", "status": "error", "detail": str(exc), "data": None}


async def list_creation_artifacts_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "list_creation_artifacts", "status": "skipped", "detail": "Session not found", "data": None}
    return {
        "tool": "list_creation_artifacts",
        "status": "ok",
        "detail": "Creation artifacts loaded",
        "data": compact_creation_snapshot(session),
    }


async def get_creation_dependencies(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "get_creation_dependencies", "status": "skipped", "detail": "Session not found", "data": None}
    try:
        return {
            "tool": "get_creation_dependencies",
            "status": "ok",
            "detail": "Artifact dependencies loaded",
            "data": creation_artifact_dependencies(session, _text(args.get("artifact"))),
        }
    except ValueError as exc:
        return {"tool": "get_creation_dependencies", "status": "error", "detail": str(exc), "data": None}


async def get_creation_dependency_graph_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "get_creation_dependency_graph", "status": "skipped", "detail": "Session not found", "data": None}
    data = creation_dependency_graph(session)
    commit_session(db)
    return {"tool": "get_creation_dependency_graph", "status": "ok", "detail": "Dependency graph loaded", "data": data}


async def validate_creation_consistency_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "validate_creation_consistency", "status": "skipped", "detail": "Session not found", "data": None}
    data = validate_creation_consistency(session)
    commit_session(db)
    return {
        "tool": "validate_creation_consistency",
        "status": "ok" if data["valid"] else "warning",
        "detail": "Creation data is consistent" if data["valid"] else "Creation data needs attention",
        "data": data,
    }


def _revision_error(tool: str, session: NovelCreationSession) -> dict[str, Any]:
    return {
        "tool": tool,
        "status": "error",
        "detail": "Novel creation session revision conflict",
        "data": {"reason": "revision_conflict", "current_revision": int(session.revision or 0)},
    }


async def patch_creation_artifact_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "patch_creation_artifact", "status": "skipped", "detail": "Session not found", "data": None}
    if args.get("expected_revision") is None or int(args["expected_revision"]) != int(session.revision or 0):
        return _revision_error("patch_creation_artifact", session)
    try:
        result = patch_creation_artifact(
            session,
            _text(args.get("artifact")),
            args.get("changes") if isinstance(args.get("changes"), list) else [],
            source="assistant",
            validator=_validate_stage,
        )
        commit_session(db)
        # Writes return a receipt; the complete artifact remains available via
        # get_creation_artifact. Echoing it in the status envelope can exceed
        # model capacity after the database has already committed the write.
        return {
            "tool": "patch_creation_artifact", "status": "ok", "detail": "Artifact patched",
            "data": {
                "session_id": str(session.id),
                "artifact": _text(args.get("artifact")),
                "revision": int(session.revision or 0),
                "changes": result["changes"],
                "affected_artifacts": result["affected_artifacts"],
            },
        }
    except Exception as exc:
        db.rollback()
        return {"tool": "patch_creation_artifact", "status": "error", "detail": str(exc), "data": None}


async def _set_creation_locks(db: Session, args: dict[str, Any], *, locked: bool) -> dict[str, Any]:
    tool = "lock_creation_fields" if locked else "unlock_creation_fields"
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": tool, "status": "skipped", "detail": "Session not found", "data": None}
    if args.get("expected_revision") is None or int(args["expected_revision"]) != int(session.revision or 0):
        return _revision_error(tool, session)
    try:
        artifact = set_creation_artifact_locks(
            session,
            _text(args.get("artifact")),
            args.get("paths") if isinstance(args.get("paths"), list) else [],
            locked=locked,
        )
        commit_session(db)
        return {"tool": tool, "status": "ok", "detail": "Artifact locks updated", "data": artifact}
    except Exception as exc:
        db.rollback()
        return {"tool": tool, "status": "error", "detail": str(exc), "data": None}


async def lock_creation_fields(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _set_creation_locks(db, args, locked=True)


async def unlock_creation_fields(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _set_creation_locks(db, args, locked=False)


async def undo_creation_artifact_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "undo_creation_artifact", "status": "skipped", "detail": "Session not found", "data": None}
    if args.get("expected_revision") is None or int(args["expected_revision"]) != int(session.revision or 0):
        return _revision_error("undo_creation_artifact", session)
    try:
        result = undo_creation_artifact(session, _text(args.get("artifact")))
        commit_session(db)
        return {"tool": "undo_creation_artifact", "status": "ok", "detail": "Latest artifact change undone", "data": result}
    except Exception as exc:
        db.rollback()
        return {"tool": "undo_creation_artifact", "status": "error", "detail": str(exc), "data": None}


async def list_creation_entities_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "list_creation_entities", "status": "skipped", "detail": "Session not found", "data": None}
    result = query_creation_entities(
        session,
        artifact=_text(args.get("artifact")) or None,
        entity_type=_text(args.get("entity_type")) or None,
        include_deleted=bool(args.get("include_deleted", False)),
        query=_text(args.get("query")),
        offset=int(args.get("offset") or 0),
        limit=int(args.get("limit") or 20),
    )
    commit_session(db)
    return {
        "tool": "list_creation_entities",
        "status": "ok",
        "detail": "Creation entities loaded",
        "data": {"revision": int(session.revision or 0), **result},
    }


async def get_creation_entity_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    entity = get_creation_entity_record(db, _text(args.get("entity_id")))
    if not entity:
        return {"tool": "get_creation_entity", "status": "skipped", "detail": "Entity not found", "data": None}
    return {"tool": "get_creation_entity", "status": "ok", "detail": "Creation entity loaded", "data": serialize_creation_entity(entity)}


async def patch_creation_entity_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    entity = get_creation_entity_record(db, _text(args.get("entity_id")))
    if not entity:
        return {"tool": "patch_creation_entity", "status": "skipped", "detail": "Entity not found", "data": None}
    session = _session(db, entity.session_id)
    if args.get("expected_revision") is None or int(args["expected_revision"]) != int(session.revision or 0):
        return _revision_error("patch_creation_entity", session)
    try:
        result = patch_creation_entity_record(
            session,
            entity,
            args.get("changes") if isinstance(args.get("changes"), list) else [],
            expected_revision=int(args["expected_revision"]),
            source="assistant",
        )
        commit_session(db)
        return {"tool": "patch_creation_entity", "status": "ok", "detail": "Creation entity patched", "data": result}
    except Exception as exc:
        db.rollback()
        return {"tool": "patch_creation_entity", "status": "error", "detail": str(exc), "data": None}


async def delete_creation_entity_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    entity = get_creation_entity_record(db, _text(args.get("entity_id")))
    if not entity:
        return {"tool": "delete_creation_entity", "status": "skipped", "detail": "Entity not found", "data": None}
    session = _session(db, entity.session_id)
    if args.get("expected_revision") is None or int(args["expected_revision"]) != int(session.revision or 0):
        return _revision_error("delete_creation_entity", session)
    try:
        result = delete_creation_entity_record(
            session,
            entity,
            expected_revision=int(args["expected_revision"]),
            source="assistant",
        )
        commit_session(db)
        return {"tool": "delete_creation_entity", "status": "ok", "detail": "Creation entity deleted", "data": result}
    except Exception as exc:
        db.rollback()
        return {"tool": "delete_creation_entity", "status": "error", "detail": str(exc), "data": None}


async def list_creation_artifact_versions_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    artifact = _text(args.get("artifact"))
    if not session:
        return {"tool": "list_creation_artifact_versions", "status": "skipped", "detail": "Session not found", "data": None}
    versions = list_artifact_versions(
        db,
        session_id=session.id,
        artifact=artifact,
        limit=int(args.get("limit") or 100),
    )
    return {
        "tool": "list_creation_artifact_versions",
        "status": "ok",
        "detail": "Artifact history loaded",
        "data": {"revision": int(session.revision or 0), "versions": [serialize_artifact_version(item) for item in versions]},
    }


async def get_creation_artifact_diff_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    version = get_artifact_version(db, _text(args.get("version_id")))
    if not version:
        return {"tool": "get_creation_artifact_diff", "status": "skipped", "detail": "Version not found", "data": None}
    try:
        return {
            "tool": "get_creation_artifact_diff",
            "status": "ok",
            "detail": "Artifact diff loaded",
            "data": artifact_version_diff(db, version, against_version_id=_text(args.get("against_version_id")) or None),
        }
    except Exception as exc:
        return {"tool": "get_creation_artifact_diff", "status": "error", "detail": str(exc), "data": None}


async def restore_creation_artifact_version_tool(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    version = get_artifact_version(db, _text(args.get("version_id")))
    if not version:
        return {"tool": "restore_creation_artifact_version", "status": "skipped", "detail": "Version not found", "data": None}
    session = _session(db, version.session_id)
    if args.get("expected_revision") is None or int(args["expected_revision"]) != int(session.revision or 0):
        return _revision_error("restore_creation_artifact_version", session)
    try:
        result = restore_creation_artifact_version_record(
            session, version, expected_revision=int(args["expected_revision"]),
        )
        commit_session(db)
        return {"tool": "restore_creation_artifact_version", "status": "ok", "detail": "Artifact version restored", "data": result}
    except Exception as exc:
        db.rollback()
        return {"tool": "restore_creation_artifact_version", "status": "error", "detail": str(exc), "data": None}


async def import_creation_material(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    session = _session(db, _text(args.get("session_id")))
    if not session:
        return {"tool": "import_creation_material", "status": "skipped", "detail": "Session not found", "data": None}
    file_path = Path(_text(args.get("file_path"))).expanduser()
    if not file_path.exists() or not file_path.is_file():
        return {"tool": "import_creation_material", "status": "error", "detail": "导入文件不存在", "data": None}
    try:
        import_run, replayed = create_material_import(
            db,
            session,
            filename=file_path.name,
            raw=file_path.read_bytes(),
            model=_text(args.get("model")) or None,
            source_message_id=_text(args.get("source_message_id")) or None,
        )
        commit_session(db)
        if not replayed and import_run.status == "queued":
            task = asyncio.create_task(run_material_import(import_run.id, _text(args.get("model")) or None))
            if import_run.operation_id:
                register_operation_actions(import_run.operation_id, cancel=task.cancel)
        return {
            "tool": "import_creation_material",
            "status": "ok",
            "detail": "已恢复同一文件导入" if replayed else "原始文件已保存，持久导入任务已开始",
            "data": serialize_material_import(import_run),
        }
    except Exception as exc:
        db.rollback()
        return {"tool": "import_creation_material", "status": "error", "detail": str(exc), "data": None}


async def preview_creation_import(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    import_run = db.get(NovelCreationMaterialImport, _text(args.get("import_id")))
    if not import_run:
        return {"tool": "preview_creation_import", "status": "skipped", "detail": "Import run not found", "data": None}
    session_id = _text(args.get("session_id"))
    if session_id and import_run.session_id != session_id:
        return {"tool": "preview_creation_import", "status": "error", "detail": "导入任务不属于当前立项会话", "data": None}
    return {
        "tool": "preview_creation_import",
        "status": "ok",
        "detail": "导入预览已就绪" if import_run.status == "waiting_user" else "导入状态已加载",
        "data": serialize_material_import(import_run),
    }


async def apply_creation_import(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    import_run = db.get(NovelCreationMaterialImport, _text(args.get("import_id")))
    if not import_run:
        return {"tool": "apply_creation_import", "status": "skipped", "detail": "Import run not found", "data": None}
    try:
        result = apply_material_import(
            db,
            import_run,
            selected_artifacts=[_text(value) for value in (args.get("selected_artifacts") or [])],
            strategy=_text(args.get("strategy")) or "merge",
            expected_revision=int(args.get("expected_revision")),
        )
        return {"tool": "apply_creation_import", "status": "ok", "detail": "所选导入内容已原子写入", "data": result}
    except RuntimeError as exc:
        if str(exc) == "revision_conflict":
            return {"tool": "apply_creation_import", "status": "conflict", "detail": "立项 revision 已变化，请刷新预览", "data": None}
        return {"tool": "apply_creation_import", "status": "error", "detail": str(exc), "data": None}
    except Exception as exc:
        db.rollback()
        return {"tool": "apply_creation_import", "status": "error", "detail": str(exc), "data": None}


def _resolve_creation_model(model: Any, *, use_model: bool) -> str:
    if not use_model:
        return ""
    requested = _text(model)
    if requested.casefold() in {"siming", "default", "auto", "openai"}:
        requested = ""
    try:
        selection = LLMGateway.select_model_for_task(
            task_type="planning",
            model_override=requested or None,
        )
        return _text(getattr(selection, "model", "")) or requested
    except Exception:
        return requested


async def run_creation_artifact_generation(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    from ....services.novel_creation_stage_execution import execute_creation_artifact_generation

    payload = dict(args)
    payload["model"] = _resolve_creation_model(
        payload.get("model"),
        use_model=bool(payload.get("use_model", True)),
    )
    return await execute_creation_artifact_generation(
        db,
        project_id,
        payload,
        ensure_not_cancelled=_ensure_stage_not_cancelled,
        generate_concepts=_generate_compact_concepts,
        normalize_stage=_normalize_stage_data,
        enhance_with_model=_enhance_with_model,
    )


async def save_creation_artifact(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await save_creation_stage_data(
        db, args, normalize_stage=_normalize_stage_data, validate_stage=_validate_stage,
    )


async def confirm_creation_artifact(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    """Confirm the current artifact without implicitly generating another artifact."""
    session_id = _text(args.get("session_id"))
    artifact = _text(args.get("artifact"))
    session = _session(db, session_id)
    if not session:
        return {"tool": "confirm_creation_artifact", "status": "skipped", "detail": "Session not found", "data": None}
    if "data" in args:
        return {
            "tool": "confirm_creation_artifact",
            "status": "error",
            "detail": "确认工具不能同时修改内容；请先在上一条用户消息中保存修改，再由作者确认当前版本",
            "data": None,
        }
    current = serialize_creation_artifact(session, artifact)
    data = current.get("data") if isinstance(current.get("data"), dict) else None
    if not isinstance(data, dict):
        return {"tool": "confirm_creation_artifact", "status": "conflict", "detail": "Artifact has no generated data to confirm", "data": None}
    result = await save_creation_artifact(db, project_id, {
        "session_id": session_id,
        "stage": artifact,
        "data": data,
        "confirm": True,
        "source": "assistant",
        "expected_revision": args.get("expected_revision"),
    })
    if result.get("status") == "ok":
        run = (
            db.query(NovelCreationStageRun)
            .filter(NovelCreationStageRun.session_id == session_id, NovelCreationStageRun.stage == artifact)
            .order_by(NovelCreationStageRun.created_at.desc())
            .first()
        )
        if run and confirm_run(db, run):
            commit_session(db)
        if run and run.operation_id:
            get_operation_service().complete_author_confirmation(run.operation_id)
    return {**result, "tool": "confirm_creation_artifact"}


async def _generate_creation_artifact(
    db: Session,
    project_id: str,
    args: dict[str, Any],
    *,
    operation: str,
    tool: str,
) -> dict[str, Any]:
    payload = {
        **args,
        "stage": _text(args.get("artifact") or args.get("stage")),
        "operation": operation,
        "use_model": bool(args.get("use_model", True)),
        "auto_confirm": False,
    }
    if operation == "refine" and not _text(payload.get("instruction")):
        return {"tool": tool, "status": "error", "detail": "instruction is required for refinement", "data": None}
    result = await run_creation_artifact_generation(db, project_id, payload)
    return {**result, "tool": tool}


async def generate_creation_artifact(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _generate_creation_artifact(db, project_id, args, operation="generate", tool="generate_creation_artifact")


async def refine_creation_artifact(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _generate_creation_artifact(db, project_id, args, operation="refine", tool="refine_creation_artifact")


async def regenerate_creation_artifact(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _generate_creation_artifact(db, project_id, args, operation="regenerate", tool="regenerate_creation_artifact")


async def _creation_operation_action(args: dict[str, Any], *, action: str, tool: str) -> dict[str, Any]:
    operation_id = _text(args.get("operation_id"))
    if not operation_id:
        return {"tool": tool, "status": "error", "detail": "operation_id is required", "data": None}
    status, payload = await get_operation_service().action(operation_id, action)
    if status == "not_found":
        return {"tool": tool, "status": "skipped", "detail": "Operation not found", "data": None}
    if status != "ok":
        return {"tool": tool, "status": "conflict", "detail": "Operation does not support this action in its current state", "data": payload}
    return {"tool": tool, "status": "ok", "detail": f"Operation action completed: {action}", "data": payload}


async def cancel_creation_operation(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _creation_operation_action(args, action="cancel", tool="cancel_creation_operation")


async def pause_creation_operation(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _creation_operation_action(args, action="pause", tool="pause_creation_operation")


async def resume_creation_operation(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _creation_operation_action(args, action="continue", tool="resume_creation_operation")


async def retry_creation_operation(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    return await _creation_operation_action(args, action="retry_current_unit", tool="retry_creation_operation")


async def validate_creation_session(db: Session, project_id: str, args: dict[str, Any]) -> dict:
    result = await validate_creation_consistency_tool(db, project_id, args)
    return {**result, "tool": "validate_creation_session"}
