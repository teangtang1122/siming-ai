"""Workspace implementation of the scheduler's task-runner port."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ...architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    TOOL_CATEGORY_METADATA,
    normalize_tool_categories,
    tool_category_controller_schema,
    tool_names_for_categories,
)
from ...database.models import ScheduledTask
from ...modules.model_runtime.application.execution import model_executor as LLMGateway
from ..agent_tool_stream import collect_tool_turn
from . import executor as workspace_executor
from .native_tool_batch import (
    NativeToolBatchValidationError,
    ValidatedNativeToolBatch,
    validate_workspace_native_tool_batch,
)
from .registry import registry
from .run_step_payloads import serialize_step_result
from .tool_result_projection import (
    ToolResultBatchOverCapacity,
    ToolResultProjectionError,
    admit_native_assistant_transaction,
    declared_model_results_for_tool_names,
    model_tool_result_projector,
)
from .tool_schemas import build_workspace_tool_schemas
from .turn_control import is_terminal_tool_result, terminal_reply

logger = logging.getLogger(__name__)


def _authorized_tool_names(task: ScheduledTask) -> set[str]:
    names = {tool.name for tool in registry.list_for_scheduler()}
    raw_policy = task.tool_policy if isinstance(task.tool_policy, list) else []
    policy = {str(name).strip() for name in raw_policy if str(name).strip()}
    return names & policy if raw_policy else names


def _active_tool_names(
    authorized_names: set[str],
    active_categories: tuple[str, ...],
) -> set[str]:
    return authorized_names & set(tool_names_for_categories(active_categories))


def _tool_schemas(
    authorized_names: set[str],
    active_categories: tuple[str, ...],
) -> list[dict[str, Any]]:
    return [
        tool_category_controller_schema(),
        *build_workspace_tool_schemas(
            sorted(_active_tool_names(authorized_names, active_categories))
        ),
    ]


def _category_result(
    arguments: dict[str, Any],
    authorized_names: set[str],
) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    try:
        categories = normalize_tool_categories(arguments.get("enabled_categories"))
    except ValueError:
        return {
            "tool": TOOL_CATEGORY_CONTROLLER,
            "status": "error",
            "detail": "工具类别参数无效，未切换能力。",
            "data": None,
        }, None
    labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in categories]
    available = _active_tool_names(authorized_names, categories)
    detail = (
        f"已准备{'、'.join(labels)}能力，共 {len(available)} 项定时任务工具"
        if labels
        else "已关闭全部业务工具"
    )
    return {
        "tool": TOOL_CATEGORY_CONTROLLER,
        "status": "ok",
        "detail": detail,
        "data": {
            "enabled_categories": list(categories),
            "available_tool_count": len(available),
        },
    }, categories


def _tool_message(tool_call: dict[str, Any], result: dict[str, Any]) -> dict[str, str]:
    function = tool_call.get("function")
    tool_name = str(function.get("name") or "") if isinstance(function, dict) else ""
    tool = registry.get(tool_name)
    if tool is None:
        # ``set_tool_categories`` is a small, deterministic controller result
        # rather than a registered business-tool payload.
        content = serialize_step_result(result)
    else:
        try:
            content = model_tool_result_projector.project(tool, result).content
        except ToolResultProjectionError as exc:
            content = serialize_step_result(exc.model_error_result())
    return {
        "role": "tool",
        "tool_call_id": str(tool_call.get("id") or ""),
        "content": content,
    }


def _validated_native_turn(
    result: dict[str, Any],
    *,
    allowed_tool_names: set[str],
    require_initial_controller: bool,
) -> tuple[str, ValidatedNativeToolBatch]:
    content = str(result.get("content") or "")
    raw_calls = (
        list(result.get("tool_calls") or []) if isinstance(result.get("tool_calls"), list) else []
    )
    try:
        batch = validate_workspace_native_tool_batch(
            raw_calls,
            allowed_tool_names=allowed_tool_names,
            resolve_tool=registry.get,
            require_initial_controller=require_initial_controller,
        )
    except NativeToolBatchValidationError as exc:
        raise RuntimeError(f"conversation_protocol_invalid:{exc.reason}") from exc
    if not batch.calls:
        return content, batch
    assistant_payload: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "tool_calls": list(batch.calls),
    }
    reasoning = str(result.get("reasoning_content") or "")
    if reasoning:
        assistant_payload["reasoning_content"] = reasoning
    provider_state = result.get("provider_state")
    if isinstance(provider_state, list) and provider_state:
        assistant_payload["provider_state"] = provider_state
    try:
        declared = declared_model_results_for_tool_names(
            batch.names,
            resolve_tool=registry.get,
        )
        admit_native_assistant_transaction(assistant_payload, declared)
    except ToolResultBatchOverCapacity as exc:
        raise RuntimeError(f"conversation_protocol_invalid:{exc.reason}") from exc
    except ValueError as exc:
        raise RuntimeError("conversation_protocol_invalid:native_tool_contract_invalid") from exc
    return content, batch


def run_workspace_scheduled_task(db: Session, task: ScheduledTask) -> str:
    """Run one scheduled prompt through the normal workspace tool chain."""
    system_parts = [
        "你是一个定时任务执行助手。请根据用户的提示完成任务。",
        (
            "第一模型步骤只开放 set_tool_categories，必须先调用它选择所需能力；"
            "类别从下一模型步骤生效，调用控制工具后当前步骤立即结束。"
        ),
        "只调用本步骤实际提供的工具；工具结果失败时不得声称任务已完成。",
    ]
    if isinstance(task.tool_policy, list) and task.tool_policy:
        policy_names = [str(name).strip() for name in task.tool_policy]
        system_parts.append(f"本任务授权工具：{', '.join(policy_names)}")

    messages = [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": task.prompt},
    ]

    authorized_names = _authorized_tool_names(task)

    async def run_agent_loop() -> str:
        active_categories: tuple[str, ...] = ()
        category_selected = False
        while True:
            result = await collect_tool_turn(
                LLMGateway,
                messages=messages,
                tools=_tool_schemas(authorized_names, active_categories),
                tool_choice="required" if not category_selected else "auto",
                model=None,
                temperature=0.3,
                max_tokens=4000,
                timeout=120,
            )
            content, batch = _validated_native_turn(
                result,
                allowed_tool_names={
                    TOOL_CATEGORY_CONTROLLER,
                    *_active_tool_names(authorized_names, active_categories),
                },
                require_initial_controller=not category_selected,
            )
            tool_calls = list(batch.calls)
            if not tool_calls and not category_selected:
                raise RuntimeError(
                    "模型没有调用本步骤唯一开放的 set_tool_categories，定时任务已停止"
                )

            if not tool_calls:
                return content.strip() or "任务执行完成"

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
            )

            for tool_call in tool_calls:
                function = tool_call.get("function") if isinstance(tool_call, dict) else None
                tool_name = str(function.get("name") or "") if isinstance(function, dict) else ""
                arguments = batch.arguments_by_call_id[str(tool_call["id"])]
                if tool_name == TOOL_CATEGORY_CONTROLLER:
                    tool_result, replacement = _category_result(arguments, authorized_names)
                    if replacement is not None:
                        active_categories = replacement
                        category_selected = True
                else:
                    tool_result = await workspace_executor.execute_workspace_action(
                        db,
                        task.project_id,
                        {"tool": tool_name, "arguments": arguments},
                    )
                messages.append(_tool_message(tool_call, tool_result))

                definition = registry.get(tool_name)
                if (
                    definition is not None
                    and definition.ends_agent_turn
                    and is_terminal_tool_result(tool_result)
                ):
                    return terminal_reply(tool_result)

                if tool_name == TOOL_CATEGORY_CONTROLLER:
                    break

    try:
        return asyncio.run(run_agent_loop())
    except Exception as exc:
        raw = str(exc)
        if raw.startswith("conversation_protocol_invalid:"):
            reason = raw.split(":", 2)[1]
            raise RuntimeError(
                f"Agent execution failed: conversation_protocol_invalid:{reason}"
            ) from exc
        error_id = uuid.uuid4().hex
        logger.exception(
            "Scheduled workspace agent failed error_id=%s task=%s type=%s",
            error_id,
            getattr(task, "id", None),
            type(exc).__name__,
        )
        raise RuntimeError(f"Agent execution failed: server_error:{error_id}") from exc


__all__ = ["run_workspace_scheduled_task"]
