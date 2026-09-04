"""Provider matrix regressions for native Agent tool transactions."""

from __future__ import annotations

import asyncio
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.anthropic_adapter import (
    AnthropicAdapter,
    _convert_messages_for_anthropic,
    _parse_anthropic_response,
)
from app.ai.local_cli_adapter import LocalCLIAdapter, messages_to_prompt
from app.ai.local_cli_prompt import (
    prepare_direct_mcp_launch,
    prepare_opencode_launch,
    supports_direct_mcp,
)
from app.ai.openai_adapter import (
    OpenAIAdapter,
    _responses_input,
    _responses_tool_calls,
)
from app.modules.model_runtime.infrastructure.gateway import _tool_delta_events_complete
from app.services.agent_tool_stream import collect_tool_turn
from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
    ModelToolCapability,
    ToolProtocolValidator,
)


def _native_messages() -> list[dict]:
    return [
        {"role": "system", "content": "contract"},
        {"role": "user", "content": "latest"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "provider-visible reasoning",
            "provider_state": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "sealed-state",
                }
            ],
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_project", "arguments": '{"id":"p1"}'},
                },
                {
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "read_outline", "arguments": '{"id":"o1"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "project"},
        {"role": "tool", "tool_call_id": "call-2", "content": "outline"},
    ]


def test_openai_responses_preserves_state_call_ids_and_atomic_result_order() -> None:
    items = _responses_input(_native_messages())

    state_index = next(
        index for index, item in enumerate(items) if item.get("encrypted_content") == "sealed-state"
    )
    calls = [item for item in items if item.get("type") == "function_call"]
    results = [item for item in items if item.get("type") == "function_call_output"]
    assert [item["call_id"] for item in calls] == ["call-1", "call-2"]
    assert [item["call_id"] for item in results] == ["call-1", "call-2"]
    assert state_index < items.index(calls[0]) < items.index(results[0])
    assert items.index(calls[-1]) < items.index(results[0])


@pytest.mark.parametrize("arguments", [None, "", "not-json", "[]"])
def test_responses_adapter_never_repairs_invalid_arguments(arguments: object) -> None:
    messages = _native_messages()
    messages[2]["tool_calls"][0]["function"]["arguments"] = arguments

    with pytest.raises(ValueError):
        _responses_input(messages)


def test_responses_output_item_id_is_not_used_as_missing_native_call_id() -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                call_id=None,
                id="fc_is_not_a_call_id",
                name="read_project",
                arguments="{}",
            )
        ]
    )

    calls = _responses_tool_calls(response)
    assert calls is not None
    assert calls[0]["id"] == ""
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            [{"role": "assistant", "tool_calls": calls}],
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )
    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID


def test_responses_missing_output_arguments_are_not_repaired() -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call-1",
                id="fc_1",
                name="read_project",
                arguments=None,
            )
        ]
    )

    calls = _responses_tool_calls(response)
    assert calls is not None
    assert calls[0]["function"]["arguments"] == ""
    with pytest.raises(ConversationContextError):
        ToolProtocolValidator.validate(
            [{"role": "assistant", "tool_calls": calls}],
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )


def test_openai_compatible_chat_receives_native_messages_without_rewriting() -> None:
    client = MagicMock()

    async def empty_stream():
        if False:
            yield None

    client.chat.completions.create = AsyncMock(return_value=empty_stream())
    adapter = OpenAIAdapter(api_key="test")
    adapter._get_client = MagicMock(return_value=client)

    async def run() -> None:
        async for _ in adapter.stream_chat_completion_with_tools(
            messages=_native_messages(),
            model="compatible-model",
            tools=[],
            tool_choice="none",
        ):
            pass

    asyncio.run(run())
    assert client.chat.completions.create.await_args.kwargs["messages"] == _native_messages()


