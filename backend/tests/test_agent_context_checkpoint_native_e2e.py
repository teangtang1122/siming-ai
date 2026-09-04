"""Full-chain regressions for checkpointed native Agent turns."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.database.models import ModelContextProfile, Project
from app.mcp.server import handle_message
from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    ConversationContextCheckpoint,
    ConversationContextState,
)
from app.modules.assistant.infrastructure.system_conversations import (
    SqlAlchemySystemConversationStore,
)
from app.services.creation_agent_turn_runtime import (
    CreationAgentTurnInput,
    produce_creation_agent_turn,
)
from app.services.novel_creation_agent import CREATION_AGENT_TURN_SCHEMA
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace
from app.services.tool_category_state import (
    bind_tool_category_turn_guard,
    create_tool_category_state,
    remove_tool_category_state,
)
from app.services.workspace.assistant_direct_mcp_turn import WorkspaceDirectMcpTurn
from app.services.workspace.assistant_turn_state import (
    WorkspaceAssistantTurnState,
    WorkspaceTurnSuperseded,
)
from app.services.workspace.run_log import mark_assistant_run
from tests.test_novel_creation_workspace_v2 import _db, _ready_session


def _native_stream(responses: list[dict], captured: list[dict]) -> MagicMock:
    remaining = iter(responses)

    def invoke(**kwargs):
        captured.append(deepcopy(kwargs))
        response = next(remaining)

        async def generate():
            content = str(response.get("content") or "")
            if content:
                yield {"type": "content_delta", "delta": content}
            for index, call in enumerate(response.get("tool_calls") or []):
                function = call["function"]
                arguments = function.get("arguments", "{}")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                yield {
                    "type": "tool_call_delta",
                    "index": index,
                    "id": call["id"],
                    "name": function["name"],
                    "arguments_delta": arguments,
                }
            yield {
                "type": "done",
                "finish_reason": (
                    "tool_calls" if response.get("tool_calls") else "stop"
                ),
                "usage": response.get("usage"),
            }

        return generate()

    return MagicMock(side_effect=invoke)


def _checkpoint_completion_recorder(captured: list[dict]):
    async def completion(**kwargs):
        captured.append(kwargs)
        return {
            "content": json.dumps(
                {
                    "schema": "conversation_checkpoint_navigation.v1",
                    "semantic_navigation": {
                        "authority": "non_authoritative_navigation",
                        "current_objectives": ["核对立项快照"],
                        "resolved_decisions": [],
                        "superseded_directions": [],
                        "unresolved_questions": [],
                        "next_context_needed": ["读取真实立项快照"],
                    },
                    "author_quote_positions": [],
                    "prior_author_quote_states": [],
                },
                ensure_ascii=False,
            )
        }

    return completion


def test_creation_long_history_checkpoint_then_native_category_and_read() -> None:
    db = _db()
    session = _ready_session(db)
    db.add(
        ModelContextProfile(
            provider="openai",
            model_name="test",
            context_window_tokens=300_000,
            max_output_tokens=4_096,
            safety_margin_tokens=512,
        )
    )
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.create(
        "Long creation transcript",
        scope_type="creation",
        scope_id=session.id,
    )["conversation"]["id"]
    for turn_index in range(120):
        user_content = f"old-user-{turn_index}:" + "u" * 1_400
        assistant_content = f"old-assistant-{turn_index}:" + "a" * 1_400
        turn_id = f"old-turn-{turn_index}"
        conversations.append_turn(
            conversation_id,
            {
                "user_content": user_content,
                "assistant_content": assistant_content,
                "status": "completed",
                "creation_session_id": session.id,
                "scope_type": "creation",
                "scope_id": session.id,
                "payload": {
                    "creation_agent_client_turn_id": turn_id,
                    "creation_agent_turn": {
                        "schema": CREATION_AGENT_TURN_SCHEMA,
                        "session_id": session.id,
                        "client_turn_id": turn_id,
                        "messages": [
                            {"role": "user", "content": user_content},
                            {"role": "assistant", "content": assistant_content},
                        ],
                        "outcome": {"status": "completed"},
                    },
                },
            },
        )
    latest = conversations.start_turn(
        conversation_id,
        {
            "user_content": "逐字保留：请核对当前立项快照",
            "creation_session_id": session.id,
            "scope_type": "creation",
            "scope_id": session.id,
        },
    )
    assistant_message_id = latest["messages"][1]["id"]
    db.commit()

    model_requests: list[dict] = []
    stream = _native_stream(
        [
            {
                "tool_calls": [
                    {
                        "id": "creation-category",
                        "type": "function",
                        "function": {
                            "name": "set_tool_categories",
                            "arguments": {
                                "enabled_categories": ["creation_data"]
                            },
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "creation-read",
                        "type": "function",
                        "function": {
                            "name": "get_creation_snapshot",
                            "arguments": {},
                        },
                    }
                ]
            },
            {"content": "已依据真实立项快照核对。", "tool_calls": []},
        ],
        model_requests,
    )
    executor = AsyncMock(
        return_value={
            "tool": "get_creation_snapshot",
            "status": "ok",
            "detail": "已读取当前立项快照",
            "data": {"session_id": session.id, "revision": session.revision},
        }
    )
    checkpoint_requests: list[dict] = []
    events: list[dict] = []
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    async def publish(event: dict) -> None:
        events.append(event)

    request = CreationAgentTurnInput(
        session_id=session.id,
        message="逐字保留：请核对当前立项快照",
        client_turn_id=str(uuid4()),
        model="openai:test",
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
        local_cli_read_paths=(),
    )
    with (
        patch(
            "app.services.creation_agent_turn_runtime.SessionLocal",
            new=factory,
        ),
        patch(
            "app.services.conversation_context.preparation."
            "_default_checkpoint_completion",
            return_value=_checkpoint_completion_recorder(checkpoint_requests),
        ),
        patch(
            "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
            return_value=True,
        ),
        patch(
            "app.services.novel_creation_agent.LLMGateway.provider_for_model",
            return_value="openai",
        ),
        patch(
            "app.services.novel_creation_agent.LLMGateway."
            "stream_chat_completion_with_tools",
            new=stream,
        ),
        patch(
            "app.services.creation_agent_execution.execute_workspace_action",
            new=executor,
        ),
    ):
        asyncio.run(produce_creation_agent_turn(request, publish))

    assert not [event for event in events if event.get("type") == "error"], events
    assert any(event.get("type") == "complete" for event in events)
    assert checkpoint_requests
    assert all(
        call["tools"] == [] and call["tool_choice"] == "none"
        for call in checkpoint_requests
    )
    assert stream.call_count == 3
    assert executor.await_count == 1
    assert executor.call_args.args[2]["tool"] == "get_creation_snapshot"
    first_messages = model_requests[0]["messages"]
    assert first_messages[-1] == {
        "role": "user",
        "content": "逐字保留：请核对当前立项快照",
    }
    assert any(
        "[HISTORICAL_REFERENCE_DATA]" in str(message.get("content") or "")
        for message in first_messages
    )
    delivered = model_requests[2]["messages"]
    native_call = next(message for message in delivered if message.get("tool_calls"))
    assert native_call["tool_calls"][0]["function"]["name"] == "get_creation_snapshot"
    assert any(message.get("tool_call_id") == "creation-read" for message in delivered)
    db.expire_all()
    checkpoints = (
        db.query(ConversationContextCheckpoint)
        .filter(
            ConversationContextCheckpoint.system_conversation_id
            == conversation_id
        )
        .all()
    )
    assert checkpoints
    assert any(checkpoint.status == "ready" for checkpoint in checkpoints)


def test_append_during_checkpoint_supersedes_old_creation_and_latest_continues() -> None:
    db = _db()
    session = _ready_session(db)
    db.add(
        ModelContextProfile(
            provider="openai",
            model_name="test",
            context_window_tokens=300_000,
            max_output_tokens=4_096,
            safety_margin_tokens=512,
        )
    )
    conversations = SqlAlchemySystemConversationStore(db)
    conversation_id = conversations.create(
        "Concurrent creation transcript",
        scope_type="creation",
        scope_id=session.id,
    )["conversation"]["id"]
    for turn_index in range(120):
        user_content = f"source-user-{turn_index}:" + "u" * 1_400
        assistant_content = f"source-assistant-{turn_index}:" + "a" * 1_400
        turn_id = f"source-turn-{turn_index}"
        conversations.append_turn(
            conversation_id,
            {
                "user_content": user_content,
                "assistant_content": assistant_content,
                "status": "completed",
                "creation_session_id": session.id,
                "scope_type": "creation",
                "scope_id": session.id,
                "payload": {
                    "creation_agent_client_turn_id": turn_id,
                    "creation_agent_turn": {
                        "schema": CREATION_AGENT_TURN_SCHEMA,
                        "session_id": session.id,
                        "client_turn_id": turn_id,
                        "messages": [
                            {"role": "user", "content": user_content},
                            {"role": "assistant", "content": assistant_content},
                        ],
                        "outcome": {"status": "completed"},
                    },
                },
            },
        )
    old_started = conversations.start_turn(
        conversation_id,
        {
            "user_content": "old request must stop",
            "creation_session_id": session.id,
            "scope_type": "creation",
            "scope_id": session.id,
        },
    )
    old_assistant_id = old_started["messages"][1]["id"]
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    checkpoint_requests: list[dict] = []
    newest: dict[str, str] = {}

    async def checkpoint_completion(**kwargs):
        checkpoint_requests.append(kwargs)
        if not newest:
            append_db = factory()
            try:
                appended = SqlAlchemySystemConversationStore(append_db).start_turn(
                    conversation_id,
                    {
                        "user_content": "latest user survives verbatim",
                        "creation_session_id": session.id,
                        "scope_type": "creation",
                        "scope_id": session.id,
                    },
                )
                newest["assistant_id"] = appended["messages"][1]["id"]
                append_db.commit()
            finally:
                append_db.close()
        return await _checkpoint_completion_recorder([])(**kwargs)

    old_stream = _native_stream([], [])
    old_executor = AsyncMock()
    old_events: list[dict] = []

    async def publish_old(event: dict) -> None:
        old_events.append(event)

    old_request = CreationAgentTurnInput(
        session_id=session.id,
        message="old request must stop",
        client_turn_id=str(uuid4()),
        model="openai:test",
        conversation_id=conversation_id,
        assistant_message_id=old_assistant_id,
        local_cli_read_paths=(),
    )
    with (
        patch("app.services.creation_agent_turn_runtime.SessionLocal", new=factory),
        patch(
            "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
            return_value=True,
        ),
        patch(
            "app.services.novel_creation_agent.LLMGateway.provider_for_model",
            return_value="openai",
        ),
        patch(
            "app.services.conversation_context.preparation."
            "_default_checkpoint_completion",
            return_value=checkpoint_completion,
        ),
        patch(
            "app.services.novel_creation_agent.LLMGateway."
            "stream_chat_completion_with_tools",
            new=old_stream,
        ),
        patch(
            "app.services.creation_agent_execution.execute_workspace_action",
            new=old_executor,
        ),
    ):
        asyncio.run(produce_creation_agent_turn(old_request, publish_old))

    assert checkpoint_requests
    assert any(event.get("type") == "superseded" for event in old_events)
    assert not any(event.get("type") == "complete" for event in old_events)
    old_stream.assert_not_called()
    old_executor.assert_not_awaited()
    db.expire_all()
    detail_after_old = conversations.get(conversation_id)
    old_message = next(
        message
        for message in detail_after_old["messages"]
        if message["id"] == old_assistant_id
    )
    assert old_message["status"] == "aborted"
    assert old_message["payload"]["superseded"] is True
    assert any(
        message["role"] == "user"
        and message["content"] == "latest user survives verbatim"
        for message in detail_after_old["messages"]
    )
    context_state = (
        db.query(ConversationContextState)
        .filter(ConversationContextState.system_conversation_id == conversation_id)
        .one()
    )
    assert context_state.last_budget_json
    assert any(
        checkpoint.status == "ready"
        for checkpoint in db.query(ConversationContextCheckpoint).filter(
            ConversationContextCheckpoint.system_conversation_id == conversation_id
        )
    )

    model_requests: list[dict] = []
    latest_stream = _native_stream(
        [
            {
                "tool_calls": [
                    {
                        "id": "latest-category",
                        "type": "function",
                        "function": {
                            "name": "set_tool_categories",
                            "arguments": {"enabled_categories": ["creation_data"]},
                        },
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "id": "latest-read",
                        "type": "function",
                        "function": {
                            "name": "get_creation_snapshot",
                            "arguments": {},
                        },
                    }
                ]
            },
            {"content": "latest turn completed", "tool_calls": []},
        ],
        model_requests,
    )
    latest_executor = AsyncMock(
        return_value={
            "tool": "get_creation_snapshot",
            "status": "ok",
            "detail": "snapshot read",
            "data": {"session_id": session.id, "revision": session.revision},
        }
    )
    latest_events: list[dict] = []

    async def publish_latest(event: dict) -> None:
        latest_events.append(event)

    latest_request = CreationAgentTurnInput(
        session_id=session.id,
        message="latest user survives verbatim",
        client_turn_id=str(uuid4()),
        model="openai:test",
        conversation_id=conversation_id,
        assistant_message_id=newest["assistant_id"],
        local_cli_read_paths=(),
    )
    with (
        patch("app.services.creation_agent_turn_runtime.SessionLocal", new=factory),
        patch(
            "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
            return_value=True,
        ),
        patch(
            "app.services.novel_creation_agent.LLMGateway.provider_for_model",
            return_value="openai",
        ),
        patch(
            "app.services.conversation_context.preparation."
            "_default_checkpoint_completion",
            return_value=checkpoint_completion,
        ),
        patch(
            "app.services.novel_creation_agent.LLMGateway."
            "stream_chat_completion_with_tools",
            new=latest_stream,
        ),
        patch(
            "app.services.creation_agent_execution.execute_workspace_action",
            new=latest_executor,
        ),
    ):
        asyncio.run(produce_creation_agent_turn(latest_request, publish_latest))

    assert not [event for event in latest_events if event.get("type") == "error"], latest_events
    assert any(event.get("type") == "complete" for event in latest_events), latest_events
    assert latest_executor.await_count == 1
    assert model_requests[0]["messages"][-1] == {
        "role": "user",
        "content": "latest user survives verbatim",
    }
    assert sum(
        1
        for message in model_requests[0]["messages"]
        if message.get("role") == "user"
        and message.get("content") == "latest user survives verbatim"
    ) == 1
    db.expire_all()
    final_detail = conversations.get(conversation_id)
    assert next(
        message for message in final_detail["messages"] if message["id"] == old_assistant_id
    )["status"] == "aborted"
    assert next(
        message
        for message in final_detail["messages"]
        if message["id"] == newest["assistant_id"]
    )["status"] == "completed"


def test_direct_mcp_guard_denies_superseded_workspace_before_handler() -> None:
    db = _db()
    project = Project(title="Guarded MCP")
    db.add(project)
    db.flush()
    conversation = AssistantConversation(
        project_id=project.id,
        title="Guarded transcript",
        scope="project",
    )
    db.add(conversation)
    db.flush()
    run = AssistantRun(
        project_id=project.id,
        conversation_id=conversation.id,
        status="superseded",
        scope="project",
    )
    db.add(run)
    db.commit()
    state_file = create_tool_category_state()
    bind_tool_category_turn_guard(
        state_file,
        {
            "kind": "workspace",
            "project_id": project.id,
            "conversation_id": conversation.id,
            "run_id": run.id,
        },
    )
    executor = AsyncMock()
    try:
        with patch("app.mcp.server.execute_tool", new=executor):
            response = handle_message(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "search_outline",
                            "arguments": {"query": "stale"},
                        },
                    }
                ),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
            )
        payload = json.loads(response)
        content = json.loads(payload["result"]["content"][0]["text"])
        assert payload["result"]["isError"] is True
        assert content["data"]["reason"] == "turn_superseded"
        executor.assert_not_awaited()
    finally:
        remove_tool_category_state(state_file)


def test_direct_model_text_cannot_finalize_a_superseded_workspace_run() -> None:
    db = _db()
    project = Project(title="Superseded direct turn")
    db.add(project)
    db.flush()
    conversation = AssistantConversation(
        project_id=project.id,
        title="Superseded transcript",
        scope="project",
    )
    db.add(conversation)
    db.flush()
    user = AssistantMessage(
        conversation_id=conversation.id,
        sequence_no=1,
        role="user",
        content="old request",
        status="completed",
    )
    assistant = AssistantMessage(
        conversation_id=conversation.id,
        sequence_no=2,
        role="assistant",
        content="pending",
        status="running",
    )
    db.add_all([user, assistant])
    db.flush()
    run = AssistantRun(
        project_id=project.id,
        conversation_id=conversation.id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
        status="running",
        scope="project",
    )
    db.add(run)
    db.commit()
    mark_assistant_run(
        db,
        run,
        status="superseded",
        phase="superseded_by_new_user",
        error="newer user",
        final_reply="superseded receipt",
    )
    state = WorkspaceAssistantTurnState(
        db=db,
        project_id=project.id,
        payload=SimpleNamespace(
            model="opencode_cli:test",
            temperature=0.3,
            max_tokens=1_024,
        ),
        selected_provider="opencode_cli",
        supports_function_calling=False,
        local_cli_selected=True,
        local_cli_mcp_enabled=True,
        encode_event=lambda event: json.dumps(event, ensure_ascii=False),
        execute_action=AsyncMock(),
        prepare_context=AsyncMock(),
    )
    state.workspace = SqlAlchemyAssistantWorkspace(db)
    state.conversation = conversation
    state.user_message = user
    state.assistant_message = assistant
    state.assistant_run = run
    state.turn_telemetry = MagicMock()

    class _Gateway:
        @staticmethod
        def stream_chat_completion(**_kwargs):
            async def generate():
                yield "stale model reply"

            return generate()

    async def collect() -> None:
        turn = WorkspaceDirectMcpTurn(state, _Gateway())
        async for _event in turn.run(messages=[], iteration=1):
            pass

    with pytest.raises(WorkspaceTurnSuperseded):
        asyncio.run(collect())
    db.expire_all()
    persisted_run = db.get(AssistantRun, run.id)
    persisted_message = db.get(AssistantMessage, assistant.id)
    assert persisted_run.status == "superseded"
    assert persisted_run.completed_at is not None
    assert persisted_message.status == "aborted"
    assert persisted_message.content == "superseded receipt"
    assert state.final_reply == ""
