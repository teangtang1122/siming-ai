"""Allowlisted public projections for workspace-assistant records.

The durable run log deliberately keeps exact requests and results for replay,
provenance, and crash recovery.  HTTP/SSE consumers must never receive that
audit source directly: it can contain provider state, tool arguments, or a
legacy exception string.  This module is the single public projection layer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from app.architecture.resource_references import public_resource_reference
from app.architecture.tool_result_policy import ModelResultPolicy, ModelResultPreview
from app.core.utils import utc_isoformat
from app.services.chapter_writing_constraints import recommended_han_character_target
from app.services.workspace.assistant_public_errors import public_model_error_message

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:/-]{1,255}$")
_SUCCESS_STATUSES = frozenset({"ok", "completed", "success", "succeeded"})
_ERROR_STATUSES = frozenset({"error", "failed", "interrupted"})
_SAFE_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
)
_INVALID_PUBLIC_VALUE = object()
_MAX_PUBLIC_RECEIPT_STRING_CHARS = 2_000
_MAX_PUBLIC_RECEIPT_LIST_ITEMS = 100


def _safe_identifier(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized if _SAFE_IDENTIFIER.fullmatch(normalized) else None


def _stable_step_detail(tool: str | None, status: str) -> str:
    label = tool or "工具步骤"
    if status in _SUCCESS_STATUSES:
        return f"{label} 已完成"
    if status in {"cancelled", "aborted"}:
        return f"{label} 已取消"
    if status == "running":
        return f"正在执行 {label}"
    if status in _ERROR_STATUSES:
        return f"{label} 执行失败"
    return f"{label} 状态已更新"


def _public_tool_remediation(
    tool: str | None,
    status: str,
    value: Any,
) -> dict[str, Any] | None:
    """Project only producer-declared, author-actionable retry information."""

    if (
        tool != "save_external_chapter_draft"
        or status != "needs_confirmation"
        or not isinstance(value, Mapping)
    ):
        return None
    data = value.get("data")
    if not isinstance(data, Mapping):
        return None
    reason_code = _safe_identifier(data.get("reason_code"))
    messages = {
        "context_manifest_required": "请先准备本章写作上下文，再用返回的清单与令牌保存草稿。",
        "context_manifest_unavailable": "写作上下文已不可用，请重新准备上下文后保存草稿。",
        "context_selection_invalid": "写作上下文令牌无效或已使用，请重新准备上下文后保存草稿。",
        "writing_constraint_invalid": "本章篇幅约束无效，请重新准备写作上下文后重试。",
    }
    if reason_code in messages:
        return {
            "code": reason_code,
            "message": messages[reason_code],
            "retryable": True,
        }
    if reason_code != "draft_below_minimum":
        return None
    actual = data.get("actual_han_characters")
    minimum = data.get("minimum_han_characters")
    if (
        not isinstance(actual, int)
        or isinstance(actual, bool)
        or actual < 0
        or not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum <= actual
    ):
        return None
    missing = minimum - actual
    recommended = recommended_han_character_target(minimum)
    recommended_additional = recommended - actual
    message = (
        f"正文有 {actual} 个汉字，低于最低要求 {minimum} 个；至少还差 {missing} 个。"
        f"为减少反复退回，建议一次补至 {recommended} 个汉字（约再补 "
        f"{recommended_additional} 个）后重试。"
    )
    return {
        "code": reason_code,
        "message": message,
        "retryable": True,
        "actual_han_characters": actual,
        "minimum_han_characters": minimum,
        "missing_han_characters": missing,
        "recommended_han_characters": recommended,
        "recommended_additional_han_characters": recommended_additional,
    }


def _safe_reference_audit(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    kind = str(value.get("source_kind") or "")
    coverage = str(value.get("coverage") or "")
    name = str(value.get("source_name") or "").strip()
    source_chars = value.get("source_chars")
    digest = str(value.get("content_sha256") or "").lower()
    if kind not in {"long_text", "attachment", "routed_data"}:
        return None
    if coverage not in {"full", "distributed", "excerpt"}:
        return None
    if not name or len(name) > 255:
        return None
    if not isinstance(source_chars, int) or isinstance(source_chars, bool) or source_chars < 1:
        return None
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    return {
        "source_kind": kind,
        "source_name": name,
        "coverage": coverage,
        "source_chars": source_chars,
        "content_sha256": digest,
    }


def _declared_public_value(value: Any, *, full_strings: bool) -> Any:
    """Project one declared receipt value without accepting nested objects."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _INVALID_PUBLIC_VALUE
    if isinstance(value, str):
        return value if full_strings else value[:_MAX_PUBLIC_RECEIPT_STRING_CHARS]
    if not isinstance(value, list) or len(value) > _MAX_PUBLIC_RECEIPT_LIST_ITEMS:
        return _INVALID_PUBLIC_VALUE
    projected: list[Any] = []
    for item in value:
        if isinstance(item, (Mapping, list)):
            return _INVALID_PUBLIC_VALUE
        safe_item = _declared_public_value(item, full_strings=full_strings)
        if safe_item is _INVALID_PUBLIC_VALUE:
            return _INVALID_PUBLIC_VALUE
        projected.append(safe_item)
    return projected


