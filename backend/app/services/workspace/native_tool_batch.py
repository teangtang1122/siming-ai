"""Strict validation for complete workspace native-tool batches."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.architecture.tool_categories import TOOL_CATEGORY_CONTROLLER


class NativeToolBatchValidationError(ValueError):
    """The provider batch is not safe to execute, even partially."""

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = {"reason": reason, **details}


@dataclass(frozen=True)
class ValidatedNativeToolBatch:
    calls: tuple[dict[str, Any], ...]
    arguments_by_call_id: dict[str, dict[str, Any]]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(str(call["function"]["name"]) for call in self.calls)


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
    for index, call in enumerate(calls):
        name = str(call["function"]["name"])
        if name not in allowed_tool_names:
            raise NativeToolBatchValidationError(
                "native_tool_not_open",
                "模型调用了本步骤未声明的原生工具，整批未执行。",
                call_index=index,
                call_id=str(call["id"]),
                tool=name,
                tools=[name],
            )
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
    cataloging = [name for name in names if "cataloging" in name]
    if singleton_cataloging_tools and cataloging and len(names) != 1:
        raise NativeToolBatchValidationError(
            "cataloging_tool_must_be_only_call",
            "建档状态工具必须是模型步骤中唯一的业务调用，整批未执行。",
            call_count=len(names),
            cataloging_tool=cataloging[0],
        )


__all__ = [
    "NativeToolBatchValidationError",
    "ValidatedNativeToolBatch",
    "validate_workspace_native_tool_batch",
]
