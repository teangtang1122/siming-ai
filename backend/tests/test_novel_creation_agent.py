from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.core.exceptions import LLMError
from app.database.models import NovelCreationStageRun, OperationRun
from app.modules.assistant.infrastructure.system_conversations import (
    SqlAlchemySystemConversationStore,
)
from app.modules.model_runtime.application.execution import model_executor
from app.routers.novel_creation import (
    CreationAgentRequest,
    creation_agent_turn,
    list_creation_sessions,
)
from app.services.agent_tool_stream import collect_tool_turn
from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
    ReferenceContext,
    ToolExecutionReceipt,
)
from app.services.conversation_context.canonical import canonical_sha256
from app.services.conversation_context.checkpoint_state import (
    safe_public_error_detail,
)
from app.services.creation_agent_execution import run_native_steps
from app.services.creation_agent_turn_records import (
    creation_agent_turn_records,
    creation_current_user_context_message,
    creation_execution_ledger_from_conversation,
    creation_turns_as_context_turns,
    seal_creation_runtime_snapshot,
    validate_creation_runtime_snapshot,
)
from app.services.creation_agent_turn_runtime import (
    CreationAgentTurnInput,
    produce_creation_agent_turn,
)
from app.services.novel_creation_agent import (
    CREATION_AGENT_TURN_SCHEMA,
    run_creation_agent,
)
from app.services.novel_creation_runs import create_run
from app.services.tool_category_state import (
    append_tool_category_audit,
    append_tool_category_event,
    replace_tool_categories,
)
from app.services.workspace.tool_result_projection import (
    TOOL_CATEGORY_CONTROLLER_RESULT_CONTRACT,
    ToolResultProjectionError,
    max_native_tool_transaction_wrapper_tokens,
)
from tests.test_novel_creation_workspace_v2 import _db, _ready_session


def _stream_completion(source):
    """Build a recording mock that returns the production async event protocol."""
    responses = iter(source) if isinstance(source, list) else None

    def invoke(**kwargs):
        async def generate():
            if responses is not None:
                response = next(responses)
            elif callable(source):
                response = source(**kwargs)
                if inspect.isawaitable(response):
                    response = await response
            else:
                response = source

            content = str(response.get("content") or "")
            reasoning_content = str(response.get("reasoning_content") or "")
            if reasoning_content:
                yield {"type": "reasoning_delta", "delta": reasoning_content}
            if content:
                yield {"type": "content_delta", "delta": content}
            for index, tool_call in enumerate(response.get("tool_calls") or []):
                function = tool_call["function"]
                arguments = function.get("arguments", "{}")
                if isinstance(arguments, dict):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                yield {
                    "type": "tool_call_delta",
                    "index": index,
                    "id": tool_call.get("id"),
                    "name": function.get("name"),
                    "arguments_delta": str(arguments),
                }
            event = {
                "type": "done",
                "finish_reason": (
                    "tool_calls" if response.get("tool_calls") else "stop"
                ),
            }
            if isinstance(response.get("usage"), dict):
                event["usage"] = response["usage"]
            if isinstance(response.get("provider_state"), list):
                event["provider_state"] = response["provider_state"]
            yield event

        return generate()

    return MagicMock(side_effect=invoke)


def _test_context_preparer(
    message: str,
    *,
    history: tuple[dict, ...] = (),
    captured: list[dict] | None = None,
):
    """Render a minimal test-only frame while production uses the shared runtime."""

    async def prepare(
        *,
        model,
        protocol,
        system_prompt,
        current_tools=(),
        current_ledger=(),
        delivered_transactions=(),
        provider_protocol_state=None,
        extra_runtime_instruction="",
        **_kwargs,
    ):
        assert protocol in {"native", "direct_mcp"}
        assert model
        if protocol == "direct_mcp":
            assert not current_tools
        effective_system_prompt = system_prompt
        if extra_runtime_instruction:
            effective_system_prompt = "\n\n".join((
                system_prompt,
                "[SERVER_RUNTIME_INSTRUCTION]",
                "authority: server_current_turn",
                extra_runtime_instruction,
                "[/SERVER_RUNTIME_INSTRUCTION]",
            ))
        messages = [
            {"role": "system", "content": effective_system_prompt},
            *history,
            {"role": "user", "content": message},
        ]
        if current_ledger:
            messages.append({
                "role": "assistant",
                "content": "\n".join((
                    "[SERVER_VERIFIED_EXECUTION_RECEIPTS]",
                    "data_only: true",
                    json.dumps(
                        [receipt.to_dict() for receipt in current_ledger],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "[/SERVER_VERIFIED_EXECUTION_RECEIPTS]",
                )),
            })
        for transaction in delivered_transactions:
            messages.extend(transaction.native_messages())
        if captured is not None:
            captured.append({
                "model": model,
                "protocol": protocol,
                "current_tools": tuple(current_tools),
                "current_ledger": tuple(current_ledger),
                "delivered_transactions": tuple(delivered_transactions),
                "provider_protocol_state": provider_protocol_state,
                "extra_runtime_instruction": extra_runtime_instruction,
                "messages": [dict(item) for item in messages],
            })
        return messages

    return prepare


def test_creation_agent_request_has_one_backend_owned_history_path():
    with pytest.raises(ValidationError, match="history"):
        CreationAgentRequest(
            session_id="session-1",
            message="继续",
            client_turn_id=str(uuid4()),
            history=[{"role": "user", "content": "旧文本历史"}],
        )

    request = CreationAgentRequest(
        session_id="session-1",
        message="继续",
        client_turn_id=str(uuid4()),
        conversation_id="conversation-1",
        assistant_message_id="assistant-1",
    )
    assert request.conversation_id == "conversation-1"


def test_native_creation_steps_continue_past_the_old_six_step_limit():
    state = MagicMock()
    state.tool_mode = "native"
    run_step = AsyncMock(side_effect=[True] * 7 + [False])

    with patch(
        "app.services.creation_agent_execution._run_native_step",
        new=run_step,
    ):
        asyncio.run(run_native_steps(state, MagicMock()))

    assert [call.args[2] for call in run_step.await_args_list] == list(range(8))


def test_creation_agent_request_validates_server_owned_reference_hash():
    request = CreationAgentRequest(
        session_id="session-1",
        message="按附件里的设定继续",
        client_turn_id=str(uuid4()),
        reference_context={
            "source_kind": "attachment",
            "source_name": "设定.txt",
            "content": "青鸾城终年落雪。",
            "coverage": "full",
            "source_chars": len("青鸾城终年落雪。"),
        },
    )
    assert request.reference_context is not None
    assert request.reference_context.content_sha256 == (
        "b84634bf7636efa8cdbc9a360ceebce76dbbe6bb5baad6c59f60e5186f1f3914"
    )

    with pytest.raises(ValidationError, match="content_sha256"):
        CreationAgentRequest(
            session_id="session-1",
            message="按附件里的设定继续",
            client_turn_id=str(uuid4()),
            reference_context={
                "source_kind": "attachment",
                "source_name": "设定.txt",
                "content": "青鸾城终年落雪。",
                "coverage": "full",
                "source_chars": len("青鸾城终年落雪。"),
                "content_sha256": "0" * 64,
            },
        )


def test_creation_session_list_keeps_its_single_router_path():
    store = type("EmptyCreationStore", (), {"sessions": lambda self, **_kwargs: []})()
    with patch(
        "app.routers.novel_creation_aux_routes.novel_creation_session_store",
        return_value=store,
    ):
        response = asyncio.run(list_creation_sessions(db=object()))

    assert response.data == {"sessions": []}


def test_creation_agent_collects_streamed_content_and_tool_calls():
    async def stream(**_kwargs):
        yield {"type": "reasoning_delta", "delta": "先核对真实状态"}
        yield {"type": "content_delta", "delta": "先读取"}
        yield {"type": "tool_call_delta", "index": 0, "id": "call-1", "name": "get_creation_snapshot"}
        yield {"type": "tool_call_delta", "index": 0, "arguments_delta": "{}"}
        yield {
            "type": "done",
            "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
            "provider_state": [{
                "type": "reasoning",
                "encrypted_content": "encrypted-state",
            }],
        }

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=stream,
    ):
        result = asyncio.run(collect_tool_turn(model_executor, messages=[], tools=[]))

    assert result["content"] == "先读取"
    assert result["reasoning_content"] == "先核对真实状态"
    assert result["provider_state"] == [{
        "type": "reasoning",
        "encrypted_content": "encrypted-state",
    }]
    assert result["tool_calls"][0]["id"] == "call-1"
    assert result["tool_calls"][0]["function"]["arguments"] == "{}"
    assert result["usage"]["prompt_tokens"] == 42


def test_shared_agent_collector_rejects_non_stream_gateway_results():
    class InvalidGateway:
        @staticmethod
        async def stream_chat_completion_with_tools(**_kwargs):
            return {"content": "旧的阻塞返回值", "tool_calls": []}

    with pytest.raises(TypeError, match="异步事件流"):
        asyncio.run(collect_tool_turn(InvalidGateway, messages=[], tools=[]))


def test_creation_agent_rejects_missing_native_call_id_without_running_handler():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion({
        "content": "",
        "tool_calls": [{
            "id": None,
            "type": "function",
            "function": {
                "name": "set_tool_categories",
                "arguments": '{"enabled_categories":["creation_data"]}',
            },
        }],
    })
    executor = AsyncMock()

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ), pytest.raises(ConversationContextError) as caught:
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="继续立项",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("继续立项"),
        ))

    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID
    assert caught.value.details == {
        "iteration": 1,
        "call_index": 0,
        "reason": "invalid_native_tool_call_identity",
    }
    executor.assert_not_awaited()
    assert completion.call_count == 1


@pytest.mark.parametrize(
    ("tool_calls", "expected_reason"),
    [
        (
            [
                {
                    "id": "call-categories",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": '{"enabled_categories":["creation_data"]}',
                    },
                },
                {
                    "id": "call-broken",
                    "type": "function",
                    "function": {
                        "name": "get_creation_snapshot",
                        "arguments": '{"broken"',
                    },
                },
            ],
            "invalid_native_tool_arguments_json",
        ),
        (
            [
                {
                    "id": "call-duplicate",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": '{"enabled_categories":["creation_data"]}',
                    },
                },
                {
                    "id": "call-duplicate",
                    "type": "function",
                    "function": {
                        "name": "get_creation_snapshot",
                        "arguments": "{}",
                    },
                },
            ],
            "duplicate_native_tool_call_id",
        ),
        (
            [
                {
                    "id": "call-categories",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": '{"enabled_categories":["creation_data"]}',
                    },
                },
                {
                    "id": "call-read",
                    "type": "function",
                    "function": {
                        "name": "get_creation_snapshot",
                        "arguments": "{}",
                    },
                },
            ],
            "category_controller_must_be_only_call",
        ),
        (
            [
                {
                    "id": "call-categories",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": "[]",
                    },
                },
            ],
            "native_tool_arguments_not_object",
        ),
        (
            [
                {
                    "id": "call-empty-arguments",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": "   ",
                    },
                },
            ],
            "native_tool_arguments_empty",
        ),
        (
            [
                {
                    "id": "call-not-open",
                    "type": "function",
                    "function": {
                        "name": "get_creation_snapshot",
                        "arguments": "{}",
                    },
                },
            ],
            "native_tool_not_open",
        ),
    ],
)
def test_creation_agent_rejects_invalid_native_batch_before_any_handler(
    tool_calls,
    expected_reason,
):
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion({"content": "", "tool_calls": tool_calls})
    category_handler = MagicMock()
    executor = AsyncMock()

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent._category_tool_result",
        new=category_handler,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ), pytest.raises(ConversationContextError) as caught:
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="继续立项",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("继续立项"),
        ))

    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID
    assert caught.value.details["reason"] == expected_reason
    category_handler.assert_not_called()
    executor.assert_not_awaited()
    assert completion.call_count == 1


