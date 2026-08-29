"""Generate one persistent outline proposal from model-selected exact context."""

from __future__ import annotations

import json as _json
from typing import Any

from sqlalchemy.orm import Session

from app.architecture.uow import commit_session

from ....core.json_repair import parse_json_object
from ....database.models import Project
from ....modules.model_runtime.application.execution import model_executor as LLMGateway
from ....prompts.outline_writer_prompts import build_outline_writer_messages
from ....services.context_orchestrator import ContextOrchestrator
from ....services.story_granularity import extract_chapter_number, normalize_outline_batch
from ....services.task_context_selection import render_generation_context
from ..outline_drafts import (
    PendingOutlineDraftConflict,
    latest_pending_outline_draft,
    outline_draft_result_data,
    pending_outline_draft_block_result,
    store_outline_draft,
)
from ..turn_control import AssistantTurnDirective, apply_turn_directive

OUTLINE_PROPOSAL_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_outline_nodes",
        "description": "提出供作者审阅的大纲草稿；不会写入正式大纲。",
        "parameters": {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "minLength": 1, "maxLength": 200},
                            "node_type": {
                                "type": "string",
                                "enum": ["chapter", "volume", "section"],
                            },
                            "summary": {"type": "string"},
                            "parent_title": {"type": "string"},
                            "actual_summary": {"type": "string"},
                            "planned_summary": {"type": "string"},
                            "character_names": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "status": {"type": "string", "enum": ["pending"]},
                        },
                        "required": [
                            "title",
                            "node_type",
                            "summary",
                            "character_names",
                            "status",
                        ],
                    },
                },
                "design_notes": {"type": "string"},
            },
            "required": ["nodes", "design_notes"],
        },
    },
}


def _parse_jsonish_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = _json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except (_json.JSONDecodeError, TypeError, ValueError):
        pass
    return parse_json_object(value)


def _normalize_outline_payload(value: Any) -> dict[str, Any] | None:
    parsed = _parse_jsonish_object(value)
    if not isinstance(parsed, dict):
        return None
    if isinstance(parsed.get("nodes"), list):
        return parsed
    if isinstance(parsed.get("node"), dict):
        return {**parsed, "nodes": [parsed["node"]]}
    for key in (
        "arguments",
        "args",
        "input",
        "parameters",
        "payload",
        "function",
        "data",
        "action",
        "propose_outline_nodes",
    ):
        candidate = _normalize_outline_payload(parsed.get(key))
        if candidate:
            if not candidate.get("design_notes") and parsed.get("design_notes"):
                candidate = {**candidate, "design_notes": parsed.get("design_notes")}
            return candidate
    for key in ("actions", "tool_calls"):
        values = parsed.get(key)
        if isinstance(values, list):
            for value in values:
                candidate = _normalize_outline_payload(value)
                if candidate:
                    return candidate
    return None


