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
from app.services.novel_creation_agent import (
    CREATION_AGENT_TURN_SCHEMA,
    creation_agent_replay_messages,
    run_creation_agent,
)
from app.services.novel_creation_runs import create_run
from app.services.tool_category_state import (
    append_tool_category_audit,
    append_tool_category_event,
    replace_tool_categories,
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
            yield event

        return generate()

    return MagicMock(side_effect=invoke)


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


def test_creation_session_list_keeps_its_single_router_path():
    store = type("EmptyCreationStore", (), {"sessions": lambda self, **_kwargs: []})()
    with patch("app.routers.novel_creation.novel_creation_session_store", return_value=store):
        response = asyncio.run(list_creation_sessions(db=object()))

    assert response.data == {"sessions": []}


def test_creation_agent_collects_streamed_content_and_tool_calls():
    async def stream(**_kwargs):
        yield {"type": "content_delta", "delta": "先读取"}
        yield {"type": "tool_call_delta", "index": 0, "id": "call-1", "name": "get_creation_snapshot"}
        yield {"type": "tool_call_delta", "index": 0, "arguments_delta": "{}"}
        yield {
            "type": "done",
            "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
        }

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=stream,
    ):
        result = asyncio.run(collect_tool_turn(model_executor, messages=[], tools=[]))

    assert result["content"] == "先读取"
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


def test_creation_agent_endpoint_persists_and_replays_backend_owned_tool_history():
    db = _db()
    session = _ready_session(db)
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
    agent = AsyncMock(side_effect=[
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
        "app.services.creation_agent_turn_runtime.SessionLocal",
        new=session_factory,
    ):
        first_events, reattached_events, second_events = asyncio.run(run_turns())

    assert next(event for event in first_events if event["type"] == "complete")["data"]["turn_persisted"] is True
    assert next(event for event in second_events if event["type"] == "complete")["data"]["turn_persisted"] is True
    assert next(event for event in reattached_events if event["type"] == "complete")["data"]["reply"] == "已完成读取。"
    assert agent.await_count == 2
    assert agent.call_args_list[0].kwargs["replay_messages"] == []
    assert agent.call_args_list[1].kwargs["replay_messages"] == [
        first_messages[0],
        first_messages[-1],
    ]
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


def test_creation_agent_lets_model_select_categories_then_call_creation_tools():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        {
            "content": "",
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
            replay_messages=[
                {"role": "user", "content": "这是仙侠小说"},
                {"role": "assistant", "content": "已记录为仙侠方向。"},
            ],
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
        ))

    assert executor.await_count == 2
    denied = next(
        item for item in result["tool_results"]
        if item["tool"] == "patch_creation_session" and item["status"] == "denied"
    )
    assert denied["data"]["reason"] == "read_required"
    assert result["write_count"] == 1


def test_native_tool_message_is_valid_json_when_exact_entity_is_oversized():
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
        ))

    tool_message = next(
        item for item in result["_turn_trace"]["messages"]
        if item["role"] == "tool" and item["tool_call_id"] == "call-entity"
    )
    parsed = json.loads(tool_message["content"])
    assert parsed["status"] == "ok"
    assert len(tool_message["content"]) < 90_000


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
        ))

    assert completion.call_count == 1
    assert completion.call_args.kwargs["tool_choice"] == "required"


def test_creation_agent_replay_keeps_only_conversation_and_skips_invalid_turns():
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
                "id": "assistant-valid",
                "role": "assistant",
                "payload": {"creation_agent_turn": {
                    "schema": CREATION_AGENT_TURN_SCHEMA,
                    "session_id": "session-1",
                    "replayable": True,
                    "messages": valid_messages,
                    "outcome": {"status": "completed"},
                }},
            },
            {
                "id": "assistant-corrupt",
                "role": "assistant",
                "payload": {"creation_agent_turn": {
                    "schema": CREATION_AGENT_TURN_SCHEMA,
                    "session_id": "session-1",
                    "replayable": True,
                    "messages": valid_messages[:-2] + [valid_messages[-1]],
                    "outcome": {"status": "completed"},
                }},
            },
            {
                "id": "assistant-nonreplayable-transport",
                "role": "assistant",
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

    replay = creation_agent_replay_messages(conversation, session_id="session-1")

    assert replay == [valid_messages[0], valid_messages[-1]]


def test_creation_agent_replay_removes_all_tool_protocol_messages():
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
        "id": "assistant-1",
        "role": "assistant",
        "payload": {"creation_agent_turn": {
            "schema": CREATION_AGENT_TURN_SCHEMA,
            "session_id": "session-1",
            "replayable": True,
            "messages": messages,
            "outcome": {"status": "completed"},
        }},
    }]}

    replay = creation_agent_replay_messages(conversation, session_id="session-1")

    wire = json.dumps(replay)
    assert "set_tool_categories" not in wire
    assert "call-categories" not in wire
    assert "get_creation_snapshot" not in wire
    assert [message["role"] for message in replay] == ["user", "assistant"]


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

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db, session=session, message="继续处理立项", model="openai:test",
        ))

    invalid = next(item for item in result["tool_results"] if item["tool"] == "delete_project")
    assert invalid["status"] == "skipped"
    assert "当前未向立项会话开放" in invalid["detail"]


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
        ))


def test_opencode_uses_direct_session_scoped_mcp():
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
            local_cli_read_paths=[r"D:\references\brief.md"],
        ))

    executor.assert_not_awaited()
    assert result["write_count"] == 1
    assert any(
        item["tool"] == "mcp_verified_write"
        for item in result["tool_results"]
    )
    assert len(captured_requests) == 2
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