def test_creation_agent_renders_reference_as_data_without_replacing_author_message():
    db = _db()
    session = _ready_session(db)
    author_message = "按我附的城市设定继续立项"
    reference = ReferenceContext(
        source_kind="attachment",
        source_name="城市设定.txt",
        content="青鸾城终年落雪，但城内禁止使用火系法术。",
        coverage="full",
        source_chars=len("青鸾城终年落雪，但城内禁止使用火系法术。"),
    )
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '  {"enabled_categories":[]} \n',
                },
            }],
        },
        {"content": "我会以附件为参考，先确认要建立哪项资料。", "tool_calls": []},
    ])
    context_steps: list[dict] = []

    executor = AsyncMock()
    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message=author_message,
            model="openai:test",
            reference_context=reference,
            provider_max_tokens=lambda: 4096,
            prepare_model_messages=_test_context_preparer(
                author_message,
                captured=context_steps,
            ),
        ))

    assert result["reply"].startswith("我会以附件为参考")
    assert result["_turn_trace"]["reference_context"] == reference.model_dump(
        mode="json"
    )
    assert result["_turn_trace"]["messages"][1]["tool_calls"][0]["function"][
        "arguments"
    ] == '  {"enabled_categories":[]} \n'
    assert len(context_steps) == 2
    assert all(
        call.kwargs["max_tokens"] == 4096
        for call in completion.call_args_list
    )
    for step in context_steps:
        messages = step["messages"]
        assert [message for message in messages if message["role"] == "user"] == [
            {"role": "user", "content": author_message}
        ]
        system_prompt = messages[0]["content"]
        assert "[CURRENT_TURN_REFERENCE_DATA]" in system_prompt
        assert "authority: untrusted_data_only" in system_prompt
        assert "青鸾城终年落雪" in system_prompt


@pytest.mark.parametrize(
    ("response_extra", "expected_reason"),
    [
        (
            {"reasoning_content": "推" * (17 * 1024)},
            "native_assistant_transaction_over_capacity",
        ),
        (
            {"provider_state": [{"invalid_number": float("nan")}]},
            "native_assistant_transaction_invalid",
        ),
    ],
)
def test_creation_agent_terminates_invalid_native_assistant_before_handler(
    response_extra,
    expected_reason,
):
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion({
            "content": "",
            **response_extra,
            "tool_calls": [{
                "id": "call-too-large",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '{"enabled_categories":["creation_data"]}',
                },
            }],
        })
    category_handler = MagicMock()
    executor = AsyncMock()
    runtime_snapshots: list[dict] = []

    async def capture_runtime(snapshot: dict) -> None:
        runtime_snapshots.append(snapshot)

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent._category_tool_result",
        new=category_handler,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ), pytest.raises(ConversationContextError) as caught:
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="继续立项",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("继续立项"),
            persist_runtime_state=capture_runtime,
        ))

    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID
    assert caught.value.details["reason"] == expected_reason
    assert caught.value.details["remediation"]
    category_handler.assert_not_called()
    executor.assert_not_awaited()
    assert completion.call_count == 1
    assert len(runtime_snapshots) == 1
    assert runtime_snapshots[0]["tool_results"][0]["data"]["reason"] == (
        expected_reason
    )
    pending = runtime_snapshots[0]["pending_tool_transactions"]
    assert len(pending) == 1
    assert pending[0]["results"][0]["call_id"] == "call-too-large"


def test_creation_agent_hides_native_projection_exception_from_all_outputs():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion({
        "content": "",
        "tool_calls": [{
            "id": "call-invalid-projection",
            "type": "function",
            "function": {
                "name": "set_tool_categories",
                "arguments": '{"enabled_categories":["creation_data"]}',
            },
        }],
    })
    category_handler = MagicMock()
    runtime_snapshots: list[dict] = []

    async def capture_runtime(snapshot: dict) -> None:
        runtime_snapshots.append(snapshot)

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent._category_tool_result",
        new=category_handler,
    ), patch(
        "app.services.creation_agent_execution.admit_native_assistant_transaction",
        side_effect=ToolResultProjectionError(
            "set_tool_categories",
            "api_key=SECRET raw provider body",
        ),
    ), pytest.raises(ConversationContextError) as caught:
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="继续立项",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("继续立项"),
            persist_runtime_state=capture_runtime,
        ))

    category_handler.assert_not_called()
    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID
    assert "SECRET" not in json.dumps(caught.value.details, ensure_ascii=False)
    assert len(runtime_snapshots) == 1
    persisted_wire = json.dumps(runtime_snapshots[0], ensure_ascii=False)
    assert "SECRET" not in persisted_wire
    assert "raw provider body" not in persisted_wire
    assert runtime_snapshots[0]["tool_results"][0] == {
        "tool": "set_tool_categories",
        "status": "error",
        "detail": "原生工具事务无法安全验证；本批次未执行。",
        "data": {"reason": "native_assistant_transaction_invalid"},
    }


