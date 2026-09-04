"""Generate one persistent outline proposal from model-selected exact context."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ai.capabilities import (
    TOOL_CAPABILITY_UNAVAILABLE,
    TOOL_CAPABILITY_UNAVAILABLE_MESSAGE,
)
from app.architecture.uow import commit_session
from app.modules.story.domain.outline_contract import OUTLINE_PROPOSAL_MAX_NODES

from ....core.exceptions import ValidationError
from ....database.models import Project
from ....modules.model_runtime.application.execution import model_executor as LLMGateway
from ....prompts.outline_writer_prompts import build_outline_writer_messages
from ....services.context_orchestrator import ContextOrchestrator
from ....services.task_context_selection import render_generation_context
from ..outline_drafts import (
    PendingOutlineDraftConflict,
    latest_pending_outline_draft,
    outline_draft_result_data,
    outline_proposal_batch_count,
    pending_outline_draft_block_result,
    store_outline_draft,
)
from ..turn_control import AssistantTurnDirective, apply_turn_directive
from .native_structured_output import (
    NativeStructuredOutputError,
    required_tool_arguments,
)

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
                    "maxItems": OUTLINE_PROPOSAL_MAX_NODES,
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
                            "character_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "本节点未来会涉及的人物名。尚未建立人物档案的新人物可在这里规划；"
                                    "作者确认草稿时只保留待引入姓名，不会自动创建人物档案。"
                                ),
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


async def _generate_outline(
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    max_tokens: int,
    gateway_extra: dict[str, Any],
) -> dict[str, Any]:
    return await LLMGateway.chat_completion(
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


def _native_model_binding(
    args: dict[str, Any],
    manifest: Any,
) -> tuple[str | None, dict[str, Any] | None]:
    model_override = str(args.get("model") or "").strip()
    manifest_model = str(manifest.model or "").strip()
    manifest_provider = str(manifest.provider or "").strip()
    model = model_override or (
        f"{manifest_provider}:{manifest_model}"
        if manifest_provider and manifest_model
        else manifest_model
    )
    bound_model = model or None
    if LLMGateway.supports_tool_calling(bound_model):
        return bound_model, None
    return bound_model, _result(
        "error",
        TOOL_CAPABILITY_UNAVAILABLE_MESSAGE,
        {"reason": TOOL_CAPABILITY_UNAVAILABLE},
    )


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
        return _result("needs_confirmation", "The requested context manifest was not found.")
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
    try:
        batch_count = outline_proposal_batch_count(manifest)
    except ValidationError as exc:
        return _result("needs_confirmation", str(exc))
    if "batch_count" in args and args["batch_count"] != batch_count:
        return _result("needs_confirmation", "batch_count 与已审阅的规划上下文不一致，请重新规划")
    model, capability_error = _native_model_binding(args, manifest)
    if capability_error is not None:
        return capability_error
    if not orchestrator.mark_consumed(manifest):
        return _result(
            "needs_confirmation",
            "context_selection_token 已使用；请重新检索并提交资料。",
            {"context_manifest_id": manifest.id},
        )
    commit_session(db)

    messages = build_outline_writer_messages(
        task_context=render_generation_context(manifest), batch_count=batch_count
    )
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
        result = await _generate_outline(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            gateway_extra=gateway_extra,
        )
    except Exception as exc:
        return _result("error", f"大纲生成失败: {exc}")

    try:
        parsed, raw_for_error = required_tool_arguments(
            result,
            expected_name="propose_outline_nodes",
        )
    except NativeStructuredOutputError as exc:
        return _result(
            "error",
            "大纲生成结果解析失败",
            {"protocol_error": exc.reason},
        )
    if not parsed.get("nodes"):
        return _result(
            "error",
            "大纲生成结果解析失败",
            {"raw": raw_for_error[:500]},
        )
    raw_nodes = parsed.get("nodes", [])
    if (
        not isinstance(raw_nodes, list)
        or len(raw_nodes) > OUTLINE_PROPOSAL_MAX_NODES
        or any(not isinstance(node, dict) for node in raw_nodes)
    ):
        return _result(
            "error",
            "大纲生成结果必须包含 1 至 "
            f"{OUTLINE_PROPOSAL_MAX_NODES} 个有效节点",
        )
    nodes = raw_nodes

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
    except ValidationError as exc:
        return _result("error", str(exc), {"context_manifest_id": manifest.id})

    return apply_turn_directive(
        _result(
            "ok",
            f"已生成 {len(nodes)} 个节点的大纲草稿，等待作者确认",
            outline_draft_result_data(draft),
        ),
        AssistantTurnDirective.END_AFTER_OUTLINE_DRAFT,
    )


__all__ = ["OUTLINE_PROPOSAL_TOOL", "outline_writer"]