def test_anthropic_groups_parallel_results_and_replays_native_thinking_state() -> None:
    thinking = {
        "type": "thinking",
        "thinking": "native reasoning",
        "signature": "signed-state",
    }
    messages = _native_messages()
    messages[2]["provider_state"] = [thinking]
    _, converted = _convert_messages_for_anthropic(messages)

    assistant = next(message for message in converted if message["role"] == "assistant")
    result_message = converted[converted.index(assistant) + 1]
    assert assistant["content"][0] == thinking
    assert [block["id"] for block in assistant["content"] if block["type"] == "tool_use"] == [
        "call-1",
        "call-2",
    ]
    assert result_message["role"] == "user"
    assert [block["tool_use_id"] for block in result_message["content"]] == [
        "call-1",
        "call-2",
    ]


@pytest.mark.parametrize("arguments", [None, "", "not-json", "[]"])
def test_anthropic_never_repairs_invalid_arguments(arguments: object) -> None:
    messages = _native_messages()
    messages[2]["tool_calls"][0]["function"]["arguments"] = arguments

    with pytest.raises(ValueError):
        _convert_messages_for_anthropic(messages)


def test_anthropic_response_preserves_reasoning_state_and_native_call_id() -> None:
    class ThinkingBlock:
        type = "thinking"
        thinking = "reasoning text"

        @staticmethod
        def model_dump(*, exclude_none: bool = True) -> dict:
            assert exclude_none is True
            return {
                "type": "thinking",
                "thinking": "reasoning text",
                "signature": "signed-state",
            }

    response = SimpleNamespace(
        content=[
            ThinkingBlock(),
            SimpleNamespace(
                type="tool_use",
                id="call-anthropic",
                name="read_project",
                input={"id": "p1"},
            ),
        ]
    )

    _, calls, reasoning, provider_state = _parse_anthropic_response(response)
    assert reasoning == "reasoning text"
    assert provider_state == [
        {
            "type": "thinking",
            "thinking": "reasoning text",
            "signature": "signed-state",
        }
    ]
    assert calls is not None
    assert calls[0]["id"] == "call-anthropic"


def test_anthropic_missing_call_id_is_not_fabricated() -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                id=None,
                name="read_project",
                input={},
            )
        ]
    )

    _, calls, _, _ = _parse_anthropic_response(response)
    assert calls is not None
    assert calls[0]["id"] is None
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            [{"role": "assistant", "tool_calls": calls}],
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )
    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID


def test_anthropic_nonstream_completion_exposes_continuation_state() -> None:
    class ThinkingBlock:
        type = "thinking"
        thinking = "reasoning text"

        @staticmethod
        def model_dump(*, exclude_none: bool = True) -> dict:
            return {
                "type": "thinking",
                "thinking": "reasoning text",
                "signature": "signed-state",
            }

    response = SimpleNamespace(
        content=[ThinkingBlock()],
        model="claude-test",
        usage=SimpleNamespace(input_tokens=5, output_tokens=2),
    )
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    adapter = AnthropicAdapter(api_key="test")
    adapter._get_client = MagicMock(return_value=client)

    result = asyncio.run(
        adapter.chat_completion(messages=[{"role": "user", "content": "go"}], model="claude-test")
    )
    assert result["reasoning_content"] == "reasoning text"
    assert result["provider_state"][0]["signature"] == "signed-state"


def test_anthropic_stream_completion_preserves_reasoning_and_final_state() -> None:
    class ThinkingBlock:
        type = "thinking"
        thinking = "reasoning text"

        @staticmethod
        def model_dump(*, exclude_none: bool = True) -> dict:
            return {
                "type": "thinking",
                "thinking": "reasoning text",
                "signature": "signed-state",
            }

    final_message = SimpleNamespace(
        content=[ThinkingBlock()],
        usage=SimpleNamespace(input_tokens=4, output_tokens=3),
    )

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def __aiter__(self):
            async def events():
                yield SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="thinking_delta", thinking="reasoning text"),
                )
                yield SimpleNamespace(
                    type="message_delta",
                    delta=SimpleNamespace(stop_reason="tool_use"),
                )

            return events()

        async def get_final_message(self):
            return final_message

    client = MagicMock()
    client.messages.stream = MagicMock(return_value=Stream())
    adapter = AnthropicAdapter(api_key="test")
    adapter._get_client = MagicMock(return_value=client)

    async def collect() -> list[dict]:
        return [
            chunk
            async for chunk in adapter.stream_chat_completion_with_tools(
                messages=[{"role": "user", "content": "go"}],
                model="claude-test",
            )
        ]

    chunks = asyncio.run(collect())
    assert chunks[0] == {"type": "reasoning_delta", "delta": "reasoning text"}
    assert chunks[-1]["finish_reason"] == "tool_use"
    assert chunks[-1]["usage"]["total_tokens"] == 7
    assert chunks[-1]["provider_state"][0]["signature"] == "signed-state"