def test_creation_agent_endpoint_persists_backend_owned_turns_and_passes_context_adapter():
    db = _db()
    session = _ready_session(db)
    reference = ReferenceContext(
        source_kind="routed_data",
        source_name="前端资料路由",
        content="作者指定本轮只核对立项状态。",
        coverage="full",
        source_chars=len("作者指定本轮只核对立项状态。"),
    )
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.create(
        "立项对话",
        scope_type="creation",
        scope_id=session.id,
    )["conversation"]["id"]
    first_started = conversations.start_turn(conversation_id, {
        "user_content": "读取当前立项",
        "creation_session_id": session.id,
        "scope_type": "creation",
        "scope_id": session.id,
    })
    first_assistant_id = first_started["messages"][1]["id"]
    db.commit()
    first_messages = [
        {"role": "user", "content": "读取当前立项"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-read",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call-read", "content": '{"status":"ok"}'},
        {"role": "assistant", "content": "已完成读取。"},
    ]
    agent_results = iter([
        {
            "reply": "已完成读取。",
            "tool_results": [{"tool": "get_creation_snapshot", "status": "ok"}],
            "write_count": 0,
            "run": None,
            "created_project_id": None,
            "_turn_trace": {
                "schema": CREATION_AGENT_TURN_SCHEMA,
                "session_id": session.id,
                "replayable": True,
                "messages": first_messages,
                "outcome": {"status": "completed"},
            },
        },
        {
            "reply": "已继续处理。",
            "tool_results": [],
            "write_count": 0,
            "run": None,
            "created_project_id": None,
            "_turn_trace": {
                "schema": CREATION_AGENT_TURN_SCHEMA,
                "session_id": session.id,
                "replayable": True,
                "messages": [
                    {"role": "user", "content": "继续"},
                    {"role": "assistant", "content": "已继续处理。"},
                ],
                "outcome": {"status": "completed"},
            },
        },
    ])
    prepared_context = AsyncMock(side_effect=lambda **kwargs: type(
        "PreparedContext",
        (),
        {
            "provider_messages": [
                {"role": "system", "content": kwargs["system_prompt"]},
                {
                    "role": "user",
                    "content": kwargs["current_user_message"].content,
                },
            ],
            "budget": type(
                "PreparedBudget",
                (),
                {"output_reserve_tokens": 4096},
            )(),
        },
    )())

    invoke_count = 0

    async def invoke_agent(*_args, **kwargs):
        nonlocal invoke_count
        controller_schema = {
            "type": "function",
            "function": {
                "name": "set_tool_categories",
                "description": "select categories",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        if invoke_count == 0:
            await kwargs["prepare_model_messages"](
                model="openai:test",
                protocol="native",
                system_prompt="creation-system",
                current_tools=(controller_schema,),
                delivered_transactions=(),
            )
        else:
            await kwargs["prepare_model_messages"](
                model="openai:test",
                protocol="direct_mcp",
                system_prompt="creation-system",
                current_tools=(),
                delivered_transactions=(),
                provider_protocol_state={
                    "protocol": "direct_mcp",
                    "tool_schemas": [controller_schema],
                },
            )
        invoke_count += 1
        assert kwargs["provider_max_tokens"]() == 4096
        return next(agent_results)

    agent = AsyncMock(side_effect=invoke_agent)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    async def events(response):
        collected = []
        async for chunk in response.body_iterator:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            for line in text.splitlines():
                if line.startswith("data:"):
                    collected.append(json.loads(line.removeprefix("data:").strip()))
        return collected

    async def run_turns():
        first_client_turn_id = str(uuid4())
        first_response = await creation_agent_turn(
            CreationAgentRequest(
                session_id=session.id,
                message="读取当前立项",
                client_turn_id=first_client_turn_id,
                conversation_id=conversation_id,
                assistant_message_id=first_assistant_id,
                reference_context=reference,
            ),
            request,
            db,
            conversations,
        )
        first_events = await events(first_response)
        reattached_response = await creation_agent_turn(
            CreationAgentRequest(
                session_id=session.id,
                message="读取当前立项",
                client_turn_id=first_client_turn_id,
                after_sequence=0,
                conversation_id=conversation_id,
                assistant_message_id=first_assistant_id,
                reference_context=reference,
            ),
            request,
            db,
            conversations,
        )
        reattached_events = await events(reattached_response)
        db.expire_all()
        second_started = conversations.start_turn(conversation_id, {
            "user_content": "继续",
            "creation_session_id": session.id,
            "scope_type": "creation",
            "scope_id": session.id,
        })
        db.commit()
        second_response = await creation_agent_turn(
            CreationAgentRequest(
                session_id=session.id,
                message="继续",
                client_turn_id=str(uuid4()),
                conversation_id=conversation_id,
                assistant_message_id=second_started["messages"][1]["id"],
            ),
            request,
            db,
            conversations,
        )
        return first_events, reattached_events, await events(second_response)

    with patch(
        "app.routers.novel_creation._resolve_mobile_creation_provider",
        return_value=None,
    ), patch(
        "app.services.creation_agent_turn_runtime.run_creation_agent",
        new=agent,
    ), patch(
        "app.services.creation_agent_turn_runtime.prepare_conversation_context",
        new=prepared_context,
    ), patch(
        "app.services.creation_agent_turn_runtime.SessionLocal",
        new=session_factory,
    ):
        first_events, reattached_events, second_events = asyncio.run(run_turns())

    first_complete = [event for event in first_events if event["type"] == "complete"]
    assert first_complete, first_events
    assert first_complete[0]["data"]["turn_persisted"] is True
    assert next(event for event in second_events if event["type"] == "complete")["data"]["turn_persisted"] is True
    assert next(event for event in reattached_events if event["type"] == "complete")["data"]["reply"] == "已完成读取。"
    assert agent.await_count == 2
    assert callable(agent.call_args_list[0].kwargs["prepare_model_messages"])
    assert callable(agent.call_args_list[1].kwargs["prepare_model_messages"])
    assert callable(agent.call_args_list[0].kwargs["persist_runtime_state"])
    assert callable(agent.call_args_list[1].kwargs["persist_runtime_state"])
    assert agent.call_args_list[0].kwargs["reference_context"] == reference
    assert agent.call_args_list[1].kwargs["reference_context"] is None
    assert all(
        call.kwargs["turn_execution_id"]
        for call in agent.call_args_list
    )
    assert (
        agent.call_args_list[0].kwargs["turn_execution_id"]
        != agent.call_args_list[1].kwargs["turn_execution_id"]
    )
    assert prepared_context.await_count == 2
    first_context = prepared_context.call_args_list[0].kwargs
    second_context = prepared_context.call_args_list[1].kwargs
    assert first_context["turns"] == ()
    assert first_context["trusted_execution_ledger"] == ()
    assert first_context["execution_source_hashes"] == {}
    assert first_context["current_user_message"].content == "读取当前立项"
    assert first_context["conversation"].revision == 1
    assert first_context["conversation"].creation_session_id == session.id
    assert second_context["current_user_message"].content == "继续"
    assert second_context["conversation"].revision == 3
    assert len(second_context["turns"]) == 1
    # Successful reads are exact within their model step but intentionally do
    # not become cross-turn execution facts.
    assert second_context["trusted_execution_ledger"] == ()
    assert second_context["execution_source_hashes"] == {}
    assert [
        message.content for message in second_context["turns"][0].messages
    ] == ["读取当前立项", "已完成读取。"]
    assert first_context["protocol"] == "native"
    assert first_context["current_tools"][0]["function"]["name"] == (
        "set_tool_categories"
    )
    assert first_context[
        "max_model_visible_result_tokens_for_open_tools"
    ] == TOOL_CATEGORY_CONTROLLER_RESULT_CONTRACT.max_json_bytes
    assert first_context["next_step_wrapper"] == (
        max_native_tool_transaction_wrapper_tokens()
    )
    assert second_context["protocol"] == "direct_mcp"
    assert second_context["current_tools"] == ()
    assert second_context[
        "max_model_visible_result_tokens_for_open_tools"
    ] == TOOL_CATEGORY_CONTROLLER_RESULT_CONTRACT.max_json_bytes
    assert second_context["next_step_wrapper"] == 0
    assert second_context["provider_protocol_state"]["protocol"] == "direct_mcp"
    assert second_context["provider_protocol_state"]["tool_schemas"][0][
        "function"
    ]["name"] == "set_tool_categories"
    assert second_context["active_tool_category_hash"] == canonical_sha256(
        second_context["provider_protocol_state"]["tool_schemas"]
    )
    detail = conversations.get(conversation_id)
    traces = [
        message["payload"]["creation_agent_turn"]
        for message in detail["messages"]
        if message["role"] == "assistant"
    ]
    assert [trace["messages"] for trace in traces] == [
        first_messages,
        [
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "已继续处理。"},
        ],
    ]
    for message in detail["messages"]:
        if message["role"] != "assistant":
            continue
        assert validate_creation_runtime_snapshot(
            message["payload"]["creation_agent_runtime"],
            session_id=session.id,
        ) is not None
    assert detail["messages"][1]["payload"][
        "creation_agent_reference_context"
    ] == reference.model_dump(mode="json")
    assert detail["messages"][1]["payload"]["creation_agent_runtime"][
        "reference_context"
    ] == reference.model_dump(mode="json")


def test_creation_agent_endpoint_never_reexecutes_an_uncertain_persisted_client_turn():
    db = _db()
    session = _ready_session(db)
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.create(
        "立项对话",
        scope_type="creation",
        scope_id=session.id,
    )["conversation"]["id"]
    client_turn_id = str(uuid4())
    started = conversations.start_turn(conversation_id, {
        "user_content": "修改主角",
        "creation_session_id": session.id,
        "scope_type": "creation",
        "scope_id": session.id,
        "payload": {"creation_agent_client_turn_id": client_turn_id},
    })
    assistant_message_id = started["messages"][1]["id"]
    db.commit()
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    agent = AsyncMock()

    async def run_turn():
        response = await creation_agent_turn(
            CreationAgentRequest(
                session_id=session.id,
                message="修改主角",
                client_turn_id=client_turn_id,
                conversation_id=conversation_id,
                assistant_message_id=assistant_message_id,
            ),
            request,
            db,
            conversations,
        )
        collected = []
        async for chunk in response.body_iterator:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            collected.extend(
                json.loads(line.removeprefix("data:").strip())
                for line in text.splitlines()
                if line.startswith("data:")
            )
        return collected

    with patch(
        "app.routers.novel_creation._resolve_mobile_creation_provider",
        return_value=None,
    ), patch(
        "app.services.creation_agent_turn_runtime.run_creation_agent",
        new=agent,
    ), patch(
        "app.services.creation_agent_turn_runtime.SessionLocal",
        new=session_factory,
    ):
        events = asyncio.run(run_turn())

    error = next(event for event in events if event["type"] == "error")
    assert error["data"]["error_type"] == "turn_recovery_required"
    assert "不会重新执行" in error["message"]
    agent.assert_not_awaited()


def test_creation_runtime_receipt_is_durable_and_blocks_write_replay_after_crash():
    db = _db()
    session = _ready_session(db)
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.create(
        "立项对话",
        scope_type="creation",
        scope_id=session.id,
    )["conversation"]["id"]
    started = conversations.start_turn(conversation_id, {
        "user_content": "把类型改成玄幻",
        "creation_session_id": session.id,
        "scope_type": "creation",
        "scope_id": session.id,
    })
    assistant_message_id = started["messages"][1]["id"]
    db.commit()
    client_turn_id = str(uuid4())
    request = CreationAgentTurnInput(
        session_id=session.id,
        message="把类型改成玄幻",
        client_turn_id=client_turn_id,
        model="openai:test",
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        local_cli_read_paths=(),
    )
    receipt = ToolExecutionReceipt(
        step_id="creation-step:write-1",
        tool="patch_creation_session",
        status="ok",
        summary='{"tool":"patch_creation_session","status":"ok"}',
        resource_ids=(session.id,),
        result_ref="creation-tool-result:sha256:write-1",
        reread="继续前重新读取当前 creation snapshot 与 revision。",
        write_committed=True,
    )
    runtime_snapshot = seal_creation_runtime_snapshot({
        "session_id": session.id,
        "status": "running",
        "tool_mode": "native",
        "tool_results": [{
            "tool": "patch_creation_session",
            "status": "ok",
            "data": {"session_id": session.id, "revision": 2},
        }],
        "execution_receipts": [receipt.to_dict()],
        "compacted_tool_transactions": [],
        "pending_tool_transactions": [],
        "successful_write_count": 1,
        "failed_write_count": 0,
        "successful_read_count": 1,
    })
    session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    agent = AsyncMock()

    async def interrupted_agent(*_args, **kwargs):
        await kwargs["persist_runtime_state"](runtime_snapshot)
        raise RuntimeError("simulated process interruption")

    agent.side_effect = interrupted_agent

    async def run_attempts():
        first_events: list[dict] = []
        second_events: list[dict] = []

        async def first_publish(event: dict) -> None:
            first_events.append(event)

        async def second_publish(event: dict) -> None:
            second_events.append(event)

        await produce_creation_agent_turn(request, first_publish)
        await produce_creation_agent_turn(request, second_publish)
        return first_events, second_events

    with patch(
        "app.services.creation_agent_turn_runtime.run_creation_agent",
        new=agent,
    ), patch(
        "app.services.creation_agent_turn_runtime.SessionLocal",
        new=session_factory,
    ):
        _, recovered_events = asyncio.run(run_attempts())

    db.expire_all()
    detail = conversations.get(conversation_id)
    assistant = next(
        message
        for message in detail["messages"]
        if message["id"] == assistant_message_id
    )
    stored_runtime = assistant["payload"]["creation_agent_runtime"]
    assert validate_creation_runtime_snapshot(
        stored_runtime,
        session_id=session.id,
    ) is not None
    tampered_runtime = json.loads(json.dumps(stored_runtime))
    tampered_runtime["execution_receipts"][0]["write_committed"] = False
    assert validate_creation_runtime_snapshot(
        tampered_runtime,
        session_id=session.id,
    ) is None
    recovery = next(event for event in recovered_events if event["type"] == "error")
    assert recovery["data"]["error_type"] == "turn_recovery_required"
    assert recovery["data"]["committed_write_count"] == 1
    assert recovery["data"]["result_refs"] == [receipt.result_ref]
    assert "不会重新执行" in recovery["message"]
    assert agent.await_count == 1


@pytest.mark.parametrize(
    ("code", "details"),
    (
        (
            ConversationContextErrorCode.CAPACITY_UNKNOWN,
            {"model": "unknown-model"},
        ),
        (
            ConversationContextErrorCode.CHECKPOINT_FAILED,
            {"checkpoint_id": "checkpoint-1"},
        ),
    ),
)
def test_creation_context_errors_stay_stable_and_prevent_business_tools(
    code: ConversationContextErrorCode,
    details: dict,
):
    db = _db()
    session = _ready_session(db)
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.create(
        "立项对话",
        scope_type="creation",
        scope_id=session.id,
    )["conversation"]["id"]
    started = conversations.start_turn(conversation_id, {
        "user_content": "继续立项",
        "creation_session_id": session.id,
        "scope_type": "creation",
        "scope_id": session.id,
    })
    assistant_message_id = started["messages"][1]["id"]
    db.commit()
    request = CreationAgentTurnInput(
        session_id=session.id,
        message="继续立项",
        client_turn_id=str(uuid4()),
        model="openai:test",
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        local_cli_read_paths=(),
    )
    private_message = f"stable error: {code.value}; api_key=SECRET"
    expected_message = safe_public_error_detail(code)
    context_error = ConversationContextError(
        code,
        private_message,
        details={**details, "raw_provider_error": "api_key=SECRET"},
    )
    business_tool = AsyncMock()

    async def invoke_agent(*_args, **kwargs):
        await kwargs["prepare_model_messages"](
            model="openai:test",
            protocol="native",
            system_prompt="creation-system",
            current_tools=(),
        )
        await business_tool()

    agent = AsyncMock(side_effect=invoke_agent)
    session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    events: list[dict] = []
    replayed_events: list[dict] = []

    async def publish(event: dict) -> None:
        events.append(event)

    async def replay_publish(event: dict) -> None:
        replayed_events.append(event)

    with patch(
        "app.services.creation_agent_turn_runtime.run_creation_agent",
        new=agent,
    ), patch(
        "app.services.creation_agent_turn_runtime.prepare_conversation_context",
        new=AsyncMock(side_effect=context_error),
    ), patch(
        "app.services.creation_agent_turn_runtime.SessionLocal",
        new=session_factory,
    ):
        async def run_and_recover() -> None:
            await produce_creation_agent_turn(request, publish)
            await produce_creation_agent_turn(request, replay_publish)

        asyncio.run(run_and_recover())

    error_event = next(event for event in events if event["type"] == "error")
    assert error_event["message"] == expected_message
    assert error_event["data"]["code"] == code.value
    assert error_event["data"]["message"] == expected_message
    assert error_event["data"]["details"]["remediation"]
    assert "SECRET" not in json.dumps(error_event, ensure_ascii=False)
    replayed_error = next(
        event for event in replayed_events if event["type"] == "error"
    )
    assert replayed_error["message"] == expected_message
    assert replayed_error["data"]["code"] == code.value
    assert replayed_error["data"]["details"]["remediation"]
    assert "SECRET" not in json.dumps(replayed_error, ensure_ascii=False)
    assert agent.await_count == 1
    business_tool.assert_not_awaited()
    db.expire_all()
    assistant = next(
        message
        for message in conversations.get(conversation_id)["messages"]
        if message["id"] == assistant_message_id
    )
    persisted_error = assistant["payload"]["creation_agent_error"]
    assert persisted_error["code"] == code.value
    assert persisted_error["message"] == expected_message
    assert persisted_error["details"]["remediation"]
    assert "SECRET" not in json.dumps(persisted_error, ensure_ascii=False)


def test_creation_unexpected_error_never_persists_or_streams_raw_exception():
    db = _db()
    session = _ready_session(db)
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.create(
        "立项对话",
        scope_type="creation",
        scope_id=session.id,
    )["conversation"]["id"]
    started = conversations.start_turn(conversation_id, {
        "user_content": "继续立项",
        "creation_session_id": session.id,
        "scope_type": "creation",
        "scope_id": session.id,
    })
    assistant_message_id = started["messages"][1]["id"]
    db.commit()
    request = CreationAgentTurnInput(
        session_id=session.id,
        message="继续立项",
        client_turn_id=str(uuid4()),
        model="openai:test",
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        local_cli_read_paths=(),
    )
    events: list[dict] = []

    async def publish(event: dict) -> None:
        events.append(event)

    session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    with patch(
        "app.services.creation_agent_turn_runtime.run_creation_agent",
        new=AsyncMock(side_effect=RuntimeError("api_key=SECRET raw provider body")),
    ), patch(
        "app.services.creation_agent_turn_runtime.SessionLocal",
        new=session_factory,
    ):
        asyncio.run(produce_creation_agent_turn(request, publish))

    error_event = next(event for event in events if event["type"] == "error")
    error_id = error_event["data"]["error_id"]
    assert error_event["message"] == f"立项助手处理失败；错误编号：{error_id}"
    assert error_event["data"] == {
        "error_type": "RuntimeError",
        "failure_class": "unknown",
        "next_action": "请检查模型状态后重试本轮。",
        "error_id": error_id,
    }
    assert "SECRET" not in json.dumps(error_event, ensure_ascii=False)
    db.expire_all()
    assistant = next(
        message
        for message in conversations.get(conversation_id)["messages"]
        if message["id"] == assistant_message_id
    )
    persisted = assistant["payload"]["creation_agent_error"]
    assert persisted["message"] == error_event["message"]
    assert persisted["error_id"] == error_id
    assert "SECRET" not in json.dumps(persisted, ensure_ascii=False)


def test_creation_agent_lets_model_select_categories_then_call_creation_tools():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "reasoning_content": "先选择本轮需要的立项能力",
            "provider_state": [{
                "type": "reasoning",
                "encrypted_content": "creation-reasoning-state",
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
            "tool_calls": [
                {
                    "id": "call-categories",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": '{"enabled_categories":["creation_data","creation_flow"]}',
                    },
                },
            ],
        },
        {
            "content": "",
            "usage": {"prompt_tokens": 220, "completion_tokens": 20, "total_tokens": 240},
            "tool_calls": [{
                "id": "call-read",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "usage": {"prompt_tokens": 180, "completion_tokens": 20, "total_tokens": 200},
            "tool_calls": [{
                "id": "call-generate",
                "type": "function",
                "function": {
                    "name": "generate_creation_artifact",
                    "arguments": json.dumps({
                        "artifact": "world_style",
                        "entity_type": "worldbuilding",
                        "instruction": "新增用户描述的两条修炼规则",
                    }, ensure_ascii=False),
                },
            }],
        },
        {"content": "已读取当前设定，并开始新增修炼规则。", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {"tool": "get_creation_snapshot", "status": "ok", "data": {"revision": session.revision}},
        {"tool": "generate_creation_artifact", "status": "ok", "data": {"run": {"id": "run-1", "status": "running"}}},
    ])
    context_steps: list[dict] = []
    runtime_snapshots: list[dict] = []

    async def capture_runtime_snapshot(snapshot: dict) -> None:
        runtime_snapshots.append(snapshot)

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
            "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="在世界观里加入两条修炼规则",
            model="openai:test",
            prepare_model_messages=_test_context_preparer(
                "在世界观里加入两条修炼规则",
                history=(
                    {"role": "user", "content": "这是仙侠小说"},
                    {"role": "assistant", "content": "已记录为仙侠方向。"},
                ),
                captured=context_steps,
            ),
            persist_runtime_state=capture_runtime_snapshot,
        ))

    read_action = executor.call_args_list[0].args[2]
    write_action = executor.call_args_list[1].args[2]
    assert read_action["tool"] == "get_creation_snapshot"
    assert read_action["arguments"]["session_id"] == session.id
    assert write_action["tool"] == "generate_creation_artifact"
    assert write_action["arguments"]["session_id"] == session.id
    assert write_action["arguments"]["expected_revision"] == session.revision
    assert write_action["arguments"]["model"] == "openai:test"
    assert result["run"]["id"] == "run-1"
    assert "开始新增" in result["reply"]
    assert completion.call_args_list[0].kwargs["tool_choice"] == "required"
    assert completion.call_args_list[1].kwargs["tool_choice"] == "auto"
    first_schema_names = {
        item["function"]["name"] for item in completion.call_args_list[0].kwargs["tools"]
    }
    second_schema_names = {
        item["function"]["name"] for item in completion.call_args_list[1].kwargs["tools"]
    }
    assert first_schema_names == {"set_tool_categories"}
    assert "generate_creation_artifact" in second_schema_names
    assert "patch_creation_entity" in second_schema_names
    assert [item["role"] for item in result["_turn_trace"]["messages"]] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant", "tool", "assistant",
    ]
    metrics = result["_turn_trace"]["prompt_metrics"]
    assert metrics[0]["tool_count"] == 1
    assert metrics[0]["prompt_tokens"] == 100
    assert metrics[1]["prompt_tokens"] == 220
    assert metrics[2]["prompt_tokens"] == 180
    assert result["_turn_trace"]["outcome"]["prompt_tokens"] == 500
    assert [
        len(step["delivered_transactions"])
        for step in context_steps
    ] == [0, 1, 1, 1]
    assert [
        len(step["current_ledger"])
        for step in context_steps
    ] == [0, 0, 1, 2]
    assert all(
        receipt.step_id.startswith("creation-step:")
        and receipt.result_ref.startswith("creation-tool-result:sha256:")
        for step in context_steps
        for receipt in step["current_ledger"]
    )
    assert all(
        transaction.state.value == "delivered"
        for step in context_steps
        for transaction in step["delivered_transactions"]
    )
    assert [message["role"] for message in context_steps[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "tool",
    ]
    pending_assistant = next(
        message
        for message in context_steps[1]["messages"]
        if message.get("tool_calls")
    )
    assert pending_assistant["reasoning_content"] == "先选择本轮需要的立项能力"
    assert pending_assistant["provider_state"] == [{
        "type": "reasoning",
        "encrypted_content": "creation-reasoning-state",
    }]
    assert len(result["_turn_trace"]["execution_receipts"]) == 3
    assert all(
        receipt["step_id"].startswith("creation-step:")
        and receipt["result_ref"].startswith("creation-tool-result:sha256:")
        for receipt in result["_turn_trace"]["execution_receipts"]
    )
    assert len(result["_turn_trace"]["compacted_tool_transactions"]) == 3
    assert result["_turn_trace"]["pending_tool_transactions"] == []
    assert [
        len(snapshot["pending_tool_transactions"])
        for snapshot in runtime_snapshots
    ] == [1, 0, 1, 0, 1, 0]
    assert [
        len(snapshot["execution_receipts"])
        for snapshot in runtime_snapshots
    ] == [1, 1, 2, 2, 3, 3]
    assert all(
        validate_creation_runtime_snapshot(
            snapshot,
            session_id=session.id,
        ) is not None
        for snapshot in runtime_snapshots
    )


def test_native_creation_agent_allows_same_read_after_result_is_consumed():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '{"enabled_categories":["creation_data"]}',
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read-first",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read-fresh",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {"content": "已重新读取最新立项状态。", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {
            "tool": "get_creation_snapshot",
            "status": "ok",
            "data": {"revision": int(session.revision or 0)},
        },
        {
            "tool": "get_creation_snapshot",
            "status": "ok",
            "data": {"revision": int(session.revision or 0)},
        },
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="确认刚才读取的数据仍然最新",
            model="openai:test",
            prepare_model_messages=_test_context_preparer(
                "确认刚才读取的数据仍然最新"
            ),
        ))

    assert executor.await_count == 2
    assert [
        call.args[2]["tool"] for call in executor.await_args_list
    ] == ["get_creation_snapshot", "get_creation_snapshot"]
    assert [
        item["status"]
        for item in result["tool_results"]
        if item["tool"] == "get_creation_snapshot"
    ] == ["ok", "ok"]


