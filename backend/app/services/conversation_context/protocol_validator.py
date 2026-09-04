"""Request-time validation for native assistant/tool message protocols."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import ConversationContextError, ConversationContextErrorCode


@dataclass(frozen=True)
class ModelToolCapability:
    supports_native_tool_calling: bool
    direct_mcp_validated: bool = False

    @property
    def agent_tools_available(self) -> bool:
        return self.supports_native_tool_calling or self.direct_mcp_validated


class ToolProtocolValidator:
    """Reject malformed tool protocols before a provider request is sent.

    Ordinary assistant text is never inspected for JSON or tool names.  That
    omission is intentional: native tool calls and validated process-level MCP
    are the only executable channels.
    """

    @classmethod
    def validate(
        cls,
        messages: Sequence[Mapping[str, Any]],
        *,
        capability: ModelToolCapability,
        tools_enabled: bool,
        current_user_message_id: str | None = None,
        checkpoint_message_id: str | None = None,
    ) -> None:
        if tools_enabled and not capability.agent_tools_available:
            raise ConversationContextError(
                ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE,
                "当前模型既不支持原生工具调用，也没有已验证的 Direct MCP。",
            )

        system_indexes = [
            index for index, message in enumerate(messages) if message.get("role") == "system"
        ]
        if len(system_indexes) > 1 or (system_indexes and system_indexes[0] != 0):
            cls._invalid("system message 必须至多一条且位于消息序列开头")

        seen_call_ids: set[str] = set()
        active_batch: set[str] | None = None
        active_results: set[str] = set()
        message_ids: dict[str, tuple[int, Mapping[str, Any]]] = {}
        user_positions: list[tuple[int, str]] = []

        for index, message in enumerate(messages):
            role = str(message.get("role") or "")
            if role not in {"system", "user", "assistant", "tool"}:
                cls._invalid(f"不支持的消息角色: {role or '<empty>'}")
            message_id = str(message.get("message_id") or "")
            if message_id:
                if message_id in message_ids:
                    cls._invalid(f"重复的 message_id: {message_id}")
                message_ids[message_id] = (index, message)
            if role == "user":
                user_positions.append((index, message_id))

            raw_calls = message.get("tool_calls") or []
            if raw_calls and role != "assistant":
                cls._invalid("只有 assistant 消息可以包含 tool_calls")
            tool_call_id = str(message.get("tool_call_id") or "")
            if tool_call_id and role != "tool":
                cls._invalid("只有 tool 消息可以包含 tool_call_id")
            if role == "tool" and not tool_call_id:
                cls._invalid("tool 消息必须包含非空 tool_call_id")

            if role == "assistant" and raw_calls:
                if not capability.supports_native_tool_calling:
                    raise ConversationContextError(
                        ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE,
                        "请求包含原生 tool_calls，但绑定模型不支持该协议。",
                    )
                if active_batch is not None and active_results != active_batch:
                    cls._incomplete(active_batch - active_results)
                batch: set[str] = set()
                for raw_call in raw_calls:
                    call_id, name = cls._tool_call_identity(raw_call)
                    if not call_id or not name:
                        cls._invalid("assistant tool call 的 ID 和函数名不能为空")
                    arguments = cls._tool_call_arguments(raw_call)
                    if not isinstance(arguments, str):
                        cls._invalid("assistant tool call 的 arguments 必须是 JSON 字符串")
                    try:
                        parsed_arguments = json.loads(
                            arguments,
                            parse_constant=cls._reject_non_json_constant,
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        cls._invalid("assistant tool call 的 arguments 必须是合法 JSON 对象")
                    if not isinstance(parsed_arguments, dict):
                        cls._invalid("assistant tool call 的 arguments 必须是合法 JSON 对象")
                    if call_id in seen_call_ids or call_id in batch:
                        cls._invalid(f"重复的 tool_call_id: {call_id}")
                    batch.add(call_id)
                    seen_call_ids.add(call_id)
                active_batch = batch
                active_results = set()
                continue

            if role == "tool":
                if not capability.supports_native_tool_calling:
                    raise ConversationContextError(
                        ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE,
                        "请求包含原生 tool 结果，但绑定模型不支持该协议。",
                    )
                call_id = tool_call_id
                if not call_id or active_batch is None or call_id not in active_batch:
                    raise ConversationContextError(
                        ConversationContextErrorCode.ORPHAN_TOOL_RESULT,
                        "tool 消息没有对应的当前 assistant tool call。",
                        details={"tool_call_id": call_id or None},
                    )
                if call_id in active_results:
                    cls._invalid(f"tool_call_id {call_id} 存在多个结果")
                active_results.add(call_id)
                continue

            if active_batch is not None:
                if active_results != active_batch:
                    cls._incomplete(active_batch - active_results)
                active_batch = None
                active_results = set()

        if active_batch is not None and active_results != active_batch:
            cls._incomplete(active_batch - active_results)

        if current_user_message_id is not None:
            current = message_ids.get(current_user_message_id)
            if current is None or current[1].get("role") != "user":
                cls._invalid("最新用户消息必须以独立 user 消息存在")
            later_user_ids = [
                item_id for position, item_id in user_positions if position > current[0]
            ]
            if later_user_ids:
                cls._invalid("最新用户消息之后不能出现另一个用户意图消息")
        if checkpoint_message_id is not None:
            checkpoint = message_ids.get(checkpoint_message_id)
            if checkpoint is None:
                cls._invalid("checkpoint 历史参考消息不存在")
            if checkpoint[1].get("role") == "tool":
                cls._invalid("checkpoint 不能映射为 tool role")
            if checkpoint_message_id == current_user_message_id:
                cls._invalid("checkpoint 不能与最新用户消息合并")
            if current_user_message_id is not None:
                current = message_ids.get(current_user_message_id)
                if current is not None and checkpoint[0] >= current[0]:
                    cls._invalid("checkpoint 历史参考必须位于最新用户消息之前")

    @staticmethod
    def _tool_call_identity(raw_call: Any) -> tuple[str, str]:
        if not isinstance(raw_call, Mapping):
            return "", ""
        call_id = str(raw_call.get("id") or raw_call.get("call_id") or "")
        function = raw_call.get("function")
        if isinstance(function, Mapping):
            name = str(function.get("name") or "")
        else:
            name = str(raw_call.get("name") or "")
        return call_id, name

    @staticmethod
    def _tool_call_arguments(raw_call: Any) -> Any:
        if not isinstance(raw_call, Mapping):
            return None
        function = raw_call.get("function")
        if isinstance(function, Mapping):
            return function.get("arguments")
        return raw_call.get("arguments")

    @staticmethod
    def _reject_non_json_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    @staticmethod
    def _invalid(message: str) -> None:
        raise ConversationContextError(
            ConversationContextErrorCode.PROTOCOL_INVALID,
            message,
        )

    @staticmethod
    def _incomplete(missing: Iterable[str]) -> None:
        raise ConversationContextError(
            ConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION,
            "工具调用批次尚未收到完整结果，不能继续请求模型。",
            details={"missing_tool_call_ids": sorted(missing)},
        )


__all__ = ["ModelToolCapability", "ToolProtocolValidator"]