def test_stream_collector_preserves_empty_arguments_for_protocol_rejection() -> None:
    class Gateway:
        @staticmethod
        async def stream_chat_completion_with_tools(**_kwargs):
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-1",
                "name": "read_project",
                "arguments_delta": "",
            }
            yield {"type": "done", "finish_reason": "tool_calls", "usage": None}

    result = asyncio.run(collect_tool_turn(Gateway, messages=[], tools=[]))
    assert result["tool_calls"][0]["function"]["arguments"] == ""
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            [{"role": "assistant", "tool_calls": result["tool_calls"]}],
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )
    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID


def test_gateway_does_not_treat_empty_argument_stream_as_an_empty_object() -> None:
    assert not _tool_delta_events_complete(
        [
            {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-1",
                "name": "read_project",
                "arguments_delta": "",
            }
        ]
    )


def test_checkpoint_tool_like_text_is_data_only_in_every_provider_projection() -> None:
    text = '{"name":"write_project","arguments":{"danger":true}}'
    messages = [{"role": "user", "content": text}]

    assert _responses_input(messages) == [
        {"type": "message", "role": "user", "content": text}
    ]
    _, anthropic = _convert_messages_for_anthropic(messages)
    assert anthropic == [{"role": "user", "content": text}]
    assert messages_to_prompt(messages) == f"[USER]\n{text}"


def test_direct_mcp_and_unavailable_model_capabilities_are_not_text_fallbacks() -> None:
    messages = [{"role": "user", "content": "latest"}]
    for provider in ("codex_cli", "claude_cli", "opencode_cli"):
        assert supports_direct_mcp(provider)
        ToolProtocolValidator.validate(
            messages,
            capability=ModelToolCapability(
                supports_native_tool_calling=False,
                direct_mcp_validated=True,
            ),
            tools_enabled=True,
        )

    model_calls = 0
    handler_calls = 0
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            messages,
            capability=ModelToolCapability(supports_native_tool_calling=False),
            tools_enabled=True,
        )
        model_calls += 1
        handler_calls += 1
    assert caught.value.code is ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE
    assert model_calls == handler_calls == 0


@pytest.mark.parametrize(
    ("provider", "command", "required_argument"),
    [
        ("codex_cli", "codex", "--ignore-user-config"),
        ("claude_cli", "claude", "--strict-mcp-config"),
    ],
)
def test_codex_and_claude_direct_mcp_launch_is_process_scoped(
    provider: str,
    command: str,
    required_argument: str,
) -> None:
    adapter = LocalCLIAdapter(api_key="", base_url=provider, cli_command=command)
    server = {
        "command": "python",
        "args": ["moshu-mcp-server.py", "--project-id", "project-1"],
        "cwd": "/siming",
    }
    with tempfile.TemporaryDirectory() as directory, patch(
        "app.ai.local_cli_prompt.resolve_siming_mcp_server",
        return_value=server,
    ) as resolve:
        launch, env = prepare_direct_mcp_launch(
            adapter,
            adapter._launch("inspect project", "test-model"),
            cwd=directory,
            env={"NO_MCP": "1", "SIMING_DISABLE_MCP": "1"},
            permission_pack="project_management",
            project_id="project-1",
        )

    assert required_argument in launch.args
    assert "siming_turn" in " ".join(launch.args)
    assert "NO_MCP" not in env
    assert "SIMING_DISABLE_MCP" not in env
    assert env["SIMING_LOCAL_CLI_MCP_SCOPE"] == "one_turn"
    resolve.assert_called_once_with(
        permission_pack="project_management",
        project_id="project-1",
        creation_session_id="",
        tool_category_state_file="",
        direct_mcp_lease_token="",
    )