def test_native_creation_agent_keeps_failed_write_signature_deduped():
    db = _db()
    session = _ready_session(db)
    repeated_arguments = '{"changes":{"genre":"玄幻"}}'
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '{"enabled_categories":["creation_data"]}',
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-write-first",
                "type": "function",
                "function": {
                    "name": "patch_creation_session",
                    "arguments": repeated_arguments,
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-write-duplicate",
                "type": "function",
                "function": {
                    "name": "patch_creation_session",
                    "arguments": repeated_arguments,
                },
            }],
        },
        {"content": "写入未完成，已停止重复提交。", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {
            "tool": "get_creation_snapshot",
            "status": "ok",
            "data": {"revision": int(session.revision or 0)},
        },
        {
            "tool": "patch_creation_session",
            "status": "error",
            "detail": "temporary internal failure",
            "data": None,
        },
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="改成玄幻",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("改成玄幻"),
        ))

    assert executor.await_count == 2
    duplicate = result["tool_results"][-1]
    assert duplicate["tool"] == "patch_creation_session"
    assert duplicate["status"] == "skipped"
    assert duplicate["data"]["reason"] == "creation_tool_skipped"


def test_creation_tool_failure_is_public_before_model_event_and_persistence():
    db = _db()
    session = _ready_session(db)
    secret = "api_key=SECRET raw provider body"
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": (
                        '{"enabled_categories":["creation_data","creation_flow"]}'
                    ),
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-generate",
                "type": "function",
                "function": {
                    "name": "generate_creation_artifact",
                    "arguments": json.dumps({
                        "artifact": "world_style",
                        "instruction": "补充世界设定",
                    }, ensure_ascii=False),
                },
            }],
        },
        {"content": "生成未完成，请调整后重试。", "tool_calls": []},
    ])
    context_steps: list[dict] = []
    runtime_snapshots: list[dict] = []
    events: list[dict] = []

    async def capture_runtime(snapshot: dict) -> None:
        runtime_snapshots.append(snapshot)

    async def capture_event(event: dict) -> None:
        events.append(event)

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_stage_execution._prepare_execution",
        side_effect=RuntimeError(secret),
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="生成世界设定",
            model="openai:test",
            prepare_model_messages=_test_context_preparer(
                "生成世界设定",
                captured=context_steps,
            ),
            persist_runtime_state=capture_runtime,
            on_event=capture_event,
        ))

    failed = next(
        item
        for item in result["tool_results"]
        if item["tool"] == "generate_creation_artifact"
    )
    assert failed == {
        "tool": "generate_creation_artifact",
        "status": "error",
        "detail": "工具未能完成本次操作；请重新读取当前状态或调整请求后重试。",
        "data": {"reason": "creation_tool_failed"},
    }
    model_messages = [
        message
        for step in context_steps
        for message in step["messages"]
    ]
    public_wire = json.dumps(
        {
            "model_messages": model_messages,
            "events": events,
            "runtime_snapshots": runtime_snapshots,
            "turn_trace": result["_turn_trace"],
        },
        ensure_ascii=False,
        default=str,
    )
    assert "SECRET" not in public_wire
    assert "raw provider body" not in public_wire


