"""Single model-visible projection path for workspace tool results.

Tool handlers and run records retain the complete result.  Agent loops should
send only :class:`ProjectedToolResult.content` to the model and must not add a
second redaction/truncation layer.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

from ...architecture.tool_result_policy import (
    ModelResultContract,
    ModelResultListProjection,
    ModelResultPolicy,
    ModelResultPreview,
)


class ToolWithModelResultContract(Protocol):
    name: str
    model_result_contract: ModelResultContract


class ToolResultProjectionError(ValueError):
    """The declared result contract cannot safely project a tool result."""

    def __init__(self, tool_name: str, detail: str) -> None:
        super().__init__(f"{tool_name}: {detail}")
        self.tool_name = tool_name
        self.detail = detail

    def model_error_result(self) -> dict[str, Any]:
        """Return a small native tool-result payload for a caller to deliver."""

        return {
            "tool": self.tool_name,
            "status": "error",
            "detail": self.detail,
            "data": {"reason": "model_result_projection_failed"},
        }


class ToolResultOverCapacity(ToolResultProjectionError):
    """A complete declared projection cannot fit its model-visible boundary."""

    def __init__(self, tool_name: str, *, actual_bytes: int, max_bytes: int) -> None:
        super().__init__(
            tool_name,
            (
                "工具结果超过本次模型可见容量；请缩小读取范围或使用工具的分页参数，"
                "本次结果未被截断后投递。"
            ),
        )
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes

    def model_error_result(self) -> dict[str, Any]:
        payload = super().model_error_result()
        payload["data"].update(
            {
                "reason": "tool_result_over_capacity",
                "actual_bytes": self.actual_bytes,
                "max_bytes": self.max_bytes,
            }
        )
        return payload


# One native assistant response may contain multiple tool calls.  The context
# runtime reserves this *whole-batch* boundary before asking the model, and the
# executor must admit the complete batch before running any handler.  A batch
# is never shortened after the model emitted it because that would orphan
# native tool-call IDs and could hide already-committed side effects.
MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES = 32 * 1024
MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES = 16 * 1024
TOOL_CATEGORY_CONTROLLER_RESULT_CONTRACT = ModelResultContract(
    policy=ModelResultPolicy.STATUS_ONLY,
    max_json_bytes=4 * 1024,
    data_fields=("enabled_categories",),
)


_DIAGNOSTIC_STATUSES = frozenset({
    "error",
    "denied",
    "failed",
    "cancelled",
    "canceled",
})
_DIAGNOSTIC_DETAILS = {
    "error": "工具执行失败；请检查参数和当前项目状态后重试。",
    "failed": "工具执行失败；请检查参数和当前项目状态后重试。",
    "denied": "工具调用未获许可或已达到本轮执行边界；本次未执行。",
    "cancelled": "工具执行已取消。",
    "canceled": "工具执行已取消。",
}
_DIAGNOSTIC_REASON_DETAILS = {
    "native_tool_contract_invalid": (
        "工具参数不符合当前 JSON Schema，本次未执行。请核对必填字段及类型；"
        "对象和数组必须直接传入，不能编码成 JSON 字符串。修正后再调用。"
    ),
    "revision_conflict": "资料版本已经变化，本次未写入。请读取最新内容和 revision 后再决定修改。",
    "tool_result_over_capacity": (
        "工具结果超过当前模型可见容量。请缩小读取范围或使用分页；不要原样重复调用。"
    ),
}
# Diagnostic payloads are an untrusted boundary: many legacy handlers catch an
# arbitrary provider/database exception and put ``str(exc)`` in ``detail`` or
# ``data``.  Only deterministic protocol codes produced by this repository may
# survive into a model, MCP client, run step, checkpoint receipt, REST or SSE.
_SAFE_DIAGNOSTIC_REASONS = frozenset({
    "failed_write_limit",
    "history_sequence_gap",
    "invalid_turn_state",
    "missing_tool_category_controller",
    "model_result_projection_failed",
    "native_assistant_transaction_invalid",
    "native_assistant_transaction_over_capacity",
    "native_tool_contract_invalid",
    "native_tool_not_open",
    "read_required",
    "revision_conflict",
    "serialization_failed",
    "successful_write_limit",
    "tool_execution_failed",
    "tool_result_batch_over_capacity",
    "tool_result_over_capacity",
})
_SAFE_DIAGNOSTIC_NUMERIC_FIELDS = frozenset({
    "current_revision",
    "actual_bytes",
    "batch_call_count",
    "call_count",
    "declared_batch_json_bytes",
    "failed_write_limit",
    "failed_writes",
    "max_batch_json_bytes",
    "max_bytes",
    "successful_writes",
    "write_limit",
})
_SAFE_ERROR_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class _DeclaredToolResult:
    name: str
    model_result_contract: ModelResultContract


class ToolResultBatchOverCapacity(ValueError):
    """A complete native tool-call batch cannot be executed within its contract."""

    def __init__(
        self,
        *,
        tool_names: tuple[str, ...],
        declared_json_bytes: int,
        max_json_bytes: int,
        call_count: int,
        reason: str = "tool_result_batch_over_capacity",
    ) -> None:
        if reason.startswith("native_assistant_transaction_"):
            detail = (
                f"当前原生工具 assistant 事务为 {declared_json_bytes} 字节，"
                f"不符合协议边界（上限 {max_json_bytes} 字节）；整批未执行。"
                "请减少并行调用或缩小工具参数。"
            )
        else:
            detail = (
                f"当前工具批次的声明结果上限为 {declared_json_bytes} 字节，"
                f"超过单步 {max_json_bytes} 字节上限；整批未执行。"
                "请减少并行调用，或使用工具的分页、范围参数。"
            )
        super().__init__(detail)
        self.tool_names = tool_names
        self.declared_json_bytes = declared_json_bytes
        self.max_json_bytes = max_json_bytes
        self.call_count = call_count
        self.detail = detail
        self.reason = reason

    def model_error_result(self, tool_name: str) -> dict[str, Any]:
        """Return one small native result for every rejected tool-call ID."""

        return {
            "tool": tool_name,
            "status": "error",
            "detail": self.detail,
            "data": {
                "reason": self.reason,
                "batch_call_count": self.call_count,
                "declared_batch_json_bytes": self.declared_json_bytes,
                "max_batch_json_bytes": self.max_json_bytes,
            },
        }


def max_model_visible_result_tokens_for_open_tools(
    tools: Iterable[ToolWithModelResultContract],
) -> int:
    """Return the conservative token reserve for one *admissible* result batch.

    A UTF-8 JSON payload cannot require more model tokens than its byte length.
    The result therefore remains conservative for exact provider tokenizers and
    for the repository's UTF-8-byte counter.  ``admit_model_tool_result_batch``
    is the matching pre-execution gate that makes this open-tool reserve true.
    """

    resolved = tuple(tools)
    if not resolved:
        return 0
    if len(resolved) == 1 and resolved[0].name == "set_tool_categories":
        return resolved[0].model_result_contract.max_json_bytes
    return MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES


def _openai_tool_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function")
    if not isinstance(function, Mapping):
        raise ValueError("工具 Schema 缺少 function 对象")
    name = str(function.get("name") or "").strip()
    if not name:
        raise ValueError("工具 Schema 缺少 function.name")
    return name


def declared_model_results_for_openai_tools(
    tool_schemas: Iterable[Mapping[str, Any]],
    *,
    resolve_tool: Callable[[str], ToolWithModelResultContract | None],
) -> tuple[ToolWithModelResultContract, ...]:
    """Resolve native schemas to authoritative result contracts.

    Unknown schemas fail closed.  ``set_tool_categories`` is the only
    runtime-owned schema and has a small explicit receipt contract here.
    """

    return declared_model_results_for_tool_names(
        (_openai_tool_name(schema) for schema in tool_schemas),
        resolve_tool=resolve_tool,
    )


def declared_model_results_for_tool_names(
    tool_names: Iterable[str],
    *,
    resolve_tool: Callable[[str], ToolWithModelResultContract | None],
) -> tuple[ToolWithModelResultContract, ...]:
    """Resolve an ordered call-name batch, preserving repeated tool calls."""

    resolved: list[ToolWithModelResultContract] = []
    for raw_name in tool_names:
        name = str(raw_name or "").strip()
        if not name:
            raise ValueError("工具调用缺少名称")
        if name == "set_tool_categories":
            resolved.append(
                _DeclaredToolResult(
                    name=name,
                    model_result_contract=TOOL_CATEGORY_CONTROLLER_RESULT_CONTRACT,
                )
            )
            continue
        tool = resolve_tool(name)
        if tool is None:
            raise ValueError(f"工具 {name} 没有声明模型可见结果契约")
        resolved.append(tool)
    return tuple(resolved)


def max_model_visible_result_tokens_for_open_tool_schemas(
    tool_schemas: Iterable[Mapping[str, Any]],
    *,
    resolve_tool: Callable[[str], ToolWithModelResultContract | None],
) -> int:
    """Resolve OpenAI schemas and return their whole-batch token reserve."""

    return max_model_visible_result_tokens_for_open_tools(
        declared_model_results_for_openai_tools(
            tool_schemas,
            resolve_tool=resolve_tool,
        ),
    )


def max_native_tool_transaction_wrapper_tokens() -> int:
    """Reserve replay growth not included in model-visible result contents.

    ``admit_native_assistant_transaction`` hard-validates the exact UTF-8 JSON
    payload before any handler runs.  Each result-message wrapper repeats only
    metadata already present in its validated call, so a second assistant-sized
    byte reserve bounds the wrappers without assuming a fixed call count.  The
    context runtime treats the returned integer as an already-counted token
    reserve.
    """

    return 2 * MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES


def admit_native_assistant_transaction(
    assistant_payload: Mapping[str, Any],
    tools: Iterable[ToolWithModelResultContract],
) -> int:
    """Validate exact native assistant state and declared results pre-handler.

    ``assistant_payload`` must be the exact assistant message that will be
    replayed, including content, reasoning content, provider state and the full
    ordered ``tool_calls`` array.  Returns conservative whole-transaction
    growth bytes/tokens.  Any failure means zero handlers may run.
    """

    resolved = tuple(tools)

    def invalid(call_count: int, *, declared_json_bytes: int = 0) -> None:
        raise ToolResultBatchOverCapacity(
            tool_names=tuple(tool.name for tool in resolved),
            declared_json_bytes=declared_json_bytes,
            max_json_bytes=MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES,
            call_count=call_count,
            reason="native_assistant_transaction_invalid",
        )

    if assistant_payload.get("role") != "assistant":
        invalid(0)
    raw_calls = assistant_payload.get("tool_calls")
    if not isinstance(raw_calls, (list, tuple)) or len(raw_calls) != len(resolved):
        invalid(len(raw_calls) if isinstance(raw_calls, (list, tuple)) else 0)
    call_ids: set[str] = set()
    for raw_call, tool in zip(raw_calls, resolved, strict=True):
        if not isinstance(raw_call, Mapping):
            invalid(len(raw_calls))
        call_id = str(raw_call.get("id") or raw_call.get("call_id") or "").strip()
        function = raw_call.get("function")
        if not call_id or call_id in call_ids or not isinstance(function, Mapping):
            invalid(len(raw_calls))
        call_ids.add(call_id)
        if str(function.get("name") or "").strip() != tool.name or not isinstance(
            function.get("arguments"), str
        ):
            invalid(len(raw_calls))
    try:
        payload = _json_content("native_assistant_transaction", assistant_payload)
    except ToolResultProjectionError:
        invalid(len(raw_calls))
    assistant_bytes = len(payload.encode("utf-8"))
    if assistant_bytes > MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES:
        raise ToolResultBatchOverCapacity(
            tool_names=tuple(tool.name for tool in resolved),
            declared_json_bytes=assistant_bytes,
            max_json_bytes=MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES,
            call_count=len(raw_calls),
            reason="native_assistant_transaction_over_capacity",
        )
    declared_result_bytes = admit_model_tool_result_batch(resolved)
    return assistant_bytes + declared_result_bytes + assistant_bytes


def admit_model_tool_result_batch(
    tools: Iterable[ToolWithModelResultContract],
) -> int:
    """Admit a complete resolved call batch before any handler is executed.

    Returns its conservative declared result bytes.  Callers must preserve call
    order and, on failure, emit one native error result per original call ID.
    """

    resolved = tuple(tools)
    declared = sum(tool.model_result_contract.max_json_bytes for tool in resolved)
    if declared > MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES:
        raise ToolResultBatchOverCapacity(
            tool_names=tuple(tool.name for tool in resolved),
            declared_json_bytes=declared,
            max_json_bytes=MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES,
            call_count=len(resolved),
        )
    return declared


@dataclass(frozen=True)
class ProjectedToolResult:
    """A validated JSON projection ready for a native tool-result message."""

    tool_name: str
    policy: ModelResultPolicy
    payload: dict[str, Any]
    content: str
    source_json_bytes: int
    projected_json_bytes: int
    full_source_delivered: bool


def _json_content(tool_name: str, value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ToolResultProjectionError(
            tool_name,
            f"工具结果不是有效 JSON：{exc}",
        ) from exc


def _json_clone(tool_name: str, value: Any) -> Any:
    return json.loads(_json_content(tool_name, value))


def sanitize_diagnostic_tool_result(
    tool_name: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove untrusted exception text from a diagnostic tool result.

    The returned envelope deliberately does not attempt keyword-based secret
    detection.  All free-form fields are discarded, including ``detail``,
    ``error``, warnings, arguments, provider output and nested data.  A small
    allowlist of repository-owned protocol reason codes and typed counters is
    enough for deterministic model recovery without reflecting raw failures.

    Non-diagnostic results are JSON-cloned unchanged so callers may apply this
    function at the authoritative executor boundary before persistence.
    """

    status = str(result.get("status") or "error").strip().lower()
    if status not in _DIAGNOSTIC_STATUSES:
        return _json_clone(tool_name, result)

    safe_data: dict[str, Any] = {}
    source_data = result.get("data")
    if isinstance(source_data, Mapping):
        reason = source_data.get("reason")
        if isinstance(reason, str) and reason in _SAFE_DIAGNOSTIC_REASONS:
            safe_data["reason"] = reason

        # Runtime schema failures originate at the authoritative executor and
        # contain only repository-owned field locations and validation rules.
        # Convert the transport-facing failure class to the existing stable
        # reason code so every model path receives the same recovery guidance.
        failure_class = source_data.get("failure_class")
        if failure_class == "invalid_tool_arguments":
            safe_data["reason"] = "native_tool_contract_invalid"
            safe_data["failure_class"] = failure_class
            path = source_data.get("path")
            if (
                isinstance(path, str)
                and 0 < len(path) <= 256
                and path.startswith("$")
                and all(char.isalnum() or char in "$._-[]" for char in path)
            ):
                safe_data["path"] = path
            rule = source_data.get("rule")
            if (
                isinstance(rule, str)
                and 0 < len(rule) <= 80
                and all(char.isalnum() or char in "_-" for char in rule)
            ):
                safe_data["rule"] = rule

        error_id = source_data.get("error_id")
        if isinstance(error_id, str) and _SAFE_ERROR_ID.fullmatch(error_id):
            safe_data["error_id"] = error_id

        retryable = source_data.get("retryable")
        if isinstance(retryable, bool):
            safe_data["retryable"] = retryable

        for field in _SAFE_DIAGNOSTIC_NUMERIC_FIELDS:
            value = source_data.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                safe_data[field] = value

    retryable = result.get("retryable")
    if isinstance(retryable, bool):
        safe_data.setdefault("retryable", retryable)

    return {
        "tool": tool_name,
        "status": status,
        "detail": _DIAGNOSTIC_REASON_DETAILS.get(
            safe_data.get("reason"), _DIAGNOSTIC_DETAILS[status],
        ),
        "data": safe_data or None,
    }