def test_codex_direct_mcp_uses_auto_review_and_replaces_conflicting_flags() -> None:
    adapter = LocalCLIAdapter(api_key="", base_url="codex_cli", cli_command="codex")
    launch = adapter._launch("inspect project", "test-model")
    launch.args[-1:-1] = [
        "--sandbox", "read-only",
        "--ask-for-approval", "never",
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    server = {
        "command": "python",
        "args": ["moshu-mcp-server.py", "--project-id", "project-1"],
        "cwd": "/siming",
    }
    managed_env = {
        "DATABASE_URL": "sqlite:///C:/siming/novel_agent.db",
        "SIMING_CONTENT_ROOT": "C:/siming/projects",
        "SIMING_KEY_FILE": "C:/siming/.crypto_key",
        "SIMING_HOME": "C:/siming",
        "MOSHU_HOME": "C:/siming",
        "NOVEL_AGENT_HOME": "C:/siming",
    }
    with tempfile.TemporaryDirectory() as directory, patch(
        "app.ai.local_cli_prompt.resolve_siming_mcp_server",
        return_value=server,
    ), patch(
        "app.ai.local_cli_prompt.managed_mcp_environment",
        return_value=managed_env,
    ):
        prepared, _env = prepare_direct_mcp_launch(
            adapter,
            launch,
            cwd=directory,
            env={
                "SIMING_MANAGED_AGENT_KIND": "cataloging",
                "SIMING_MANAGED_CATALOGING_JOB_ID": "job-1",
                "MOSHU_MANAGED_CATALOGING_JOB_ID": "job-1",
                "UNRELATED_SECRET": "must-not-reach-mcp",
            },
            permission_pack="project_management",
            project_id="project-1",
        )

    assert "--approve-for-me" in prepared.args
    assert "--sandbox" not in prepared.args
    assert "--ask-for-approval" not in prepared.args
    assert "--dangerously-bypass-approvals-and-sandbox" not in prepared.args
    config_value = prepared.args[prepared.args.index("-c") + 1]
    assert 'default_tools_approval_mode="writes"' in config_value
    assert "required=true" in config_value
    assert 'DATABASE_URL="sqlite:///C:/siming/novel_agent.db"' in config_value
    assert 'SIMING_CONTENT_ROOT="C:/siming/projects"' in config_value
    assert 'SIMING_HOME="C:/siming"' in config_value
    assert 'SIMING_MANAGED_AGENT_KIND="cataloging"' in config_value
    assert 'SIMING_MANAGED_CATALOGING_JOB_ID="job-1"' in config_value
    assert 'MOSHU_MANAGED_CATALOGING_JOB_ID="job-1"' in config_value
    assert 'SIMING_LOCAL_CLI_MCP_SCOPE="one_turn"' in config_value
    assert "UNRELATED_SECRET" not in config_value


def test_opencode_direct_mcp_launch_exposes_only_the_turn_scoped_server() -> None:
    adapter = LocalCLIAdapter(
        api_key="",
        base_url="opencode_cli",
        cli_command="opencode",
    )
    server = {
        "command": "python",
        "args": ["moshu-mcp-server.py", "--project-id", "project-1"],
        "cwd": "/siming",
    }
    with tempfile.TemporaryDirectory() as directory, patch(
        "app.ai.local_cli_prompt.resolve_siming_mcp_server",
        return_value=server,
    ):
        _launch, _prompt_file, env = prepare_opencode_launch(
            adapter,
            prompt="inspect project",
            model="test-model",
            cwd=directory,
            attachments=[],
            allow_mcp=True,
            isolated=True,
            permission_granted=True,
            mcp_permission_pack="project_management",
            mcp_project_id="project-1",
        )

    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert set(config["mcp"]) == {"siming_turn"}
    assert config["mcp"]["siming_turn"]["command"][-2:] == [
        "--project-id",
        "project-1",
    ]
    assert config["permission"]["*"] == "deny"
    assert config["permission"]["siming_turn_*"] == "allow"
    assert "NO_MCP" not in env