def test_creation_agent_persists_partial_batch_receipt_before_next_handler():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '{"enabled_categories":["creation_data"]}',
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-initial-read",
                "type": "function",
                "function": {
                    "name": "get_creation_snapshot",
                    "arguments": "{}",
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-write",
                    "type": "function",
                    "function": {
                        "name": "patch_creation_session",
                        "arguments": '{"changes":{"genre":"玄幻"}}',
                    },
                },
                {
                    "id": "call-read-after-write",
                    "type": "function",
                    "function": {
                        "name": "get_creation_session",
                        "arguments": "{}",
                    },
                },
            ],
        },
    ])
    executor = AsyncMock(side_effect=[
        {
            "tool": "get_creation_snapshot",
            "status": "ok",
            "data": {"revision": int(session.revision or 0)},
        },
        {
            "tool": "patch_creation_session",
            "status": "ok",
            "detail": "Creation session patched",
            "data": {"revision": int(session.revision or 0) + 1},
        },
        RuntimeError("simulated crash before the second handler returned"),
    ])
    runtime_snapshots: list[dict] = []

    async def capture_runtime(snapshot: dict) -> None:
        runtime_snapshots.append(snapshot)

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ), pytest.raises(RuntimeError, match="simulated crash"):
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="读取后改成玄幻",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("读取后改成玄幻"),
            persist_runtime_state=capture_runtime,
        ))

    persisted_before_crash = runtime_snapshots[-1]
    write_receipt = next(
        receipt
        for receipt in persisted_before_crash["execution_receipts"]
        if receipt["tool"] == "patch_creation_session"
    )
    assert write_receipt["write_committed"] is True
    partial_transaction = persisted_before_crash["pending_tool_transactions"][-1]
    assert partial_transaction["state"] == "pending"
    assert [call["call_id"] for call in partial_transaction["calls"]] == [
        "call-write",
        "call-read-after-write",
    ]
    assert [result["call_id"] for result in partial_transaction["results"]] == [
        "call-write"
    ]


def test_native_summary_uses_server_instruction_without_a_second_user_message():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories-summary",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '{"enabled_categories":["creation_data"]}',
                },
            }],
        },
        {"content": "", "tool_calls": []},
        {"content": "本轮没有保存修改；下一步要先补充哪项设定？", "tool_calls": []},
    ])
    context_steps: list[dict] = []

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="先检查可用能力",
            model="openai:test",
            prepare_model_messages=_test_context_preparer(
                "先检查可用能力",
                captured=context_steps,
            ),
        ))

    assert len(context_steps) == 3
    assert completion.call_count == 3
    summary_step = context_steps[-1]
    assert summary_step["current_tools"] == ()
    assert summary_step["extra_runtime_instruction"].startswith("请根据以上真实工具返回")
    assert "[SERVER_RUNTIME_INSTRUCTION]" in summary_step["messages"][0]["content"]
    assert [
        message["content"]
        for message in summary_step["messages"]
        if message["role"] == "user"
    ] == ["先检查可用能力"]
    assert [
        len(step["delivered_transactions"])
        for step in context_steps
    ] == [0, 1, 0]
    assert [
        len(step["current_ledger"])
        for step in context_steps
    ] == [0, 0, 1]
    assert len(result["_turn_trace"]["execution_receipts"]) == 1
    assert len(result["_turn_trace"]["compacted_tool_transactions"]) == 1
    assert result["_turn_trace"]["pending_tool_transactions"] == []


def test_native_creation_agent_blocks_a_second_successful_write_in_one_user_turn():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '{"enabled_categories":["creation_data"]}',
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read-before-write",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-write-one",
                    "type": "function",
                    "function": {
                        "name": "patch_creation_session",
                        "arguments": '{"changes":{"genre":"玄幻"}}',
                    },
                },
                {
                    "id": "call-write-two",
                    "type": "function",
                    "function": {
                        "name": "patch_creation_artifact",
                        "arguments": '{"artifact":"characters","changes":[]}',
                    },
                },
            ],
        },
        {"content": "已记录本轮一个修改。下一步想先完善哪个角色？", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {
            "tool": "get_creation_snapshot",
            "status": "ok",
            "data": {"revision": int(session.revision or 0)},
        },
        {
            "tool": "patch_creation_session",
            "status": "ok",
            "detail": "立项会话已更新",
            "data": {"revision": int(session.revision or 0) + 1},
        },
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="继续",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("继续"),
        ))

    assert executor.await_count == 2
    assert result["write_count"] == 1
    blocked = next(item for item in result["tool_results"] if item["tool"] == "patch_creation_artifact")
    assert blocked["status"] == "denied"
    assert blocked["data"]["reason"] == "successful_write_limit"
    assert completion.call_args_list[3].kwargs["tools"] == []
    assert "下一步想先完善哪个角色" in result["reply"]


def test_native_creation_agent_defers_same_step_write_until_read_result_is_seen():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '{"enabled_categories":["creation_data"]}',
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-read",
                    "type": "function",
                    "function": {"name": "get_creation_snapshot", "arguments": "{}"},
                },
                {
                    "id": "call-too-early-write",
                    "type": "function",
                    "function": {
                        "name": "patch_creation_session",
                        "arguments": '{"changes":{"genre":"玄幻"}}',
                    },
                },
            ],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-write-after-read",
                "type": "function",
                "function": {
                    "name": "patch_creation_session",
                    "arguments": '{"changes":{"genre":"玄幻"}}',
                },
            }],
        },
        {"content": "已在读取真实 revision 后保存类型。", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {
            "tool": "get_creation_snapshot",
            "status": "ok",
            "data": {"revision": int(session.revision or 0)},
        },
        {
            "tool": "patch_creation_session",
            "status": "ok",
            "detail": "Creation session patched",
            "data": {"revision": int(session.revision or 0) + 1},
        },
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="改成玄幻",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("改成玄幻"),
        ))

    assert executor.await_count == 2
    denied = next(
        item for item in result["tool_results"]
        if item["tool"] == "patch_creation_session" and item["status"] == "denied"
    )
    assert denied["data"]["reason"] == "read_required"
    assert result["write_count"] == 1


def test_native_oversized_entity_result_is_rejected_without_character_truncation():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [{
                "id": "call-categories",
                "type": "function",
                "function": {
                    "name": "set_tool_categories",
                    "arguments": '{"enabled_categories":["creation_data"]}',
                },
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-entity",
                "type": "function",
                "function": {
                    "name": "get_creation_entity",
                    "arguments": '{"entity_id":"entity-1"}',
                },
            }],
        },
        {"content": "已读取目标实体。", "tool_calls": []},
    ])
    executor = AsyncMock(return_value={
        "tool": "get_creation_entity",
        "status": "ok",
        "data": {"id": "entity-1", "data": {"notes": "长资料" * 100_000}},
    })

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="读取目标角色",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("读取目标角色"),
        ))

    tool_message = next(
        item for item in result["_turn_trace"]["messages"]
        if item["role"] == "tool" and item["tool_call_id"] == "call-entity"
    )
    parsed = json.loads(tool_message["content"])
    assert parsed["status"] == "error"
    assert parsed["data"]["reason"] == "tool_result_over_capacity"
    assert "truncated" not in tool_message["content"]
    exact_result = next(
        item for item in result["tool_results"]
        if item["tool"] == "get_creation_entity"
    )
    assert exact_result["status"] == "ok"
    assert exact_result["data"]["data"]["notes"] == "长资料" * 100_000


def test_creation_agent_rejects_native_text_before_category_selection():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion({
        "content": "我已经读取并保存了设定。",
        "tool_calls": [],
    })

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), pytest.raises(LLMError, match="set_tool_categories"):
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="加入一条修炼规则",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("加入一条修炼规则"),
        ))

    assert completion.call_count == 1
    assert completion.call_args.kwargs["tool_choice"] == "required"