def _declared_public_items(
    value: Any,
    preview: ModelResultPreview,
) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    max_items = preview.max_items
    if max_items is None or max_items <= 0 or len(value) > max_items:
        return None
    projected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        public_item: dict[str, Any] = {}
        for field in preview.item_fields:
            if field not in item:
                continue
            safe_value = _declared_public_value(item[field], full_strings=True)
            if safe_value is _INVALID_PUBLIC_VALUE:
                return None
            public_item[field] = safe_value
        projected.append(public_item)
    return projected


def _author_visible_draft_data(tool_name: str, value: Any) -> dict[str, Any] | None:
    """Return the full editor receipt declared for a terminal draft tool."""

    if not isinstance(value, Mapping):
        return None
    # Import lazily so the public projection remains usable while the global
    # registry is binding its tool definitions.
    from app.services.workspace.registry import registry

    definition = registry.get(tool_name)
    if definition is None or not definition.ends_agent_turn:
        return None
    contract = definition.model_result_contract
    preview = contract.preview
    if contract.policy is not ModelResultPolicy.ARTIFACT_REFERENCE or preview is None:
        return None
    if not any(value.get(field) for field in contract.reference_fields):
        return None

    projected: dict[str, Any] = {}
    for field in contract.data_fields:
        if field not in value:
            continue
        safe_value = _declared_public_value(value[field], full_strings=True)
        if safe_value is _INVALID_PUBLIC_VALUE:
            return None
        projected[field] = safe_value

    artifact = value.get(preview.source_field)
    if isinstance(artifact, str):
        projected[preview.source_field] = artifact
    else:
        projected_items = _declared_public_items(artifact, preview)
        if projected_items is None:
            return None
        projected[preview.source_field] = projected_items
    return projected


def public_tool_log(value: Any, *, include_success_data: bool = False) -> dict[str, Any]:
    item = value if isinstance(value, Mapping) else {}
    tool = _safe_identifier(item.get("tool")) or "tool"
    status = (_safe_identifier(item.get("status")) or "unknown").lower()
    projected: dict[str, Any] = {
        "tool": tool,
        "status": status,
        "detail": _stable_step_detail(tool, status),
    }
    remediation = _public_tool_remediation(tool, status, item)
    if remediation is not None:
        projected["detail"] = remediation["message"]
        projected["remediation"] = remediation
    step_id = _safe_identifier(item.get("step_id") or item.get("stepId"))
    if step_id:
        projected["step_id"] = step_id
    if include_success_data and status in _SUCCESS_STATUSES and "data" in item:
        public_data = _author_visible_draft_data(tool, item.get("data"))
        if public_data is not None:
            projected["data"] = public_data
    return projected


