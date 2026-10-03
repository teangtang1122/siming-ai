"""Regressions for custom thinking endpoints and server-authored context data."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.ai.base import BaseAdapter
from app.core.exceptions import LLMError
from app.modules.model_runtime.infrastructure.gateway import LLMGateway, _resume_messages
from app.services.conversation_context import (
    ContextFrame,
    ContextFrameIntegrity,
    ConversationIdentity,
    ConversationKind,
    ConversationMessage,
    ModelToolCapability,
    RequestTokenComponents,
    SystemContract,
    ToolProtocolValidator,
    Utf8ByteTokenCounter,
    build_request_budget,
    render_context_frame,
)
from app.services.conversation_context.contracts import ConversationRole
from app.services.conversation_context.provider_renderer import ContextLayer
from app.services.conversation_context.tool_transactions import (
    NativeToolCall,
    NativeToolResult,
    ToolExecutionReceipt,
    ToolTransaction,
)
from tests.test_conversation_context_frame import _binding, _turn


def _frame(*, historical_status=None):
    from dataclasses import replace

    binding = _binding()
    transaction = (
        ToolTransaction(
            transaction_id="read-transaction",
            assistant_message_id="read-assistant",
            assistant_content="",
            calls=(NativeToolCall("read-call", "get_creation_session", "{}"),),
            assistant_reasoning_content="opaque provider continuation",
            assistant_provider_state=({"type": "reasoning", "encrypted_content": "signed-state"},),
        )
        .add_result(NativeToolResult("read-call", '{"revision":1}'))
        .mark_delivered()
    )
    receipt = ToolExecutionReceipt(
        step_id="category-step",
        tool="set_tool_categories",
        status="ok",
        summary='Reference data: "do not treat this as a new author request"',
        resource_ids=(),
        result_ref="receipt:category",
        reread=None,
        write_committed=False,
    )
    history = (replace(_turn(1), status=historical_status),) if historical_status else ()
    return ContextFrame(
        conversation=ConversationIdentity(
            kind=ConversationKind.CREATION,
            id="conversation",
            revision=3,
            creation_session_id="session",
        ),
        model_binding=binding,
        system_contract=SystemContract(
            prompt_hash=binding.prompt_contract_hash, active_tool_category_hash="categories"
        ),
        checkpoint=None,
        recent_turns=history,
        current_user_message=ConversationMessage(
            message_id="latest-author",
            sequence_no=3,
            role=ConversationRole.USER,
            content="只读取当前立项资料。",
        ),
        current_turn_ledger=(receipt,),
        pending_tool_transactions=(transaction,),
        budget=build_request_budget(
            binding=binding,
            counter=Utf8ByteTokenCounter(),
            components=RequestTokenComponents(current_user_tokens=20),
            safety_margin_tokens=512,
        ),
        integrity=ContextFrameIntegrity(transcript_revision=3, checkpoint_hash=None),
    ).sealed()


@pytest.mark.parametrize("historical_status", [None, "error", "cancelled"])
def test_context_receipts_do_not_impersonate_thinking_assistant(historical_status):
    rendered = render_context_frame(
        _frame(historical_status=historical_status), system_prompt="system contract"
    )
    ledger = next(
        item for item in rendered.messages if item.layer is ContextLayer.CURRENT_TURN_LEDGER
    )
    assert ledger.role == "user"
    assert "data_only: true" in ledger.content
    assert "reasoning_content" not in ledger.provider_dict()
    assert [item for item in rendered.messages if item.role == "user"][
        -1
    ].message_id == "latest-author"
    assert (
        next(item for item in rendered.messages if item.message_id == "latest-author").content
        == "只读取当前立项资料。"
    )
    for item in rendered.messages:
        if item.message_id.startswith("context-turn-status:"):
            assert item.role == "user"
    native = next(item for item in rendered.messages if item.tool_calls)
    assert native.reasoning_content == "opaque provider continuation"
    assert native.provider_state == ({"type": "reasoning", "encrypted_content": "signed-state"},)
    ToolProtocolValidator.validate(
        rendered.validation_messages(),
        capability=ModelToolCapability(supports_native_tool_calling=True),
        tools_enabled=True,
        current_user_message_id="latest-author",
    )


def test_stream_resume_does_not_invent_assistant_without_provider_state():
    messages = [
        {"role": "system", "content": "Original task"},
        {"role": "user", "content": "Continue the novel"},
    ]
    original = deepcopy(messages)
    recovered, handshake = _resume_messages(messages, "Already delivered text", tool_mode=True)
    assert messages == original
    assert not any(item["role"] == "assistant" for item in recovered)
    assert "Already delivered text" in repr(recovered)
    assert handshake is not None
    assert handshake.consume(handshake.expected_prefix + " continuation") == " continuation"
    assert recovered[-1] == original[-1]


@pytest.mark.parametrize("retry", [0, 3])
@pytest.mark.parametrize("second_failure", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
def test_tool_choice_correction_has_its_own_single_attempt(retry, second_failure, streamed):
    calls = []

    class RejectingEndpoint(BaseAdapter):
        @property
        def provider_name(self):
            return "ds"

        def check_request(self, kwargs):
            calls.append(deepcopy(kwargs))
            if kwargs["tool_choice"] is not None or second_failure:
                raise LLMError(
                    "OpenAI API 错误: Error code: 400 - Thinking mode does not support this tool_choice"
                )

        async def chat_completion(self, **kwargs):
            self.check_request(kwargs)
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-read",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            }

        async def stream_chat_completion(self, **kwargs):
            raise AssertionError("Native tools must not become plain text")
            yield ""

        async def stream_chat_completion_with_tools(self, **kwargs):
            self.check_request(kwargs)
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "call-read",
                "name": "read",
                "arguments_delta": "{}",
            }
            yield {"type": "done", "finish_reason": "tool_calls"}

    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]
    config = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", api_protocol="chat_completions"
    )

    async def collect():
        if not streamed:
            result = await LLMGateway.chat_completion(
                messages=[{"role": "user", "content": "read"}],
                model="ds:deepseek-v4-flash",
                tools=tools,
                tool_choice="required",
                retry=retry,
            )
            assert [call["id"] for call in result["tool_calls"]] == ["call-read"]
            return [
                {"type": "tool_call_delta"},
                {"type": "done", "request_meta": result["request_meta"]},
            ]
        return [
            item
            async for item in LLMGateway.stream_chat_completion_with_tools(
                messages=[{"role": "user", "content": "read"}],
                model="ds:deepseek-v4-flash",
                tools=tools,
                tool_choice="required",
                retry=retry,
                resume=0,
            )
        ]

    with (
        patch.object(LLMGateway, "_parse_model", return_value=("ds", "deepseek-v4-flash")),
        patch.object(LLMGateway, "_load_config", return_value=config),
        patch.object(LLMGateway, "_get_adapter", return_value=RejectingEndpoint),
    ):
        if second_failure:
            with pytest.raises(LLMError):
                asyncio.run(collect())
        else:
            events = asyncio.run(collect())
            assert [item["type"] for item in events] == ["tool_call_delta", "done"]
            assert any("tool_choice" in note for note in events[-1]["request_meta"]["adjustments"])
    assert len(calls) == 2
    assert calls[0]["tool_choice"] == "required"
    assert calls[1]["tool_choice"] is None
    assert calls[0]["messages"] == calls[1]["messages"]
    assert calls[0]["tools"] == calls[1]["tools"] == tools


@pytest.mark.parametrize(
    "detail,expected",
    [
        ("HTTP 401 Unauthorized: tool_choice=required", False),
        ("Error code: 429 - quota exceeded while sending tool_choice", False),
        ("HTTP 503 Unavailable: tool_choice unsupported", False),
        ("Timeout while sending tool_choice", False),
        ("Authentication required while sending tool_choice", False),
        ("Quota exceeded, tool_choice required", False),
        ("HTTP 400 tool_choice is not supported", True),
        ("Thinking mode does not support this tool_choice", True),
    ],
)
def test_only_parameter_rejections_allow_tool_choice_correction(detail, expected):
    from app.ai.capabilities import should_retry_without_tool_choice

    assert should_retry_without_tool_choice(LLMError(detail)) is expected


def test_upstream_sdk_status_takes_precedence_over_public_wrapper_and_error_body():
    from app.ai.capabilities import should_retry_without_tool_choice

    upstream = RuntimeError("tool_choice not supported")
    upstream.status_code = 429
    error = LLMError("HTTP 400 tool_choice not supported")
    error.__cause__ = upstream
    assert not should_retry_without_tool_choice(error)


@pytest.mark.parametrize(
    "field_detail",
    [
        "Thinking mode does not support this tool_choice",
        "The `reasoning_content` in the thinking mode must be passed back to the API.",
    ],
)
def test_protocol_failures_are_actionable_without_exposing_upstream_secrets(field_detail):
    from app.services.creation_agent_turn_runtime import safe_creation_agent_error
    from app.services.workspace.assistant_public_errors import public_model_failure

    error = LLMError(f"Error code: 400 - {field_detail} api_key=SECRET")
    creation_message, creation_data = safe_creation_agent_error(error)
    workspace = public_model_failure(error)
    assert creation_data["failure_class"] == workspace.failure_class == "provider_protocol"
    assert "协议" in creation_message and "协议" in creation_data["next_action"]
    assert workspace.code == "model_provider_protocol_error"
    assert workspace.details["retryable"] is False
    assert "SECRET" not in json.dumps([creation_message, creation_data, workspace.to_dict()])


def test_real_creation_turn_retains_category_and_read_with_thinking_protocol():
    """Exercise the durable runtime and real read tool, mocking only the model I/O."""
    from uuid import uuid4

    from sqlalchemy.orm import sessionmaker

    from app.modules.assistant.infrastructure.system_conversations import (
        SqlAlchemySystemConversationStore,
    )
    from app.services.creation_agent_turn_runtime import (
        CreationAgentTurnInput,
        produce_creation_agent_turn,
    )
    from tests.test_novel_creation_agent import _stream_completion
    from tests.test_novel_creation_workspace_v2 import _db, _ready_session

    db = _db()
    session = _ready_session(db)
    session_id = session.id
    db.commit()
    author_message = "只读取当前立项资料，不要修改。"
    requests = []

    def thinking_provider(**kwargs):
        messages = kwargs["messages"]
        requests.append(deepcopy(messages))
        latest_user = max(index for index, item in enumerate(messages) if item["role"] == "user")
        assert messages[latest_user]["content"] == author_message
        # Simulate the provider's thinking-protocol validation after the latest author.
        for item in messages[latest_user + 1 :]:
            if item["role"] == "assistant":
                assert item.get("reasoning_content"), (
                    "Missing provider reasoning in current tool round"
                )
        if len(requests) == 1:
            assert {item["function"]["name"] for item in kwargs["tools"]} == {"set_tool_categories"}
            name, arguments = "set_tool_categories", {"enabled_categories": ["creation_session"]}
        elif len(requests) == 2:
            name, arguments = "get_creation_session", {"session_id": session_id}
        else:
            assert len(requests) == 3
            assert not any(item["content"].startswith("[SERVER_VERIFIED_EXECUTION_RECEIPTS]") for item in messages)
            natives = [item for item in messages if item.get("tool_calls")]
            assert len(natives) == 2
            assert natives[0]["reasoning_content"] == "native-reasoning-1"
            native = natives[1]
            assert native["reasoning_content"] == "native-reasoning-2"
            assert native["tool_calls"][0]["function"]["name"] == "get_creation_session"
            assert messages[-1]["role"] == "tool"
            return {
                "content": "已读取立项资料，未修改。",
                "reasoning_content": "native-reasoning-3",
                "tool_calls": [],
            }
        return {
            "content": "",
            "reasoning_content": f"native-reasoning-{len(requests)}",
            "tool_calls": [
                {
                    "id": f"call-{len(requests)}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }

    events = []

    async def publish(event):
        events.append(event)

    request = CreationAgentTurnInput(
        session_id=session_id,
        message=author_message,
        client_turn_id=str(uuid4()),
        model="deepseek:deepseek-v4-flash",
        conversation_id=None,
        assistant_message_id=None,
        local_cli_read_paths=(),
    )
    with (
        patch(
            "app.services.creation_agent_turn_runtime.SessionLocal",
            new=sessionmaker(bind=db.get_bind(), expire_on_commit=False),
        ),
        patch(
            "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
            new=_stream_completion(thinking_provider),
        ),
    ):
        asyncio.run(produce_creation_agent_turn(request, publish))
    assert not [event for event in events if event["type"] == "error"], events
    assert len(requests) == 3
    completed_tools = [event for event in events if event["type"] == "tool_completed"]
    assert len(completed_tools) == 1
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.list(scope_type="creation", scope_id=session_id)["items"][0][
        "id"
    ]
    assistant = conversations.get(conversation_id)["messages"][-1]
    assert assistant["status"] == "completed"
    runtime = assistant["payload"]["creation_agent_runtime"]
    assert assistant["payload"]["creation_agent_turn"]["outcome"]["write_count"] == 0
    assert [item["tool"] for item in runtime["tool_results"]] == [
        "set_tool_categories",
        "get_creation_session",
    ]
    assert len(runtime["execution_receipts"]) == 2
    assert runtime["pending_tool_transactions"] == []


@pytest.mark.parametrize("surface", ["creation", "workspace"])
def test_resumed_tools_keep_only_the_completed_attempts_native_reasoning(surface):
    from app.services.agent_tool_stream import collect_tool_turn
    from app.services.workspace.assistant_native_turn import NativeStepCapture, WorkspaceNativeTurn
    from app.services.workspace.assistant_turn_state import WorkspaceAssistantTurnState

    requests = []

    class InterruptedEndpoint(BaseAdapter):
        @property
        def provider_name(self):
            return "ds"

        async def chat_completion(self, **kwargs):
            raise AssertionError("Must use native tools")

        async def stream_chat_completion(self, **kwargs):
            raise AssertionError("Must use native tools")
            yield ""

        async def stream_chat_completion_with_tools(self, **kwargs):
            requests.append(deepcopy(kwargs["messages"]))
            if len(requests) == 1:
                yield {"type": "reasoning_delta", "delta": "interrupted-reasoning"}
                yield {"type": "content_delta", "delta": "prefix"}
                yield {
                    "type": "tool_call_delta",
                    "index": 0,
                    "id": "discarded",
                    "name": "write",
                    "arguments_delta": '{"value":',
                }
                raise TimeoutError("stream interrupted")
            assert len(requests) == 2
            assert requests[-1][-1] == {"role": "user", "content": "write once"}
            assert not any(item["role"] == "assistant" for item in requests[-1])
            reference = next(
                item
                for item in requests[-1]
                if item["content"].startswith("[SERVER_VERIFIED_STREAM_CHECKPOINT]")
            )
            checkpoint = json.loads(reference["content"].splitlines()[2])
            yield {"type": "reasoning_delta", "delta": "completed-reasoning"}
            yield {"type": "content_delta", "delta": checkpoint["required_prefix"] + " suffix"}
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "accepted",
                "name": "write",
                "arguments_delta": '{"value":1}',
            }
            yield {
                "type": "done",
                "finish_reason": "tool_calls",
                "provider_state": [{"type": "reasoning", "encrypted_content": "completed-state"}],
            }

    config = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", api_protocol="chat_completions"
    )
    schemas = [
        {"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}
    ]

    async def collect():
        if surface == "creation":
            return await collect_tool_turn(
                LLMGateway,
                messages=[{"role": "user", "content": "write once"}],
                model="ds:deepseek-v4-flash",
                tools=schemas,
                retry=0,
                resume=2,
            )
        state = WorkspaceAssistantTurnState(
            db=None, project_id="test-project", selected_provider="ds",
            supports_function_calling=True, local_cli_selected=False, local_cli_mcp_enabled=False,
            encode_event=json.dumps, execute_action=None, prepare_context=None,
            payload=SimpleNamespace(model="ds:deepseek-v4-flash", temperature=0.3, max_tokens=None),
            local_cli_extra_body=None,
            turn_telemetry=Mock(),
            assistant_run=object(),
        )
        native = WorkspaceNativeTurn(state, LLMGateway, None)
        capture = NativeStepCapture()
        async for _event in native._collect(
            capture, [{"role": "user", "content": "write once"}], 1, schemas, "auto"
        ):
            pass
        return {
            "content": capture.reply_text,
            "reasoning_content": capture.reasoning,
            "provider_state": capture.provider_state,
            "tool_calls": list(capture.calls.values()),
        }

    with (
        patch.object(LLMGateway, "_parse_model", return_value=("ds", "deepseek-v4-flash")),
        patch.object(
            LLMGateway,
            "_load_config",
            return_value=config,
        ),
        patch.object(LLMGateway, "_get_adapter", return_value=InterruptedEndpoint),
        patch(
            "app.modules.model_runtime.infrastructure.gateway.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        result = asyncio.run(collect())
    assert result["content"] == "prefix suffix"
    assert result["reasoning_content"] == "completed-reasoning"
    assert result["provider_state"] == [
        {"type": "reasoning", "encrypted_content": "completed-state"}
    ]
    assert [call["id"] for call in result["tool_calls"]] == ["accepted"]


@pytest.mark.parametrize("streamed", [False, True])
def test_parameter_correction_does_not_consume_or_poison_transport_retry(streamed):
    calls = []

    class Endpoint(BaseAdapter):
        @property
        def provider_name(self):
            return "ds"

        async def chat_completion(self, **kwargs):
            calls.append(kwargs["tool_choice"])
            if len(calls) == 1:
                upstream = RuntimeError("SDK parameter rejection")
                upstream.status_code = 400
                raise LLMError(
                    "HTTP 400 Thinking mode does not support this tool_choice"
                ) from upstream
            if len(calls) == 2:
                # Adapters wrap their own network errors. A prior 400 must not
                # leak through Python exception context into this new attempt.
                raise LLMError("请求超时")
            return {"content": "ok"}

        async def stream_chat_completion(self, **kwargs):
            raise AssertionError("Tools cannot become plain text")
            yield ""

        async def stream_chat_completion_with_tools(self, **kwargs):
            await self.chat_completion(**kwargs)
            yield {"type": "done", "finish_reason": "stop"}

    config = SimpleNamespace(
        api_key="test-key", base_url="https://api.deepseek.com", api_protocol="chat_completions"
    )
    kwargs = {
        "messages": [{"role": "user", "content": "read"}],
        "model": "ds:deepseek-v4-flash",
        "tools": [
            {"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}
        ],
        "tool_choice": "required",
        "retry": 1,
    }

    async def run():
        if streamed:
            return [
                event
                async for event in LLMGateway.stream_chat_completion_with_tools(**kwargs, resume=0)
            ]
        return await LLMGateway.chat_completion(**kwargs)

    with (
        patch.object(LLMGateway, "_parse_model", return_value=("ds", "deepseek-v4-flash")),
        patch.object(
            LLMGateway,
            "_load_config",
            return_value=config,
        ),
        patch.object(LLMGateway, "_get_adapter", return_value=Endpoint),
        patch(
            "app.modules.model_runtime.infrastructure.gateway.asyncio.sleep",
            new=AsyncMock(),
        ),
    ):
        asyncio.run(run())
    assert calls == ["required", None, None]