def test_creation_agent_records_keep_visible_turns_without_tool_replay():
    valid_messages = [
        {"role": "user", "content": "把门派名改为归墟宗"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-read",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call-read", "content": '{"status":"ok"}'},
        {"role": "assistant", "content": "已读取并完成本轮处理。"},
    ]
    conversation = {
        "messages": [
            {
                "id": "user-valid",
                "role": "user",
                "content": valid_messages[0]["content"],
                "status": "completed",
                "sequence_no": 1,
            },
            {
                "id": "assistant-valid",
                "role": "assistant",
                "content": valid_messages[-1]["content"],
                "status": "completed",
                "sequence_no": 2,
                "payload": {"creation_agent_turn": {
                    "schema": CREATION_AGENT_TURN_SCHEMA,
                    "session_id": "session-1",
                    "replayable": True,
                    "messages": valid_messages,
                    "outcome": {"status": "completed"},
                }},
            },
            {
                "id": "user-direct-mcp",
                "role": "user",
                "content": valid_messages[0]["content"],
                "status": "completed",
                "sequence_no": 3,
            },
            {
                "id": "assistant-nonreplayable-transport",
                "role": "assistant",
                "content": valid_messages[-1]["content"],
                "status": "completed",
                "sequence_no": 4,
                "payload": {"creation_agent_turn": {
                    "schema": CREATION_AGENT_TURN_SCHEMA,
                    "session_id": "session-1",
                    "replayable": False,
                    "messages": valid_messages,
                    "outcome": {"status": "completed"},
                }},
            },
        ],
    }

    records = creation_agent_turn_records(conversation, session_id="session-1")

    assert [record.assistant_message_id for record in records] == [
        "assistant-valid",
        "assistant-nonreplayable-transport",
    ]
    assert [record.user_content for record in records] == [
        valid_messages[0]["content"],
        valid_messages[0]["content"],
    ]
    assert [record.assistant_content for record in records] == [
        valid_messages[-1]["content"],
        valid_messages[-1]["content"],
    ]


def test_creation_agent_records_fail_closed_on_corrupt_historical_trace():
    trace_messages = [
        {"role": "user", "content": "修改设定"},
        {"role": "assistant", "content": "已修改。"},
    ]
    conversation = {"messages": [
        {
            "id": "user-corrupt",
            "role": "user",
            "content": "修改设定",
            "status": "completed",
            "sequence_no": 1,
        },
        {
            "id": "assistant-corrupt",
            "role": "assistant",
            "content": "已修改。",
            "status": "completed",
            "sequence_no": 2,
            "payload": {"creation_agent_turn": {
                "schema": CREATION_AGENT_TURN_SCHEMA,
                "session_id": "session-1",
                "messages": trace_messages[:-1],
                "outcome": {"status": "completed"},
            }},
        },
    ]}

    with pytest.raises(ConversationContextError) as caught:
        creation_agent_turn_records(conversation, session_id="session-1")

    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID
    assert caught.value.details["reason"] == (
        "history_turn_not_closed_or_trace_invalid"
    )


def test_creation_context_turns_never_project_historical_tool_protocol():
    messages = [
        {"role": "user", "content": "修改主角"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-categories",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": '{"enabled_categories":["creation_data"]}',
                    },
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-categories", "content": '{"status":"ok"}'},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-snapshot",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call-snapshot", "content": '{"status":"ok"}'},
        {"role": "assistant", "content": "已读取主角资料。"},
    ]
    conversation = {"messages": [{
        "id": "user-1",
        "role": "user",
        "content": messages[0]["content"],
        "status": "completed",
        "sequence_no": 1,
    }, {
        "id": "assistant-1",
        "role": "assistant",
        "content": messages[-1]["content"],
        "status": "completed",
        "sequence_no": 2,
        "payload": {"creation_agent_turn": {
            "schema": CREATION_AGENT_TURN_SCHEMA,
            "session_id": "session-1",
            "replayable": True,
            "messages": messages,
            "outcome": {"status": "completed"},
        }},
    }]}

    context_turns = creation_turns_as_context_turns(creation_agent_turn_records(
        conversation,
        session_id="session-1",
    ))

    wire = json.dumps([
        message.to_dict()
        for turn in context_turns
        for message in turn.messages
    ])
    assert "set_tool_categories" not in wire
    assert "call-categories" not in wire
    assert "get_creation_snapshot" not in wire
    assert [
        message.role.value
        for turn in context_turns
        for message in turn.messages
    ] == ["user", "assistant"]


def test_creation_context_turn_records_keep_all_closed_turns_and_exact_text():
    long_user = "作者约束" * 6_000
    long_assistant = "立项回复" * 3_000
    messages = []
    for turn_index in range(7):
        user_content = long_user if turn_index == 0 else f"用户第{turn_index + 1}轮"
        assistant_content = long_assistant if turn_index == 0 else f"助手第{turn_index + 1}轮"
        trace_messages = [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ]
        messages.extend([
            {
                "id": f"user-{turn_index}",
                "role": "user",
                "content": user_content,
                "status": "completed",
                "sequence_no": turn_index * 2 + 1,
            },
            {
                "id": f"assistant-{turn_index}",
                "role": "assistant",
                "content": assistant_content,
                "status": "completed",
                "sequence_no": turn_index * 2 + 2,
                "payload": {"creation_agent_turn": {
                    "schema": CREATION_AGENT_TURN_SCHEMA,
                    "session_id": "session-1",
                    "client_turn_id": f"turn-{turn_index}",
                    "replayable": True,
                    "messages": trace_messages,
                    "outcome": {"status": "completed"},
                }},
            },
        ])

    records = creation_agent_turn_records(
        {"messages": messages},
        session_id="session-1",
    )
    context_turns = creation_turns_as_context_turns(records)

    assert len(records) == 7
    assert records[0].user_content == long_user
    assert records[0].assistant_content == long_assistant
    assert [record.sequence for record in records] == [1, 3, 5, 7, 9, 11, 13]
    assert all(len(record.source_hash) == 64 for record in records)
    assert context_turns[0].turn_id == "turn-0"
    assert context_turns[0].status.value == "completed"
    assert [message.to_dict() for message in context_turns[0].messages] == [
        {
            "message_id": "user-0",
            "sequence_no": 1,
            "role": "user",
            "content": long_user,
            "tool_call_id": None,
            "tool_calls": [],
        },
        {
            "message_id": "assistant-0",
            "sequence_no": 2,
            "role": "assistant",
            "content": long_assistant,
            "tool_call_id": None,
            "tool_calls": [],
        },
    ]


def test_creation_context_turn_records_preserve_closed_failure_states():
    def pair(index: int, status: str, outcome_status: str = "completed") -> list[dict]:
        user_content = f"用户-{index}"
        assistant_content = f"助手-{index}"
        return [
            {
                "id": f"user-{index}",
                "role": "user",
                "content": user_content,
                "status": "completed",
                "sequence_no": index * 2 + 1,
            },
            {
                "id": f"assistant-{index}",
                "role": "assistant",
                "content": assistant_content,
                "status": status,
                "sequence_no": index * 2 + 2,
                "payload": {"creation_agent_turn": {
                    "schema": CREATION_AGENT_TURN_SCHEMA,
                    "session_id": "session-1",
                    "messages": [
                        {"role": "user", "content": user_content},
                        {"role": "assistant", "content": assistant_content},
                    ],
                    "outcome": {"status": outcome_status},
                }},
            },
        ]

    interrupted = pair(3, "interrupted", "protocol_error")
    interrupted[1]["payload"] = {
        "creation_agent_client_turn_id": "interrupted-turn",
    }
    cancelled = pair(5, "cancelled", "protocol_error")
    cancelled[1]["payload"] = {
        "creation_agent_client_turn_id": "cancelled-turn",
    }
    conversation = {
        "messages": [
            *pair(0, "completed"),
            # The conversational model turn is closed even though the
            # separately persisted stage run still owns the running badge.
            *pair(1, "running"),
            *pair(2, "error"),
            *interrupted,
            *pair(4, "error", "protocol_error"),
            *cancelled,
        ],
    }

    records = creation_agent_turn_records(conversation, session_id="session-1")

    assert [record.assistant_message_id for record in records] == [
        "assistant-0",
        "assistant-1",
        "assistant-2",
        "assistant-3",
        "assistant-4",
        "assistant-5",
    ]
    assert [record.status for record in records] == [
        "completed",
        "completed",
        "error",
        "aborted",
        "error",
        "cancelled",
    ]
    assert [record.assistant_content for record in records[2:]] == [
        "助手-2",
        "助手-3",
        "助手-4",
        "助手-5",
    ]
    assert creation_turns_as_context_turns(records)[-1].status.value == "cancelled"


def test_creation_turn_records_only_omit_explicit_current_running_pair():
    historical_trace = [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "第一轮完成"},
    ]
    messages = [
        {
            "id": "user-history",
            "role": "user",
            "content": "第一轮",
            "status": "completed",
            "sequence_no": 1,
        },
        {
            "id": "assistant-history",
            "role": "assistant",
            "content": "第一轮完成",
            "status": "completed",
            "sequence_no": 2,
            "payload": {"creation_agent_turn": {
                "schema": CREATION_AGENT_TURN_SCHEMA,
                "session_id": "session-1",
                "messages": historical_trace,
                "outcome": {"status": "completed"},
            }},
        },
        {
            "id": "user-current",
            "role": "user",
            "content": "当前轮",
            "status": "completed",
            "sequence_no": 3,
        },
        {
            "id": "assistant-current",
            "role": "assistant",
            "content": "",
            "status": "running",
            "sequence_no": 4,
        },
    ]

    records = creation_agent_turn_records(
        {"messages": messages},
        session_id="session-1",
        exclude_assistant_message_id="assistant-current",
    )
    assert [record.turn_id for record in records] == ["assistant-history"]

    with pytest.raises(ConversationContextError) as caught:
        creation_agent_turn_records(
            {"messages": messages},
            session_id="session-1",
        )
    assert caught.value.details["reason"] == (
        "history_turn_not_closed_or_trace_invalid"
    )

    messages[2]["sequence_no"] = 5
    with pytest.raises(ConversationContextError) as gap:
        creation_agent_turn_records(
            {"messages": messages},
            session_id="session-1",
            exclude_assistant_message_id="assistant-current",
        )
    assert gap.value.details["reason"] == "history_sequence_gap"


def test_creation_current_user_context_message_uses_exact_persisted_pair():
    conversation = {"messages": [
        {
            "id": "user-current",
            "role": "user",
            "content": "继续完善主角",
            "status": "completed",
            "sequence_no": 11,
        },
        {
            "id": "assistant-current",
            "role": "assistant",
            "content": "",
            "status": "running",
            "sequence_no": 12,
        },
    ]}

    current = creation_current_user_context_message(
        conversation,
        assistant_message_id="assistant-current",
        expected_content="继续完善主角",
    )

    assert current is not None
    assert current.message_id == "user-current"
    assert current.sequence_no == 11
    assert current.content == "继续完善主角"
    assert creation_current_user_context_message(
        conversation,
        assistant_message_id="assistant-current",
        expected_content="被篡改的请求",
    ) is None


