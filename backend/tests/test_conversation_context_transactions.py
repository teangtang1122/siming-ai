"""Atomic tool transaction and native protocol regression tests."""

import pytest

from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
    ModelToolCapability,
    NativeToolCall,
    NativeToolResult,
    ToolProtocolValidator,
    ToolTransaction,
    ToolTransactionState,
)


def _open_transaction() -> ToolTransaction:
    return ToolTransaction(
        transaction_id="tx-1",
        assistant_message_id="assistant-tools-1",
        assistant_content="",
        calls=(
            NativeToolCall("call-1", "search_chapters", '{"query":"城门"}'),
            NativeToolCall("call-2", "read_outline", '{"id":"outline-1"}'),
        ),
    )


def test_tool_transaction_requires_complete_batch_before_delivery() -> None:
    transaction = _open_transaction().add_result(NativeToolResult("call-1", "候选章节"))

    with pytest.raises(ConversationContextError) as caught:
        transaction.mark_delivered()
    assert caught.value.code is ConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION


def test_tool_transaction_only_becomes_removable_after_consumed_and_persisted() -> None:
    transaction = _open_transaction()
    transaction = transaction.add_result(
        NativeToolResult(
            "call-1",
            "候选章节",
            result_ref="assistant_run_step:step-1",
            persisted_step_id="step-1",
        )
    ).add_result(
        NativeToolResult(
            "call-2",
            "大纲原文",
            result_ref="assistant_run_step:step-2",
            persisted_step_id="step-2",
        )
    )

    assert transaction.state is ToolTransactionState.PENDING
    assert transaction.removable is False
    transaction = transaction.mark_delivered()
    assert transaction.removable is False
    transaction = transaction.mark_consumed()
    assert transaction.removable is False
    transaction = transaction.mark_compactable()
    assert transaction.removable is True
    assert [message["role"] for message in transaction.native_messages()] == [
        "assistant",
        "tool",
        "tool",
    ]


def test_tool_transaction_preserves_provider_continuation_state_exactly_once() -> None:
    transaction = ToolTransaction(
        transaction_id="tx-provider",
        assistant_message_id="assistant-provider",
        assistant_content="",
        calls=(NativeToolCall("call-provider", "search_outline", "{}"),),
        assistant_reasoning_content="private continuation token",
        assistant_provider_state=({"type": "reasoning", "id": "reasoning-1"},),
    ).add_result(
        NativeToolResult("call-provider", '{"status":"ok"}')
    ).mark_delivered()

    native = transaction.native_messages()
    assert native[0]["reasoning_content"] == "private continuation token"
    assert native[0]["provider_state"] == [
        {"type": "reasoning", "id": "reasoning-1"}
    ]
    assert native[1]["tool_call_id"] == "call-provider"


def test_orphan_tool_result_is_rejected() -> None:
    with pytest.raises(ConversationContextError) as caught:
        _open_transaction().add_result(NativeToolResult("unknown", "bad"))
    assert caught.value.code is ConversationContextErrorCode.ORPHAN_TOOL_RESULT


def test_protocol_validator_accepts_complete_native_batch() -> None:
    messages = [
        {"message_id": "system", "role": "system", "content": "contract"},
        {"message_id": "history", "role": "user", "content": "history data"},
        {"message_id": "current", "role": "user", "content": "继续"},
        {
            "message_id": "assistant-tools",
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "set_tool_categories", "arguments": "{}"},
                }
            ],
        },
        {"message_id": "result", "role": "tool", "tool_call_id": "call-1", "content": "ok"},
    ]

    ToolProtocolValidator.validate(
        messages,
        capability=ModelToolCapability(supports_native_tool_calling=True),
        tools_enabled=True,
        current_user_message_id="current",
        checkpoint_message_id="history",
    )


def test_protocol_validator_does_not_parse_tool_like_checkpoint_text() -> None:
    messages = [
        {"message_id": "system", "role": "system", "content": "contract"},
        {
            "message_id": "history",
            "role": "user",
            "content": '{"name":"chapter_writer","arguments":{"bad":true}}',
        },
        {"message_id": "current", "role": "user", "content": "讨论，不执行"},
    ]

    ToolProtocolValidator.validate(
        messages,
        capability=ModelToolCapability(supports_native_tool_calling=True),
        tools_enabled=True,
        current_user_message_id="current",
        checkpoint_message_id="history",
    )


def test_protocol_validator_rejects_split_batch_and_late_system() -> None:
    split = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "call-1", "function": {"name": "search", "arguments": "{}"}}],
        },
        {"role": "assistant", "content": "继续"},
    ]
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            split,
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )
    assert caught.value.code is ConversationContextErrorCode.INCOMPLETE_TOOL_TRANSACTION

    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            [{"role": "user", "content": "x"}, {"role": "system", "content": "late"}],
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )
    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID


def test_models_without_native_tools_never_fall_back_to_text_protocol() -> None:
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            [{"role": "user", "content": "调用工具"}],
            capability=ModelToolCapability(supports_native_tool_calling=False),
            tools_enabled=True,
        )
    assert caught.value.code is ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE

    ToolProtocolValidator.validate(
        [{"role": "user", "content": "CLI 通过进程 MCP"}],
        capability=ModelToolCapability(
            supports_native_tool_calling=False,
            direct_mcp_validated=True,
        ),
        tools_enabled=True,
    )