def _public_failure(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    code = _safe_identifier(value.get("code"))
    if not code:
        return None
    details = value.get("details") if isinstance(value.get("details"), Mapping) else {}
    retryable = bool(details.get("retryable"))
    projected_details: dict[str, Any] = {"retryable": retryable}
    error_id = _safe_identifier(details.get("error_id"))
    if error_id:
        projected_details["error_id"] = error_id
    remediation = _stable_public_remediation(code)
    if remediation:
        projected_details["remediation"] = remediation
    return {
        "code": code,
        "message": _stable_public_error_message(code),
        "details": projected_details,
    }


def _stable_public_error_message(code: str) -> str:
    if code == "conversation_capacity_unknown":
        return "当前模型缺少可验证的上下文容量配置，本次任务未执行。"
    if code == "conversation_checkpoint_failed":
        return (
            "对话历史整理失败，本次任务未执行；请重试。"
            "若当前使用本机 Agent CLI，请切换已验证的 API 模型或新建对话。"
        )
    if code == "conversation_checkpoint_cancelled":
        return "对话历史整理已取消，本次任务未执行。"
    if code == "conversation_protocol_invalid":
        return "模型工具协议校验失败，本批次未执行。"
    if code == "tool_capability_unavailable":
        return "当前模型不具备可验证的 Agent 工具能力。"
    if code.startswith("model_"):
        return public_model_error_message(code) or "模型调用失败，请检查模型状态后重试。"
    if code == "workspace_assistant_server_error":
        return "工作台助手处理失败，请稍后重试。"
    return "作品助手任务未完成。"


def _stable_public_remediation(code: str) -> str | None:
    if code == "conversation_capacity_unknown":
        return "请配置模型上下文窗口或切换已验证模型。"
    if code == "conversation_checkpoint_failed":
        return "请重试；本机 Agent CLI 请切换已验证的 API 模型，或新建对话。"
    if code == "conversation_checkpoint_cancelled":
        return "请重新发起任务以再次整理。"
    if code == "conversation_protocol_invalid":
        return "请重试本轮或切换支持原生工具协议的模型。"
    if code == "tool_capability_unavailable":
        return "请选择支持原生工具或 direct MCP 的模型。"
    if code.startswith("model_"):
        return "请检查模型设置、额度与网络状态后重试。"
    if code == "workspace_assistant_server_error":
        return "请稍后重试；若持续出现，请携带错误编号反馈。"
    return None


def public_message_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    reply = value.get("reply")
    if isinstance(reply, str):
        result["reply"] = reply
    for field in ("outcome", "scope", "model"):
        item = value.get(field)
        if isinstance(item, str) and len(item) <= 255:
            result[field] = item
    usage = value.get("usage")
    if isinstance(usage, Mapping):
        safe_usage = {
            key: item
            for key, item in usage.items()
            if key in _SAFE_USAGE_KEYS
            and isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
        }
        if safe_usage:
            result["usage"] = safe_usage
    audit = _safe_reference_audit(
        value.get("reference_context_audit") or value.get("reference_context")
    )
    if audit:
        result["reference_context_audit"] = audit
    result["actions"] = []
    result["tool_logs"] = (
        [public_tool_log(item) for item in (value.get("tool_logs") or [])[:100]]
        if isinstance(value.get("tool_logs"), list)
        else []
    )
    result["applied_actions"] = (
        [
            public_tool_log(item, include_success_data=True)
            for item in (value.get("applied_actions") or [])[:100]
        ]
        if isinstance(value.get("applied_actions"), list)
        else []
    )
    assistant_error = _public_failure(value.get("assistant_error"))
    if assistant_error:
        result["assistant_error"] = assistant_error
    context_error = _public_failure(value.get("conversation_context_error"))
    if context_error:
        result["conversation_context_error"] = context_error
    run = value.get("run")
    if isinstance(run, Mapping):
        result["run"] = public_run_mapping(run)
    return result


def public_message_content(message: Any, payload: dict[str, Any] | None) -> str:
    status = str(getattr(message, "status", "") or "").lower()
    if status == "error":
        failure = (payload or {}).get("assistant_error")
        if isinstance(failure, Mapping):
            return str(failure.get("message") or "作品助手任务未完成。")
        return "作品助手任务未完成。"
    if status in {"aborted", "cancelled", "interrupted"}:
        return "作品助手任务未完成或已取消。"
    return str(getattr(message, "content", "") or "")


def _public_run_error(status: str, raw_error: Any) -> tuple[str | None, str | None]:
    if status not in {"error", "aborted", "cancelled", "interrupted"}:
        return None, None
    raw = str(raw_error or "")
    candidate = raw.split(":", 1)[0].strip()
    code = _safe_identifier(candidate) if candidate else None
    if status == "cancelled":
        return "workspace_assistant_cancelled", "作品助手任务已取消。"
    if status == "interrupted":
        return "workspace_assistant_interrupted", "应用中断了尚未完成的作品助手任务。"
    if status == "aborted":
        return "workspace_assistant_aborted", "作品助手任务未完成。"
    if code and (code.startswith("conversation_") or code.startswith("model_")):
        return code, _stable_public_error_message(code)
    if code == "workspace_assistant_server_error":
        return code, _stable_public_error_message(code)
    return "workspace_assistant_failed", "作品助手任务未完成。"


def public_run_mapping(run: Mapping[str, Any]) -> dict[str, Any]:
    status = str(run.get("status") or "")
    code, error = _public_run_error(status, run.get("error"))
    result: dict[str, Any] = {
        "status": _safe_identifier(status) or "unknown",
        "current_iteration": _safe_non_negative_int(run.get("current_iteration")),
        "error": error,
        "error_code": code,
    }
    for field in (
        "run_id",
        "id",
        "project_id",
        "conversation_id",
        "canonical_conversation_id",
        "assistant_message_id",
        "operation_id",
        "phase",
        "scope",
        "model",
        "actual_model",
    ):
        value = _safe_identifier(run.get(field))
        if value:
            result[field] = value
    for field in ("created_at", "updated_at", "completed_at"):
        value = _safe_timestamp(run.get(field))
        if value:
            result[field] = value
    return result


def _safe_non_negative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _safe_timestamp(value: Any) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 40:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?", normalized):
        return None
    return normalized


def public_run_payload(run: Any) -> dict[str, Any]:
    status = str(getattr(run, "status", "") or "")
    code, error = _public_run_error(status, getattr(run, "error", None))
    return {
        "run_id": run.id,
        "actual_model": run.model,
        "id": run.id,
        "project_id": run.project_id,
        "conversation_id": run.conversation_id,
        "assistant_message_id": run.assistant_message_id,
        "operation_id": run.operation_id,
        "status": status,
        "phase": run.phase,
        "scope": run.scope,
        "model": run.model,
        "current_iteration": run.current_iteration or 0,
        "error": error,
        "error_code": code,
        "created_at": utc_isoformat(run.created_at),
        "updated_at": utc_isoformat(run.updated_at),
        "completed_at": utc_isoformat(run.completed_at),
    }


def _payload_metadata(raw: str | None) -> tuple[str | None, int]:
    if not raw:
        return None, 0
    encoded = raw.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _resource_refs(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, Mapping):
        return []
    result: list[dict[str, Any]] = []
    for raw_type, raw_items in value.items():
        items = raw_items if isinstance(raw_items, list) else [raw_items]
        for item in items[:100]:
            if not isinstance(item, Mapping):
                continue
            reference = public_resource_reference(
                raw_type,
                item.get("id"),
                item.get("revision"),
            )
            if reference is None:
                continue
            result.append(reference)
    return result


def public_step_payload(step: Any, *, can_retry: bool, retry_block_reason: str | None) -> dict:
    status = str(step.status or "")
    tool = _safe_identifier(step.tool)
    request_hash, request_bytes = _payload_metadata(step.request_json)
    result_hash, result_bytes = _payload_metadata(step.result_json)
    failed = status in _ERROR_STATUSES
    raw_result: Any = None
    if step.result_json:
        try:
            raw_result = json.loads(step.result_json)
        except (TypeError, json.JSONDecodeError):
            raw_result = None
    remediation = _public_tool_remediation(tool, status, raw_result)
    detail = remediation["message"] if remediation else _stable_step_detail(tool, status)
    result = {
        "id": step.id,
        "run_id": step.run_id,
        "step_type": step.step_type,
        "tool": tool,
        "status": status,
        "iteration": step.iteration or 0,
        "detail": detail,
        "error": _stable_step_detail(tool, status) if failed else None,
        "attempt_no": step.attempt_no or 1,
        "retry_of_step_id": step.retry_of_step_id,
        "resolved_step_id": step.resolved_step_id,
        "idempotency_key": None,
        "can_retry": can_retry,
        "retry_block_reason": retry_block_reason,
        "request_sha256": request_hash,
        "request_bytes": request_bytes,
        "result_sha256": result_hash,
        "result_bytes": result_bytes,
        "resource_refs": _resource_refs(step.output_refs),
        "started_at": utc_isoformat(step.started_at),
        "completed_at": utc_isoformat(step.completed_at),
    }
    if remediation is not None:
        result["remediation"] = remediation
    return result


__all__ = [
    "public_message_content",
    "public_message_payload",
    "public_run_mapping",
    "public_run_payload",
    "public_step_payload",
    "public_tool_log",
]