def _creation_ledger_message(
    *,
    assistant_id: str,
    sequence_no: int,
    session_id: str,
    step_id: str,
    tool: str,
    result: dict,
    write_committed: bool,
    result_ref: str | None = None,
) -> dict:
    verified_result_ref = (
        result_ref
        or f"creation-tool-result:sha256:{canonical_sha256(result)}"
    )
    receipt = ToolExecutionReceipt(
        step_id=step_id,
        tool=tool,
        status=str(result["status"]),
        summary=f"{tool}:{result['status']}",
        resource_ids=(),
        result_ref=verified_result_ref,
        reread=None,
        write_committed=write_committed,
    )
    return {
        "id": assistant_id,
        "role": "assistant",
        "content": "这段展示文字不能成为执行事实。",
        "status": "completed",
        "sequence_no": sequence_no,
        "payload": {
            "creation_agent_runtime": seal_creation_runtime_snapshot({
                "session_id": session_id,
                "status": "completed",
                "tool_mode": "native",
                "tool_results": [result],
                "execution_receipts": [receipt.to_dict()],
                "compacted_tool_transactions": [],
                "pending_tool_transactions": [],
            }),
        },
    }


def test_creation_execution_ledger_keeps_only_verified_committed_artifact_ref():
    result = {
        "tool": "patch_creation_artifact",
        "status": "ok",
        "data": {"artifact": {"revision": 9}},
    }
    projection = creation_execution_ledger_from_conversation(
        {"messages": [_creation_ledger_message(
            assistant_id="assistant-write",
            sequence_no=2,
            session_id="session-1",
            step_id="creation-step:write-1",
            tool="patch_creation_artifact",
            result=result,
            write_committed=True,
        )]},
        session_id="session-1",
    )

    assert len(projection.entries) == 1
    entry = projection.entries[0]
    assert (entry.tool, entry.status) == ("patch_creation_artifact", "ok")
    assert [
        (reference.type, reference.id, reference.revision)
        for reference in entry.resource_refs
    ] == [("creation_session", "session-1", 9)]
    assert set(projection.source_hashes) == {"creation-step:write-1"}


def test_creation_execution_ledger_rejects_tampered_result_ref():
    original = {
        "tool": "patch_creation_artifact",
        "status": "ok",
        "data": {"artifact": {"revision": 3}},
    }
    tampered = {
        **original,
        "data": {"artifact": {"revision": 999}},
    }
    stale_ref = f"creation-tool-result:sha256:{canonical_sha256(original)}"
    projection = creation_execution_ledger_from_conversation(
        {"messages": [_creation_ledger_message(
            assistant_id="assistant-tampered",
            sequence_no=2,
            session_id="session-1",
            step_id="creation-step:tampered",
            tool="patch_creation_artifact",
            result=tampered,
            write_committed=True,
            result_ref=stale_ref,
        )]},
        session_id="session-1",
    )

    assert projection.entries == ()
    assert projection.source_hashes == {}


def test_creation_execution_ledger_retry_success_resolves_error():
    error_result = {
        "tool": "patch_creation_artifact",
        "status": "error",
        "data": {"reason": "revision_conflict"},
    }
    success_result = {
        "tool": "patch_creation_artifact",
        "status": "ok",
        "data": {"artifact": {"revision": 10}},
    }
    projection = creation_execution_ledger_from_conversation(
        {"messages": [
            _creation_ledger_message(
                assistant_id="assistant-error",
                sequence_no=2,
                session_id="session-1",
                step_id="creation-step:failed-attempt",
                tool="patch_creation_artifact",
                result=error_result,
                write_committed=False,
            ),
            _creation_ledger_message(
                assistant_id="assistant-success",
                sequence_no=4,
                session_id="session-1",
                step_id="creation-step:successful-retry",
                tool="patch_creation_artifact",
                result=success_result,
                write_committed=True,
            ),
        ]},
        session_id="session-1",
    )

    assert [entry.step_id for entry in projection.entries] == [
        "creation-step:successful-retry"
    ]
    assert projection.entries[0].resource_refs[0].revision == 10


def test_creation_execution_ledger_never_copies_raw_error_reason():
    result = {
        "tool": "patch_creation_artifact",
        "status": "error",
        "data": {"reason": "provider_secret=do-not-copy"},
    }
    projection = creation_execution_ledger_from_conversation(
        {"messages": [_creation_ledger_message(
            assistant_id="assistant-error",
            sequence_no=2,
            session_id="session-1",
            step_id="creation-step:stable-error",
            tool="patch_creation_artifact",
            result=result,
            write_committed=False,
        )]},
        session_id="session-1",
    )

    assert len(projection.entries) == 1
    assert projection.entries[0].error_code == "creation_tool_failed"
    assert "provider_secret" not in repr(projection.entries[0])


def test_creation_execution_ledger_does_not_accumulate_successful_reads():
    result = {
        "tool": "get_creation_snapshot",
        "status": "ok",
        "data": {"session": {"id": "session-1", "revision": 7}},
    }
    projection = creation_execution_ledger_from_conversation(
        {"messages": [_creation_ledger_message(
            assistant_id="assistant-read",
            sequence_no=2,
            session_id="session-1",
            step_id="creation-step:read-1",
            tool="get_creation_snapshot",
            result=result,
            write_committed=False,
        )]},
        session_id="session-1",
    )

    assert projection.entries == ()
    assert projection.source_hashes == {}


def test_creation_execution_ledger_accepts_server_verified_direct_mcp_write():
    result = {
        "tool": "mcp_verified_write",
        "status": "ok",
        "detail": "MCP 写入已验证",
        "data": {
            "session_id": "session-1",
            "revision_before": 11,
            "revision_after": 12,
        },
    }
    projection = creation_execution_ledger_from_conversation(
        {"messages": [_creation_ledger_message(
            assistant_id="assistant-direct-mcp",
            sequence_no=2,
            session_id="session-1",
            step_id="creation-step:direct-mcp-write",
            tool="mcp_verified_write",
            result=result,
            write_committed=True,
        )]},
        session_id="session-1",
    )

    assert len(projection.entries) == 1
    assert [
        (reference.type, reference.id, reference.revision)
        for reference in projection.entries[0].resource_refs
    ] == [("creation_session", "session-1", 12)]


def test_creation_agent_rejects_non_creation_tools_even_if_model_requests_one():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-groups",
                    "type": "function",
                    "function": {"name": "set_tool_categories", "arguments": '{"enabled_categories":["project_files"]}'},
                },
            ],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-invalid",
                "type": "function",
                "function": {"name": "delete_project", "arguments": "{}"},
            }],
        },
        {"content": "没有执行越权操作。", "tool_calls": []},
    ])
    executor = AsyncMock()

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ), pytest.raises(ConversationContextError) as caught:
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="继续处理立项",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("继续处理立项"),
        ))

    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID
    assert caught.value.details["reason"] == "native_tool_not_open"
    assert caught.value.details["tool"] == "delete_project"
    executor.assert_not_awaited()
    assert completion.call_count == 2


def test_creation_agent_returns_a_deterministic_formal_project_handoff():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-groups",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": '{"enabled_categories":["creation_data","creation_flow"]}',
                    },
                },
            ],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-finalize",
                "type": "function",
                "function": {"name": "finalize_creation_session", "arguments": "{}"},
            }],
        },
        {"content": "项目建好了，我们继续在这里写第一章。", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {"tool": "get_creation_snapshot", "status": "ok", "data": {"revision": session.revision}},
        {
            "tool": "finalize_creation_session",
            "status": "ok",
            "detail": "Project created",
            "data": {"project_id": "formal-project-1"},
        },
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
            "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="确认创建正式作品",
            model="openai:test",
            prepare_model_messages=_test_context_preparer("确认创建正式作品"),
        ))

    assert result["created_project_id"] == "formal-project-1"
    assert "点击下方按钮进入正式作品" in result["reply"]
    assert "继续在这里写第一章" not in result["reply"]


def test_known_non_opencode_cli_uses_direct_session_scoped_mcp():
    db = _db()
    session = _ready_session(db)
    baseline_revision = int(session.revision or 0)
    captured_requests: list[dict] = []

    async def completion_response(**kwargs):
        captured_requests.append(kwargs)
        state_file = kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"]
        if len(captured_requests) == 1:
            replace_tool_categories(state_file, ["creation_data"])
            return {"content": "", "tool_calls": []}
        db.query(type(session)).filter(type(session).id == session.id).update(
            {"revision": baseline_revision + 1},
            synchronize_session=False,
        )
        db.commit()
        return {"content": "已通过临时 MCP 写入并回读确认。", "tool_calls": []}

    completion = _stream_completion(completion_response)
    executor = AsyncMock()

    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="claude_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
            "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="生成文风与世界观，基调要厚重史诗",
            model="claude_cli:claude-code",
            prepare_model_messages=_test_context_preparer(
                "生成文风与世界观，基调要厚重史诗"
            ),
        ))

    first_request = captured_requests[0]
    assert len(captured_requests) == 2
    assert first_request["tools"] == []
    assert first_request["extra_body"]["local_cli_isolated"] is True
    assert first_request["extra_body"]["local_cli_mcp_authorized"] is True
    assert first_request["extra_body"]["local_cli_allow_mcp"] is True
    assert first_request["extra_body"]["local_cli_timeout_seconds"] == 0
    assert first_request["extra_body"]["local_cli_quiet_seconds"] == 120
    assert first_request["extra_body"]["local_cli_suspected_stall_seconds"] == 300
    assert first_request["extra_body"]["local_cli_stalled_seconds"] == 600
    assert first_request["timeout"] == 0
    assert first_request["extra_body"]["local_cli_retry_attempts"] == 1
    assert first_request["retry"] == 0
    assert [item["role"] for item in first_request["messages"]].count("system") == 1
    assert "临时 Siming MCP" in first_request["messages"][0]["content"]
    assert "方案数量完全服从用户语义" in first_request["messages"][0]["content"]
    assert "用户未指定数量时只生成一套" in first_request["messages"][0]["content"]
    assert "不要创建或宣称创建后台生成任务" in first_request["messages"][0]["content"]
    executor.assert_not_awaited()
    assert result["write_count"] == 1
    assert "临时 MCP" in result["reply"]


def test_custom_cli_requires_a_known_mcp_protocol():
    db = _db()
    session = _ready_session(db)
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="custom_cli",
    ), pytest.raises(LLMError, match="没有可验证的 MCP 启动协议"):
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="把测试写入创作约束",
            model="custom_cli:custom-cli",
            prepare_model_messages=_test_context_preparer("把测试写入创作约束"),
        ))


def test_direct_cli_rejects_text_before_category_controller_call():
    db = _db()
    session = _ready_session(db)

    async def completion_response(**_kwargs):
        return {
            "content": "当前工具还不可用，我先等待。",
            "tool_calls": [],
        }

    completion = _stream_completion(completion_response)
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), pytest.raises(LLMError, match="set_tool_categories"):
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="玄幻",
            model="opencode_cli:opencode/big-pickle",
            prepare_model_messages=_test_context_preparer("玄幻"),
        ))