def _base_envelope(
    tool_name: str,
    result: Mapping[str, Any],
    *,
    result_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool": tool_name,
        "status": str(result.get("status") or "error"),
        "detail": str(result.get("detail") or ""),
    }
    for field in ("error", "error_code", "code", "warnings"):
        if field in result:
            payload[field] = _json_clone(tool_name, result[field])
    for field in result_fields:
        if field in result:
            payload[field] = _json_clone(tool_name, result[field])
    return payload


def _selected_data(
    tool_name: str,
    data: Any,
    fields: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        return {}
    return {field: _json_clone(tool_name, data[field]) for field in fields if field in data}


def _project_list(
    tool_name: str,
    value: Any,
    projection: ModelResultListProjection,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ToolResultProjectionError(
            tool_name,
            f"声明的列表字段 {projection.source_field or 'data'} 不是数组",
        )
    if projection.max_items is not None and len(value) > projection.max_items:
        raise ToolResultProjectionError(
            tool_name,
            (
                f"列表字段 {projection.source_field or 'data'} 返回 {len(value)} 项，"
                f"超过声明上限 {projection.max_items}；必须由工具分页，不能由投影器截断"
            ),
        )
    projected: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ToolResultProjectionError(
                tool_name,
                f"列表字段 {projection.source_field or 'data'} 的第 {index + 1} 项不是对象",
            )
        projected.append(
            {
                field: _json_clone(tool_name, item[field])
                for field in projection.item_fields
                if field in item
            }
        )
    return projected


def _apply_list_projections(
    tool_name: str,
    source_data: Any,
    projected_data: dict[str, Any] | list[dict[str, Any]],
    projections: tuple[ModelResultListProjection, ...],
) -> dict[str, Any] | list[dict[str, Any]]:
    current = projected_data
    for projection in projections:
        source_value = (
            source_data
            if not projection.source_field
            else (
                source_data.get(projection.source_field)
                if isinstance(source_data, Mapping)
                else None
            )
        )
        values = _project_list(tool_name, source_value, projection)
        if not projection.output_field:
            if len(projections) != 1 or isinstance(current, dict) and current:
                raise ToolResultProjectionError(
                    tool_name,
                    "data 根列表投影不能与其他 data 字段组合",
                )
            current = values
        else:
            if not isinstance(current, dict):
                raise ToolResultProjectionError(
                    tool_name,
                    "data 根列表投影不能再追加命名列表",
                )
            current[projection.output_field] = values
    return current


def _project_declared_data(
    tool_name: str,
    source_data: Any,
    contract: ModelResultContract,
) -> dict[str, Any] | list[dict[str, Any]]:
    selected: dict[str, Any] | list[dict[str, Any]] = _selected_data(
        tool_name,
        source_data,
        contract.data_fields,
    )
    return _apply_list_projections(
        tool_name,
        source_data,
        selected,
        contract.list_projections,
    )


def _preview_value(
    tool_name: str,
    source_data: Mapping[str, Any],
    preview: ModelResultPreview,
) -> tuple[Any, dict[str, Any]]:
    value = source_data.get(preview.source_field)
    if isinstance(value, str):
        if preview.max_chars is None or preview.max_chars <= 0:
            raise ToolResultProjectionError(
                tool_name,
                "字符串 artifact preview 必须声明正数 max_chars",
            )
        visible = value[: preview.max_chars]
        metadata = {
            "source_chars": len(value),
            "truncated": len(visible) < len(value),
            "sha256": sha256(value.encode("utf-8")).hexdigest(),
        }
        return visible, metadata
    if isinstance(value, list):
        projection = ModelResultListProjection(
            source_field=preview.source_field,
            output_field=preview.output_field,
            item_fields=preview.item_fields,
            max_items=preview.max_items,
        )
        visible = _project_list(tool_name, value, projection)
        return visible, {"source_items": len(value), "truncated": False}
    if value is None:
        return None, {"missing": True}
    raise ToolResultProjectionError(
        tool_name,
        f"artifact preview 字段 {preview.source_field} 必须是字符串或数组",
    )


class ModelToolResultProjector:
    """Apply the result contract declared by a ToolDef or ToolSpec."""

    def project(
        self,
        tool: ToolWithModelResultContract,
        result: Mapping[str, Any],
        *,
        max_json_bytes: int | None = None,
    ) -> ProjectedToolResult:
        if not isinstance(result, Mapping):
            raise ToolResultProjectionError(tool.name, "工具结果必须是 JSON 对象")
        raw_tool_name = result.get("tool")
        if raw_tool_name not in (None, "", tool.name):
            raise ToolResultProjectionError(
                tool.name,
                f"工具结果声明了不匹配的 tool={raw_tool_name}",
            )

        contract = tool.model_result_contract
        limit = contract.max_json_bytes
        if max_json_bytes is not None:
            if max_json_bytes <= 0:
                raise ValueError("max_json_bytes must be positive")
            limit = min(limit, max_json_bytes)

        source_content = _json_content(tool.name, result)
        source_bytes = len(source_content.encode("utf-8"))

        status = str(result.get("status") or "error").strip().lower()
        # Tool-specific non-terminal success states (for example ``running``
        # and ``needs_confirmation``) still carry successful business data and
        # must use the declared projection.  Only true diagnostic outcomes keep
        # their original error envelope.
        diagnostic_status = status in _DIAGNOSTIC_STATUSES
        if diagnostic_status:
            # Handler-returned failures are not trusted. Legacy tools may have
            # copied provider bodies, database exceptions, arguments or secrets
            # into their error envelope, so only deterministic protocol fields
            # cross the model-visible boundary.
            payload = sanitize_diagnostic_tool_result(tool.name, result)
        elif contract.policy is ModelResultPolicy.INLINE_BOUNDED:
            payload = _json_clone(tool.name, result)
            payload["tool"] = tool.name
        else:
            payload = _base_envelope(
                tool.name,
                result,
                result_fields=contract.result_fields,
            )
            source_data = result.get("data")
            projected_data = _project_declared_data(
                tool.name,
                source_data,
                contract,
            )

            if contract.policy is ModelResultPolicy.ARTIFACT_REFERENCE:
                # A blocked/preparation result has no artifact yet. Require a
                # durable reference only when the handler claims completion;
                # otherwise preserve its declared prerequisite information.
                if status in {"ok", "ready", "success", "succeeded", "completed"} and (
                    not isinstance(source_data, Mapping)
                    or not any(source_data.get(field) for field in contract.reference_fields)
                ):
                    raise ToolResultProjectionError(
                        tool.name,
                        "成功的 artifact_reference 结果缺少持久化引用",
                    )
                if isinstance(source_data, Mapping) and contract.preview is not None:
                    preview_value, preview_metadata = _preview_value(
                        tool.name,
                        source_data,
                        contract.preview,
                    )
                    if preview_value is not None:
                        if not isinstance(projected_data, dict):
                            raise ToolResultProjectionError(
                                tool.name,
                                "artifact_reference 的 data 必须是对象",
                            )
                        projected_data[contract.preview.output_field] = preview_value
                        projected_data[f"{contract.preview.output_field}_meta"] = preview_metadata

            payload["data"] = projected_data

        content = _json_content(tool.name, payload)
        projected_bytes = len(content.encode("utf-8"))
        if projected_bytes > limit:
            raise ToolResultOverCapacity(
                tool.name,
                actual_bytes=projected_bytes,
                max_bytes=limit,
            )

        return ProjectedToolResult(
            tool_name=tool.name,
            policy=contract.policy,
            payload=payload,
            content=content,
            source_json_bytes=source_bytes,
            projected_json_bytes=projected_bytes,
            full_source_delivered=payload == _json_clone(tool.name, result),
        )


model_tool_result_projector = ModelToolResultProjector()


__all__ = [
    "MAX_MODEL_VISIBLE_TOOL_RESULT_BATCH_JSON_BYTES",
    "MAX_NATIVE_ASSISTANT_TRANSACTION_JSON_BYTES",
    "TOOL_CATEGORY_CONTROLLER_RESULT_CONTRACT",
    "ModelToolResultProjector",
    "ProjectedToolResult",
    "ToolResultBatchOverCapacity",
    "ToolResultOverCapacity",
    "ToolResultProjectionError",
    "admit_native_assistant_transaction",
    "admit_model_tool_result_batch",
    "declared_model_results_for_openai_tools",
    "declared_model_results_for_tool_names",
    "max_model_visible_result_tokens_for_open_tool_schemas",
    "max_model_visible_result_tokens_for_open_tools",
    "max_native_tool_transaction_wrapper_tokens",
    "model_tool_result_projector",
    "sanitize_diagnostic_tool_result",
]
