from __future__ import annotations

import pytest

from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
    ModelToolCapability,
    ToolProtocolValidator,
)


def _validate(messages):
    ToolProtocolValidator.validate(
        messages,
        capability=ModelToolCapability(supports_native_tool_calling=True),
        tools_enabled=True,
        current_user_message_id="current",
        checkpoint_message_id="checkpoint",
    )


def test_checkpoint_reference_must_precede_the_current_user() -> None:
    with pytest.raises(ConversationContextError) as caught:
        _validate(
            [
                {"message_id": "system", "role": "system", "content": "contract"},
                {"message_id": "current", "role": "user", "content": "latest"},
                {"message_id": "checkpoint", "role": "assistant", "content": "old data"},
            ]
        )

    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID


@pytest.mark.parametrize(
    "message",
    [
        {
            "message_id": "bad-assistant",
            "role": "assistant",
            "content": "",
            "tool_call_id": "call-1",
        },
        {"message_id": "bad-tool", "role": "tool", "content": "result"},
    ],
)
def test_tool_call_id_is_accepted_only_on_native_tool_results(message) -> None:
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            [{"role": "system", "content": "contract"}, message],
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )

    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        "",
        "not-json",
        "[]",
        '{"value":NaN}',
        '{"value":Infinity}',
        [],
        {"ok": True},
    ],
)
def test_native_tool_arguments_must_be_a_json_object_string(arguments) -> None:
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            [
                {"role": "system", "content": "contract"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read_project", "arguments": arguments},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            ],
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )

    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID


def test_native_tool_arguments_valid_json_object_remains_sendable() -> None:
    ToolProtocolValidator.validate(
        [
            {"role": "system", "content": "contract"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "read_project", "arguments": '{"id":"p1"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ],
        capability=ModelToolCapability(supports_native_tool_calling=True),
        tools_enabled=True,
    )