def test_direct_cli_creation_continues_past_the_old_six_step_limit():
    db = _db()
    session = _ready_session(db)
    call_count = 0

    async def completion_response(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 7:
            replace_tool_categories(
                kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"],
                ["creation_data" if call_count % 2 else "creation_flow"],
            )
            return {"content": "", "tool_calls": []}
        return {"content": "已在超过旧上限后完成立项检查。", "tool_calls": []}

    completion = _stream_completion(completion_response)
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="连续检查多轮",
            model="opencode_cli:opencode/big-pickle",
            prepare_model_messages=_test_context_preparer("连续检查多轮"),
        ))

    assert call_count == 8
    assert result["reply"] == "已在超过旧上限后完成立项检查。"


def test_opencode_uses_direct_session_scoped_mcp():
    db = _db()
    session = _ready_session(db)
    baseline_revision = int(session.revision or 0)
    captured_requests: list[dict] = []
    context_steps: list[dict] = []

    async def completion_response(**kwargs):
        captured_requests.append(kwargs)
        state_file = kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"]
        if len(captured_requests) == 1:
            replace_tool_categories(state_file, ["creation_data"])
            return {"content": "", "tool_calls": []}
        append_tool_category_audit(
            state_file,
            {
                "tool": "patch_creation_session",
                "arguments": {"changes": {"target_words": 2_500_000}},
                "status": "ok",
                "result": {"status": "ok", "data": {"revision": baseline_revision + 1}},
            },
        )
        db.query(type(session)).filter(type(session).id == session.id).update(
            {"revision": baseline_revision + 1},
            synchronize_session=False,
        )
        db.commit()
        return {
            "content": "已通过临时 Siming MCP 更新立项目标，并回读确认 revision 已变化。",
            "tool_calls": [],
        }

    completion = _stream_completion(completion_response)
    executor = AsyncMock()
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
            "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="把目标改为250万字和1000章",
            model="opencode_cli:opencode/big-pickle",
            prepare_model_messages=_test_context_preparer(
                "把目标改为250万字和1000章",
                captured=context_steps,
            ),
            local_cli_read_paths=[r"D:\references\brief.md"],
        ))

    executor.assert_not_awaited()
    assert result["write_count"] == 1
    assert any(
        item["tool"] == "mcp_verified_write"
        for item in result["tool_results"]
    )
    verified_result = next(
        item
        for item in result["tool_results"]
        if item["tool"] == "mcp_verified_write"
    )
    assert result["_turn_trace"]["execution_receipts"] == [
        {
            **result["_turn_trace"]["execution_receipts"][0],
            "tool": "mcp_verified_write",
            "status": "ok",
            "result_ref": (
                "creation-tool-result:sha256:"
                f"{canonical_sha256(verified_result)}"
            ),
            "write_committed": True,
        }
    ]
    assert len(captured_requests) == 2
    assert len(context_steps) == 2
    assert all(step["current_tools"] == () for step in context_steps)
    assert all(
        step["provider_protocol_state"]["protocol"] == "direct_mcp"
        for step in context_steps
    )
    assert context_steps[0]["provider_protocol_state"]["tool_schemas"][0][
        "function"
    ]["name"] == "set_tool_categories"
    assert any(
        schema["function"]["name"] == "get_creation_snapshot"
        for schema in context_steps[1]["provider_protocol_state"]["tool_schemas"]
    )
    first_request = captured_requests[0]
    assert first_request["tools"] == []
    assert first_request["extra_body"]["local_cli_isolated"] is True
    assert first_request["extra_body"]["local_cli_mcp_authorized"] is True
    assert first_request["extra_body"]["local_cli_allow_mcp"] is True
    assert first_request["extra_body"]["local_cli_read_permission_granted"] is True
    assert first_request["extra_body"]["local_cli_read_paths"] == [r"D:\references\brief.md"]
    assert first_request["extra_body"]["local_cli_mcp_permission_pack"] == "creation_session"
    assert first_request["extra_body"]["local_cli_mcp_creation_session_id"] == session.id
    assert first_request["extra_body"]["local_cli_timeout_seconds"] == 0
    assert first_request["extra_body"]["local_cli_retry_attempts"] == 1
    assert first_request["extra_body"]["local_cli_resume_incomplete_opencode"] is True
    assert first_request["retry"] == 0
    assert "临时 Siming MCP" in first_request["messages"][0]["content"]
    assert "set_tool_categories" in first_request["messages"][0]["content"]
    assert "get_creation_snapshot" in captured_requests[1]["messages"][0]["content"]
    assert "不要为了确认而再次读取" in captured_requests[1]["messages"][0]["content"]
    assert result["_turn_trace"]["replayable"] is False
    assert result["_turn_trace"]["direct_mcp_calls"] == [{
        "tool": "patch_creation_session",
        "arguments": {"changes": {"target_words": 2_500_000}},
        "status": "ok",
        "result": {"status": "ok", "data": {"revision": baseline_revision + 1}},
    }]


def test_direct_cli_does_not_start_a_third_summary_call_after_verified_write():
    db = _db()
    session = _ready_session(db)
    baseline_revision = int(session.revision or 0)
    call_count = 0

    async def completion_response(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            replace_tool_categories(
                kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"],
                ["creation_data"],
            )
            return {"content": "", "tool_calls": []}
        db.query(type(session)).filter(type(session).id == session.id).update(
            {"revision": baseline_revision + 1},
            synchronize_session=False,
        )
        db.commit()
        return {"content": "", "tool_calls": []}

    completion = _stream_completion(completion_response)
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="只生成一个创意方向",
            model="opencode_cli:opencode/big-pickle",
            prepare_model_messages=_test_context_preparer("只生成一个创意方向"),
        ))

    assert call_count == 2
    assert result["write_count"] == 1
    assert "revision" in result["reply"]


def test_direct_cli_cancels_a_runaway_process_after_a_second_write_is_blocked():
    db = _db()
    session = _ready_session(db)
    baseline_revision = int(session.revision or 0)
    call_count = 0

    async def completion_response(**kwargs):
        nonlocal call_count
        call_count += 1
        state_file = kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"]
        if call_count == 1:
            replace_tool_categories(state_file, ["creation_data"])
            return {"content": "", "tool_calls": []}
        db.query(type(session)).filter(type(session).id == session.id).update(
            {"revision": baseline_revision + 1},
            synchronize_session=False,
        )
        db.commit()
        append_tool_category_event(state_file, {
            "type": "tool_completed",
            "message": "本条用户消息已经成功写入一次，后续写入已拦截",
            "data": {
                "tool": "confirm_creation_artifact",
                "status": "denied",
                "turn_boundary": "successful_write_limit",
            },
        })
        await asyncio.Future()

    completion = _stream_completion(completion_response)
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="下一步",
            model="opencode_cli:opencode/big-pickle",
            prepare_model_messages=_test_context_preparer("下一步"),
        ))

    assert call_count == 2
    assert result["write_count"] == 1
    assert "后续自动写入已被系统拦截" in result["reply"]
    assert "下一步要处理哪个单一对象" in result["reply"]


def test_direct_cli_transport_error_after_committed_write_returns_verified_success():
    db = _db()
    session = _ready_session(db)
    baseline_revision = int(session.revision or 0)

    call_count = 0

    async def completion_response(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            replace_tool_categories(
                kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"],
                ["creation_data"],
            )
            return {"content": "", "tool_calls": []}
        db.query(type(session)).filter(type(session).id == session.id).update(
            {"revision": baseline_revision + 1},
            synchronize_session=False,
        )
        db.commit()
        raise LLMError("OpenCode 模型连接在生成过程中中断")

    completion = _stream_completion(completion_response)
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="生成一个创意方向",
            model="opencode_cli:opencode/big-pickle",
            prepare_model_messages=_test_context_preparer("生成一个创意方向"),
        ))

    assert result["write_count"] == 1
    assert any(
        item["tool"] == "mcp_verified_write"
        for item in result["tool_results"]
    )
    assert "revision" in result["reply"]


def test_direct_cli_interruption_settles_stage_run_created_by_stale_mcp_surface():
    db = _db()
    session = _ready_session(db)

    call_count = 0

    async def completion_response(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            replace_tool_categories(
                kwargs["extra_body"]["local_cli_mcp_tool_category_state_file"],
                ["creation_flow"],
            )
            return {"content": "", "tool_calls": []}
        create_run(db, session, "concepts", {
            "operation": "generate",
            "model": "opencode_cli:opencode/big-pickle",
        })
        db.commit()
        raise LLMError("CLI connection closed")

    completion = _stream_completion(completion_response)
    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        side_effect=lambda _model, base=None, **_kwargs: dict(base or {}),
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), pytest.raises(LLMError, match="connection closed"):
        asyncio.run(run_creation_agent(
            db,
            session=session,
            message="生成创意方向",
            model="opencode_cli:opencode/big-pickle",
            prepare_model_messages=_test_context_preparer("生成创意方向"),
        ))

    run = db.query(NovelCreationStageRun).filter_by(session_id=session.id).one()
    operation = db.get(OperationRun, run.operation_id)
    assert run.status == "interrupted"
    assert run.failure_class == "interrupted"
    assert run.events[-1].event_type == "interrupted"
    assert operation is not None
    assert operation.status == "interrupted"
    assert operation.health_status == "disconnected"


def test_creation_agent_resolves_default_model_once_and_propagates_it_to_generation():
    from types import SimpleNamespace

    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-groups-default-model",
                    "type": "function",
                    "function": {
                        "name": "set_tool_categories",
                        "arguments": '{"enabled_categories":["creation_data","creation_flow"]}',
                    },
                },
            ],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-read-default-model",
                "type": "function",
                "function": {"name": "get_creation_snapshot", "arguments": "{}"},
            }],
        },
        {
            "content": "",
            "tool_calls": [{
                "id": "call-generate-default-model",
                "type": "function",
                "function": {
                    "name": "generate_creation_artifact",
                    "arguments": json.dumps({
                        "artifact": "concepts",
                        "model": None,
                        "use_model": False,
                    }),
                },
            }],
        },
        {"content": "已使用当前有效模型开始生成创意方向。", "tool_calls": []},
    ])
    executor = AsyncMock(side_effect=[
        {"tool": "get_creation_snapshot", "status": "ok", "data": {"revision": session.revision}},
        {
            "tool": "generate_creation_artifact",
            "status": "running",
            "data": {"run": {"id": "run-model", "status": "running"}},
        },
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.select_model_for_task",
        return_value=SimpleNamespace(model="openai:resolved-default"),
    ) as select_model, patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=True,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="openai",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
            "app.services.creation_agent_execution.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="生成创意方向",
            model="siming",
            prepare_model_messages=_test_context_preparer("生成创意方向"),
        ))

    select_model.assert_called_once_with(
        task_type="planning",
        model_override=None,
    )
    assert completion.call_args_list[0].kwargs["model"] == "openai:resolved-default"
    write_arguments = executor.call_args_list[1].args[2]["arguments"]
    assert write_arguments["model"] == "openai:resolved-default"
    assert write_arguments["use_model"] is True
    assert result["run"]["id"] == "run-model"