def _outline_payload_from_result(
    result: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    candidates: list[Any] = []
    for call in result.get("tool_calls") or []:
        if isinstance(call, dict):
            function = call.get("function")
            if isinstance(function, dict):
                candidates.append(function.get("arguments", ""))
            candidates.append(call)
    candidates.append(result.get("content", ""))
    raw_for_error = ""
    for raw in candidates:
        if raw_for_error == "":
            raw_for_error = (
                raw if isinstance(raw, str) else _json.dumps(raw, ensure_ascii=False)
            )
        parsed = _normalize_outline_payload(raw)
        if parsed:
            return parsed, raw_for_error
    return None, raw_for_error


def _normalize_generated_nodes(
    nodes: list[Any],
    requirements: str,
) -> list[dict[str, Any]]:
    normalized = normalize_outline_batch(
        [item for item in nodes[:8] if isinstance(item, dict)],
        chapter_number=extract_chapter_number(requirements),
    )
    for node in normalized:
        if "character_names" not in node and isinstance(
            node.get("related_characters"), list
        ):
            node["character_names"] = node.get("related_characters")
        node.pop("related_characters", None)
        node["status"] = "pending"
    return normalized


def _result(
    status: str,
    detail: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "tool": "outline_writer",
        "status": status,
        "detail": detail,
        "data": data or {},
    }


async def outline_writer(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Generate an unsaved proposal after an Agent finalized exact evidence."""
    if not db.query(Project.id).filter(Project.id == project_id).first():
        return _result("skipped", "项目不存在")
    pending = latest_pending_outline_draft(db, project_id)
    if pending:
        return pending_outline_draft_block_result("outline_writer", pending)

    parent_id = str(args.get("parent_id") or "").strip() or None
    insert_after_id = str(args.get("insert_after_id") or "").strip() or None
    requirements = str(args.get("requirements") or "").strip()
    batch_count = max(1, min(8, int(args.get("batch_count") or 1)))
    manifest_id = str(args.get("context_manifest_id") or "").strip()
    selection_token = str(args.get("context_selection_token") or "").strip()
    if not manifest_id:
        return _result(
            "needs_confirmation",
            "必须先建立大纲规划上下文，并由模型检索、复核资料。",
            {"next_tool": "prepare_task_context"},
        )

    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(manifest_id, project_id)
    if manifest is None:
        return _result(
            "needs_confirmation",
            "The requested context manifest was not found.",
        )
    usable, detail = orchestrator.validate_task_selection(
        manifest,
        token=selection_token,
        task_type="outline_planning",
        parent_id=parent_id,
        insert_after_id=insert_after_id,
    )
    if not usable:
        return _result(
            (
                manifest.status
                if manifest.status in {"stale", "blocked_rebuild"}
                else "needs_confirmation"
            ),
            detail,
            {
                "context_manifest_id": manifest.id,
                "context_manifest": orchestrator.manifest_payload(
                    manifest,
                    include_content=False,
                ),
            },
        )
    if not orchestrator.mark_consumed(manifest):
        return _result(
            "needs_confirmation",
            "context_selection_token 已使用；请重新检索并提交资料。",
            {"context_manifest_id": manifest.id},
        )
    commit_session(db)

    messages = build_outline_writer_messages(
        task_context=render_generation_context(manifest),
        batch_count=batch_count,
    )
    model = str(args.get("model") or manifest.model or "") or None
    max_tokens = max(1, int(manifest.output_reserve_tokens or 1))
    gateway_extra = LLMGateway.local_cli_extra_body(
        model,
        base={
            "moshu_task_type": "planning",
            "moshu_project_id": project_id,
            "moshu_context_manifest_id": manifest.id,
            "moshu_context_manifest_rendered": True,
            "local_cli_isolated": True,
        },
    )
    commit_session(db)
    try:
        result = await LLMGateway.chat_completion(
            messages=messages,
            model=model,
            temperature=0.7,
            max_tokens=max_tokens,
            timeout=180,
            retry=1,
            extra_body=gateway_extra,
            tools=[OUTLINE_PROPOSAL_TOOL],
            tool_choice="required",
        )
    except Exception as exc:
        return _result("error", f"大纲生成失败: {exc}")

    parsed, raw_for_error = _outline_payload_from_result(result)
    if not isinstance(parsed, dict) or not parsed.get("nodes"):
        return _result(
            "error",
            "大纲生成结果解析失败",
            {"raw": str(raw_for_error)[:500]},
        )
    raw_nodes = parsed.get("nodes", [])
    if (
        not isinstance(raw_nodes, list)
        or len(raw_nodes) > 8
        or any(not isinstance(node, dict) for node in raw_nodes)
    ):
        return _result("error", "大纲生成结果必须包含 1 至 8 个有效节点")
    nodes = _normalize_generated_nodes(raw_nodes, requirements)
    if not nodes:
        return _result("error", "大纲生成结果没有有效节点")

    usable, detail = orchestrator.validate(manifest)
    if not usable:
        return _result(
            "stale",
            detail,
            {"context_manifest_id": manifest.id},
        )
    try:
        draft = store_outline_draft(
            db,
            project_id=project_id,
            context_manifest_id=manifest.id,
            parent_id=parent_id,
            insert_after_id=insert_after_id,
            nodes=nodes,
            design_notes=str(parsed.get("design_notes") or ""),
            context_selection_token=selection_token,
        )
    except PendingOutlineDraftConflict as conflict:
        return pending_outline_draft_block_result(
            "outline_writer",
            conflict.draft,
        )

    return apply_turn_directive(
        _result(
            "ok",
            f"已生成 {len(nodes)} 个节点的大纲草稿，等待作者确认",
            outline_draft_result_data(draft),
        ),
        AssistantTurnDirective.END_AFTER_OUTLINE_DRAFT,
    )


__all__ = ["OUTLINE_PROPOSAL_TOOL", "outline_writer"]
