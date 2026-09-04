"""Atomic native tool-call transaction state for one Agent turn."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .canonical import canonical_sha256, canonical_value
from .errors import ConversationContextError, ConversationContextErrorCode


class ToolTransactionState(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    CONSUMED = "consumed"
    COMPACTABLE = "compactable"


@dataclass(frozen=True)
class NativeToolCall:
    call_id: str
    name: str
    arguments_json: str

    def __post_init__(self) -> None:
        if not self.call_id or not self.name:
            raise ValueError("native tool call id and name are required")
        if not self.arguments_json:
            raise ValueError("native tool call arguments_json is required")

    def to_provider_dict(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments_json},
        }


@dataclass(frozen=True)
class NativeToolResult:
    call_id: str
    content: str
    result_ref: str | None = None
    persisted_step_id: str | None = None

    def __post_init__(self) -> None:
        if not self.call_id:
            raise ValueError("native tool result call_id is required")


@dataclass(frozen=True)
class ToolExecutionReceipt:
    """Server-authored replacement for a consumed native transaction."""

    step_id: str
    tool: str
    status: str
    summary: str
    resource_ids: tuple[str, ...]
    result_ref: str
    reread: str | None
    write_committed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_ids", tuple(self.resource_ids))
        if not self.step_id or not self.tool or not self.status or not self.result_ref:
            raise ValueError("tool execution receipt identity fields are required")
        if len(self.resource_ids) != len(set(self.resource_ids)):
            raise ValueError("tool execution receipt resource_ids must be unique")

    def to_dict(self) -> dict[str, Any]:
        return canonical_value(self)


@dataclass(frozen=True)
class ToolTransaction:
    transaction_id: str
    assistant_message_id: str
    assistant_content: str
    calls: tuple[NativeToolCall, ...]
    assistant_reasoning_content: str = ""
    assistant_provider_state: tuple[dict[str, Any], ...] = ()
    results: tuple[NativeToolResult, ...] = ()
    state: ToolTransactionState = ToolTransactionState.PENDING

    def __post_init__(self) -> None:
        object.__setattr__(self, "calls", tuple(self.calls))
        object.__setattr__(
            self,
            "assistant_provider_state",
            tuple(self.assistant_provider_state),
        )
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "state", ToolTransactionState(self.state))
        if not self.transaction_id or not self.assistant_message_id:
            raise ValueError("tool transaction identity is required")
        if not self.calls:
            raise ValueError("tool transaction must contain at least one call")
        call_ids = [call.call_id for call in self.calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("tool call IDs must be unique inside a transaction")
        result_ids = [result.call_id for result in self.results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("a tool call may have at most one result")
        if not set(result_ids).issubset(call_ids):
            raise ConversationContextError(
                ConversationContextErrorCode.ORPHAN_TOOL_RESULT,
                "工具结果没有对应的原生工具调用。",
            )
        if self.state is not ToolTransactionState.PENDING and set(result_ids) != set(call_ids):
            raise ConversationContextError(
                ConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION,
                "未收齐整批工具结果，不能进入后续事务状态。",
            )
        if self.state is ToolTransactionState.COMPACTABLE and any(
            not result.result_ref or not result.persisted_step_id for result in self.results
        ):
            raise ValueError("compactable transaction requires durable result references")

    @property
    def complete(self) -> bool:
        return {result.call_id for result in self.results} == {call.call_id for call in self.calls}

    @property
    def removable(self) -> bool:
        return self.state is ToolTransactionState.COMPACTABLE

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self)

    def add_result(self, result: NativeToolResult) -> ToolTransaction:
        if self.state is not ToolTransactionState.PENDING:
            raise ValueError("results may only be added to a pending transaction")
        if result.call_id not in {call.call_id for call in self.calls}:
            raise ConversationContextError(
                ConversationContextErrorCode.ORPHAN_TOOL_RESULT,
                "工具结果没有对应的原生工具调用。",
                details={"tool_call_id": result.call_id},
            )
        if result.call_id in {item.call_id for item in self.results}:
            raise ValueError("tool result already exists for call_id")
        return replace(self, results=(*self.results, result))

    def mark_delivered(self) -> ToolTransaction:
        if self.state is not ToolTransactionState.PENDING:
            raise ValueError("only a pending transaction can be delivered")
        if not self.complete:
            raise ConversationContextError(
                ConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION,
                "未收齐整批工具结果，不能继续请求模型。",
            )
        return replace(self, state=ToolTransactionState.DELIVERED)

    def mark_consumed(self) -> ToolTransaction:
        if self.state is not ToolTransactionState.DELIVERED:
            raise ValueError("only a delivered transaction can be consumed")
        return replace(self, state=ToolTransactionState.CONSUMED)

    def mark_compactable(self) -> ToolTransaction:
        if self.state is not ToolTransactionState.CONSUMED:
            raise ValueError("only a consumed transaction can become compactable")
        if any(not result.result_ref or not result.persisted_step_id for result in self.results):
            raise ValueError("durable RunStep/result references are required before compaction")
        return replace(self, state=ToolTransactionState.COMPACTABLE)

    def native_messages(self) -> tuple[dict[str, Any], ...]:
        """Return the original atomic assistant/tool protocol messages."""

        assistant = {
            "role": "assistant",
            "content": self.assistant_content,
            "tool_calls": [call.to_provider_dict() for call in self.calls],
        }
        if self.assistant_reasoning_content:
            assistant["reasoning_content"] = self.assistant_reasoning_content
        if self.assistant_provider_state:
            assistant["provider_state"] = list(self.assistant_provider_state)
        results_by_id = {result.call_id: result for result in self.results}
        tool_messages = [
            {
                "role": "tool",
                "tool_call_id": call.call_id,
                "content": results_by_id[call.call_id].content,
            }
            for call in self.calls
            if call.call_id in results_by_id
        ]
        return (assistant, *tool_messages)

    def to_dict(self, *, include_native_payload: bool = True) -> dict[str, Any]:
        if include_native_payload:
            return canonical_value(self)
        return {
            "transaction_id": self.transaction_id,
            "assistant_message_id": self.assistant_message_id,
            "call_ids": [call.call_id for call in self.calls],
            "tool_names": [call.name for call in self.calls],
            "result_refs": [result.result_ref for result in self.results],
            "persisted_step_ids": [result.persisted_step_id for result in self.results],
            "state": self.state.value,
        }


__all__ = [
    "NativeToolCall",
    "NativeToolResult",
    "ToolExecutionReceipt",
    "ToolTransaction",
    "ToolTransactionState",
]
