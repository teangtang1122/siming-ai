from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.services.novel_creation_agent import run_creation_agent
from tests.test_novel_creation_workspace_v2 import _db, _ready_session


def test_creation_agent_lets_model_read_then_call_any_creation_tool():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(side_effect=[
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
        "app.services.novel_creation_agent.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="在世界观里加入两条修炼规则",
            model="openai:test",
            history=[{"role": "user", "content": "这是仙侠小说"}],
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


def test_creation_agent_rejects_non_creation_tools_even_if_model_requests_one():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(side_effect=[
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

    assert result["tool_results"][0]["status"] == "skipped"
    assert "不属于立项会话" in result["tool_results"][0]["detail"]


def test_local_cli_provider_uses_mcp_without_api_tool_schema():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(return_value={
        "content": "我可以继续协助完善世界观。",
        "tool_calls": [],
    })
    executor = AsyncMock()

    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        return_value={"local_cli_allow_mcp": True},
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ), patch(
        "app.services.novel_creation_agent.execute_workspace_action",
        new=executor,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="生成文风与世界观，基调要厚重史诗",
            model="opencode_cli:opencode/deepseek-v4-flash-free",
        ))

    first_request = completion.call_args_list[0].kwargs
    assert first_request["tools"] == []
    assert first_request["extra_body"]["local_cli_allow_mcp"] is True
    assert first_request["extra_body"].get("local_cli_timeout_seconds", 600) == 600
    assert [item["role"] for item in first_request["messages"]].count("system") == 1
    assert "本机 Agent CLI" in first_request["messages"][0]["content"]
    assert "siming_patch_creation_artifact" in first_request["messages"][0]["content"]
    assert "写后读取" in first_request["messages"][0]["content"]
    assert "JSON actions" not in first_request["messages"][0]["content"]
    executor.assert_not_awaited()
    assert result["write_count"] == 0
    assert "继续协助" in result["reply"]


def test_local_cli_write_is_counted_only_after_database_revision_advances():
    db = _db()
    session = _ready_session(db)
    baseline = session.revision

    async def completion(**_kwargs):
        session.revision = baseline + 1
        db.commit()
        return {"content": "已通过 MCP 写入并完成复查。", "tool_calls": []}

    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        return_value={"local_cli_allow_mcp": True},
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="把测试写入创作约束",
            model="opencode_cli:opencode/deepseek-v4-flash-free",
        ))

    assert result["write_count"] == 1
    assert result["tool_results"][0]["tool"] == "mcp_verified_write"
    assert result["tool_results"][0]["data"]["revision_before"] == baseline
    assert result["tool_results"][0]["data"]["revision_after"] == baseline + 1


def test_local_cli_write_claim_is_rejected_when_revision_does_not_change():
    db = _db()
    session = _ready_session(db)
    completion = AsyncMock(return_value={
        "content": "已通过 MCP 写入创作约束。",
        "tool_calls": [],
    })

    with patch(
        "app.services.novel_creation_agent.LLMGateway.supports_tool_calling",
        return_value=False,
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.provider_for_model",
        return_value="opencode_cli",
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.local_cli_extra_body",
        return_value={"local_cli_allow_mcp": True},
    ), patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="写入测试",
            model="opencode_cli:opencode/deepseek-v4-flash-free",
        ))

    assert result["write_count"] == 0
    assert "没有保存任何修改" in result["reply"]
