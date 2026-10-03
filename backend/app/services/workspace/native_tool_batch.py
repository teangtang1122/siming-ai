"""Strict validation for complete workspace native-tool batches."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.architecture.tool_categories import CATALOGING_TOOL_CATEGORIES, TOOL_CATEGORY_CONTROLLER


class NativeToolBatchValidationError(ValueError):
    """The provider batch is not safe to execute, even partially."""

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = {"reason": reason, **details}


MAX_NATIVE_TOOL_NAME_REJECTIONS = 3


class NativeToolBatchNotOpen(NativeToolBatchValidationError):
    """Structurally valid calls retained only to receipt a whole-batch denial."""

    def __init__(self, calls: list[dict[str, Any]], allowed_tool_names: set[str] | frozenset[str]) -> None:
        unavailable = [call for call in calls if call["function"]["name"] not in allowed_tool_names]
        first = unavailable[0]
        super().__init__(
            "native_tool_not_open",
            "模型调用了本步骤未开放的工具，整批未执行。",
            call_index=calls.index(first),
            call_id=first["id"],
            tool=first["function"]["name"],
            tools=list(dict.fromkeys(call["function"]["name"] for call in unavailable)),
        )
        self.calls = calls
        self.allowed_tool_names = sorted(allowed_tool_names)
        self.recovery_fits = False

    def model_error_result(self, tool_name: str) -> dict[str, Any]:
        category_hint = (
            "需要其他能力时单独调用 set_tool_categories。"
            if TOOL_CATEGORY_CONTROLLER in self.allowed_tool_names else ""
        )
        return {
            "tool": tool_name,
            "status": "error",
            "detail": (
                "本批次含有当前未开放的工具，所有调用均未执行。请根据本步骤实际提供的工具名称与"
                f"参数 Schema 重新选择调用；{category_hint}"
                "不要猜测工具别名，也不要把同批其他调用当作已成功。"
            ),
            "data": {
                "reason": self.reason,
                "unavailable_tools": self.details["tools"],
                "available_tools": self.allowed_tool_names,
                "batch_call_count": len(self.calls),
                "executed": False,
                "retryable": self.recovery_fits,
            },
        }


@dataclass(frozen=True)
class ValidatedNativeToolBatch:
    calls: tuple[dict[str, Any], ...]
    arguments_by_call_id: dict[str, dict[str, Any]]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(str(call["function"]["name"]) for call in self.calls)


def is_cataloging_mutation(definition: Any | None) -> bool:
    """Read-only preparation must not inherit cataloging control boundaries."""
    return (
        getattr(definition, "agent_category", "") in CATALOGING_TOOL_CATEGORIES
        and getattr(definition, "tool_type", "read") != "read"
    )


def validate_workspace_native_tool_batch(
    raw_calls: list[Any],
    *,
    allowed_tool_names: set[str] | frozenset[str],
    resolve_tool: Callable[[str], Any | None],
    require_initial_controller: bool,
    singleton_cataloging_tools: bool = True,
) -> ValidatedNativeToolBatch:
    """Validate every native call before returning any executable arguments."""

    calls: list[dict[str, Any]] = []
    arguments_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_call in enumerate(raw_calls):
        call, arguments = _validate_one(raw_call, index, arguments_by_id)
        calls.append(call)
        arguments_by_id[str(call["id"])] = arguments
    names = [str(call["function"]["name"]) for call in calls]
    _validate_batch_semantics(
        names,
        resolve_tool=resolve_tool,
        require_initial_controller=require_initial_controller,
        singleton_cataloging_tools=singleton_cataloging_tools,
    )
    if any(name not in allowed_tool_names for name in names):
        raise NativeToolBatchNotOpen(calls, allowed_tool_names)
    return ValidatedNativeToolBatch(tuple(calls), arguments_by_id)


def _validate_one(
    raw_call: Any,
    index: int,
    prior_arguments: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw_call, dict) or not isinstance(raw_call.get("function"), dict):
        raise NativeToolBatchValidationError(
            "invalid_native_tool_call",
            "模型返回了缺少 function 对象的原生调用，整批未执行。",
            call_index=index,
        )
    function = raw_call["function"]
    name = str(function.get("name") or "").strip()
    call_id = str(raw_call.get("id") or "").strip()
    if not name:
        raise NativeToolBatchValidationError(
            "native_tool_name_missing",
            "模型返回了缺少工具名称的原生调用，整批未执行。",
            call_index=index,
        )
    if not call_id:
        raise NativeToolBatchValidationError(
            "native_tool_call_id_missing",
            "模型返回了缺少原生 call_id 的工具调用，未执行任何工具。",
            call_index=index,
            tool=name,
        )
    if call_id in prior_arguments:
        raise NativeToolBatchValidationError(
            "duplicate_native_tool_call_id",
            "模型在同一原生工具批次中重复使用 call_id，整批未执行。",
            call_index=index,
            call_id=call_id,
        )
    raw_arguments = function.get("arguments")
    if not isinstance(raw_arguments, str):
        raise NativeToolBatchValidationError(
            "native_tool_arguments_not_json_string",
            "原生工具 arguments 必须是 JSON 字符串，整批未执行。",
            call_index=index,
            call_id=call_id,
            tool=name,
        )
    if not raw_arguments.strip():
        raise NativeToolBatchValidationError(
            "native_tool_arguments_empty",
            "模型返回了空的原生工具 arguments，整批未执行。",
            call_index=index,
            call_id=call_id,
            tool=name,
        )
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise NativeToolBatchValidationError(
            "invalid_native_tool_arguments_json",
            "模型返回了无效的原生工具 arguments JSON，整批未执行。",
            call_index=index,
            call_id=call_id,
            tool=name,
        ) from exc
    if not isinstance(arguments, dict):
        raise NativeToolBatchValidationError(
            "native_tool_arguments_not_object",
            "原生工具 arguments 必须是 JSON 对象，整批未执行。",
            call_index=index,
            call_id=call_id,
            tool=name,
        )
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": raw_arguments},
    }, arguments


def _validate_batch_semantics(
    names: list[str],
    *,
    resolve_tool: Callable[[str], Any | None],
    require_initial_controller: bool,
    singleton_cataloging_tools: bool,
) -> None:
    if TOOL_CATEGORY_CONTROLLER in names and len(names) != 1:
        raise NativeToolBatchValidationError(
            "category_controller_must_be_only_call",
            "set_tool_categories 必须是模型步骤中唯一的原生调用，整批未执行。",
            call_count=len(names),
        )
    if require_initial_controller and names and TOOL_CATEGORY_CONTROLLER not in names:
        raise NativeToolBatchValidationError(
            "initial_category_controller_required",
            "模型没有调用本步骤唯一开放的 set_tool_categories，整批未执行。",
            tools=names,
        )
    terminal_names = [
        name
        for name in names
        if bool(getattr(resolve_tool(name), "ends_agent_turn", False))
    ]
    if terminal_names and len(names) != 1:
        raise NativeToolBatchValidationError(
            "draft_tool_must_be_only_call",
            "终止当前回合的草稿工具必须是模型步骤中唯一的业务调用，整批未执行。",
            call_count=len(names),
            draft_tool=terminal_names[0],
        )
    cataloging = [name for name in names if is_cataloging_mutation(resolve_tool(name))]
    if singleton_cataloging_tools and cataloging and len(names) != 1:
        raise NativeToolBatchValidationError(
            "cataloging_tool_must_be_only_call",
            "建档写入或控制工具必须是模型步骤中唯一的业务调用，整批未执行。",
            call_count=len(names),
            cataloging_tool=cataloging[0],
        )


__all__ = [
    "MAX_NATIVE_TOOL_NAME_REJECTIONS",
    "NativeToolBatchNotOpen",
    "NativeToolBatchValidationError",
    "ValidatedNativeToolBatch",
    "validate_workspace_native_tool_batch",
]
