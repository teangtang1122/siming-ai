from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.deepseek_adapter import DeepSeekAdapter
from app.core.exceptions import LLMError
from app.database.models import NovelCreationSession
from app.services.agent_tool_stream import collect_tool_turn
from app.services.creation_agent_reply import creation_receipt_reply, creation_reply_error
from app.services.novel_creation_agent import run_creation_agent
from app.services.novel_creation_workspace import (
    initialize_session_draft,
    save_stage,
    serialize_creation_artifact,
)
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry
from app.services.workspace.tools import novel_creation_v2
from tests.test_novel_creation_agent import _stream_completion, _test_context_preparer
from tests.test_novel_creation_workspace_v2 import _db, _ready_session
from tests.tool_budget_helpers import request_budget

DSML = (
    '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="get_creation_entity">'
    '<｜｜DSML｜｜ parameter name="entity_id" string="true">entity-1'
    '</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'
)
REPLY = "题材已更新为玄幻。主角有什么目标？"


def _call(name: str, arguments: dict) -> dict:
    return {"content": "", "tool_calls": [{
        "id": f"call-{name}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}


def _run_after_write(
    *summaries: dict | BaseException,
    write_available_tokens: int | None = None,
) -> tuple[dict, list[dict], MagicMock]:
    db = _db()
    session = _ready_session(db)
    baseline = int(session.revision)
    contexts: list[dict] = []
    responses = iter([
        _call("set_tool_categories", {"enabled_categories": ["creation_session"]}),
        _call("get_creation_snapshot", {}),
        _call("patch_creation_session", {"changes": {"form": {"genre": "玄幻"}}}),
        *summaries,
    ])

    def response(**_kwargs):
        item = next(responses)
        if isinstance(item, BaseException):
            raise item
        return item

    completion = _stream_completion(response)
    executor = AsyncMock(wraps=execute_workspace_action)
    def bound_budget():
        available = (
            write_available_tokens
            if write_available_tokens is not None and completion.call_count == 3
            else 250_000
        )
        return request_budget(available)

    try:
        with (
            patch("app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools", new=completion),
            patch("app.services.creation_agent_execution.execute_workspace_action", new=executor),
        ):
            result = asyncio.run(run_creation_agent(
                db, session=session, message="把题材设为玄幻", model="openai:test",
                provider_request_budget=bound_budget,
                prepare_model_messages=_test_context_preparer("把题材设为玄幻", captured=contexts),
            ))
        db.refresh(session)
        assert session.revision == baseline + 1
        assert result["write_count"] == 1
        assert [call.args[2]["tool"] for call in executor.await_args_list] == [
            "get_creation_snapshot", "patch_creation_session",
        ]
        assert any(receipt["write_committed"] for receipt in result["_turn_trace"]["execution_receipts"])
        assert result["_turn_trace"]["pending_tool_transactions"] == []
        return result, contexts, completion
    finally:
        db.close()


def test_successful_write_enters_explicit_summary_without_replanning():
    result, contexts, completion = _run_after_write({"content": REPLY})

    assert result["reply"] == REPLY
    assert completion.call_count == 4
    assert "工具已关闭" in contexts[-1]["extra_runtime_instruction"]
    assert "[SERVER_RUNTIME_INSTRUCTION]" in contexts[-1]["messages"][0]["content"]
    assert contexts[-1]["current_tools"] == ()
    assert result["_turn_trace"]["prompt_metrics"][-1]["phase"] == "summary"


def test_failed_pending_concepts_patch_cannot_be_reported_as_saved():
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting", user_brief="属性系统玄幻")
    db.add(session)
    initialize_session_draft(session)
    db.commit()
    revision = session.revision
    completion = _stream_completion([
        _call("set_tool_categories", {"enabled_categories": ["creation_artifacts"]}),
        _call("get_creation_artifact", {"artifact": "concepts"}),
        _call("patch_creation_artifact", {
            "artifact": "concepts", "expected_revision": revision,
            "changes": [{"path": "/golden_finger", "action": "set", "value": "力量、敏捷、体质"}],
        }),
        {"content": "已记录属性面板。", "tool_calls": []},
    ])
    try:
        with patch(
            "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
            new=completion,
        ):
            result = asyncio.run(run_creation_agent(
                db, session=session, message="力量、敏捷、体质等基础属性", model="openai:test",
                provider_request_budget=lambda: request_budget(250_000),
                prepare_model_messages=_test_context_preparer("力量、敏捷、体质等基础属性"),
            ))
        assert result["write_count"] == 0
        assert session.revision == revision
        assert result["reply"].startswith("本轮没有保存任何修改")
        assert "generate_creation_artifact" in result["reply"]
        assert "已记录" not in result["reply"]
    finally:
        db.close()


def test_session_genre_write_commits_with_observed_64k_remaining_budget():
    result, _, _ = _run_after_write(
        {"content": REPLY}, write_available_tokens=7_034,
    )
    receipt = next(
        item for item in result["_turn_trace"]["execution_receipts"]
        if item["tool"] == "patch_creation_session"
    )
    assert receipt["status"] == "ok"
    assert receipt["write_committed"] is True


def test_large_entity_patch_delivers_success_to_summary_without_replaying_write():
    db = _db()
    session = _ready_session(db)
    characters = serialize_creation_artifact(session, "characters")["data"]
    characters["characters"][0]["background"] = "完整角色背景。" * 500
    save_stage(session, "characters", characters)
    db.commit()
    entity = next(item for item in session.entities if item.entity_type == "character")
    entity_id = entity.id
    baseline = session.revision
    limit = registry.get("patch_creation_entity").model_result_contract.max_json_bytes
    reply = "角色姓名已更新为林遥，原有背景已保留。"
    steps = iter([
        _call("set_tool_categories", {"enabled_categories": ["creation_entities"]}),
        _call("get_creation_entity", {"entity_id": entity_id}),
        _call("patch_creation_entity", {
            "entity_id": entity_id,
            "changes": [{"action": "set", "path": "/name", "value": "林遥"}],
        }),
    ])

    def response(**kwargs):
        if kwargs["tools"]:
            return next(steps)
        receipt_message = kwargs["messages"][-1]
        assert receipt_message["role"] == "tool"
        receipt = json.loads(receipt_message["content"])
        assert receipt["status"] == "ok"
        assert receipt["data"]["entity"]["id"] == entity_id
        assert receipt["data"]["entity"]["revision"] == baseline + 1
        assert "data" not in receipt["data"]["artifact"]
        assert len(receipt_message["content"].encode("utf-8")) <= limit
        return {"content": reply}

    completion = _stream_completion(response)
    executor = AsyncMock(wraps=execute_workspace_action)
    try:
        with (
            patch("app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools", new=completion),
            patch("app.services.creation_agent_execution.execute_workspace_action", new=executor),
        ):
            result = asyncio.run(run_creation_agent(
                db, session=session, message="把主角改名为林遥", model="deepseek:deepseek-flash",
                provider_request_budget=request_budget,
                prepare_model_messages=_test_context_preparer("把主角改名为林遥"),
            ))
        assert result["reply"] == reply
        assert result["_turn_trace"]["outcome"]["reply_diagnostics"] == []
        assert result["write_count"] == 1
        assert completion.call_count == 4
        assert executor.await_count == 2
        db.refresh(session)
        assert session.revision == baseline + 1
        raw_write = next(item for item in result["tool_results"] if item["tool"] == "patch_creation_entity")
        assert len(json.dumps(raw_write["data"]["artifact"], ensure_ascii=False).encode("utf-8")) > limit
        assert db.get(type(entity), entity_id).data_json["background"] == "完整角色背景。" * 500
        assert result["_turn_trace"]["pending_tool_transactions"] == []
    finally:
        db.close()


def test_generated_outline_delivers_saved_facts_to_summary_once(monkeypatch):
    with _db() as db:
        session = _ready_session(db)
        outline = deepcopy(session.draft_json["stages"]["macro_outline"]["data"])
        draft = deepcopy(session.draft_json)
        draft["stages"]["macro_outline"] = {"status": "pending", "data": None}
        session.draft_json = draft
        db.commit()
        baseline = session.revision
        reply = "全书主线与卷纲已生成并保存，等待审阅确认。"
        steps = iter([
            _call("set_tool_categories", {"enabled_categories": ["creation_generation","creation_session"]}),
            _call("get_creation_snapshot", {}),
            _call("generate_creation_artifact", {"artifact": "macro_outline", "entity_type": "volume"}),
        ])

        def response(**kwargs):
            if kwargs["tools"]:
                return next(steps)
            receipt_message = kwargs["messages"][-1]
            receipt = json.loads(receipt_message["content"])
            assert receipt["data"]["saved"] is True
            assert receipt["data"]["requires_confirmation"] is True
            assert receipt["data"]["status"] == "generated"
            assert receipt["data"]["revision"] == baseline + 1
            assert receipt["data"]["collection_counts"] == {"volumes": len(outline["volumes"])}
            assert "session" not in receipt["data"] and "run" not in receipt["data"]
            return {"content": reply}

        def generate(**kwargs):
            async def stream():
                yield json.dumps({"data": outline}, ensure_ascii=False)
            return stream()

        monkeypatch.setattr(novel_creation_v2.LLMGateway, "stream_chat_completion", generate)
        completion = _stream_completion(response)
        with patch("app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools", new=completion):
            result = asyncio.run(run_creation_agent(
                db, session=session, message="生成首版卷纲", model="openai:test",
                provider_request_budget=request_budget,
                prepare_model_messages=_test_context_preparer("生成首版卷纲"),
            ))
        assert result["reply"] == reply
        assert result["write_count"] == 1
        assert completion.call_count == 4
        assert session.revision == baseline + 1
        assert result["_turn_trace"]["pending_tool_transactions"] == []


@pytest.mark.parametrize("bad_summary", [
    {"content": DSML},
    {"content": "已经保存。\n" + DSML},
    {"content": DSML.replace("｜｜", "｜")},
    {"content": DSML.replace("｜", "|")},
    {"content": DSML.replace("<", "&lt;")},
    {"content": "<｜｜DSML  "},
    {"content": ""},
    _call("patch_creation_session", {"changes": {"form": {"genre": "科幻"}}}),
])
def test_invalid_summary_is_repaired_without_replaying_any_tool(bad_summary):
    result, contexts, completion = _run_after_write(bad_summary, {"content": REPLY})

    assert result["reply"] == REPLY
    assert completion.call_count == 5
    assert all(context["current_tools"] == () for context in contexts[3:])
    visible = "".join(
        event["data"].get("delta", "") for event in result["_turn_trace"]["progress_events"]
        if event["type"] == "reply_delta"
    )
    assert visible == REPLY
    assert "DSML" not in json.dumps(result["_turn_trace"]["messages"], ensure_ascii=False)


def test_repeated_invalid_summary_stops_with_explicit_receipt_and_preserves_write():
    result, contexts, completion = _run_after_write({"content": DSML}, {"content": DSML})

    assert completion.call_count == 5
    assert "DSML" not in result["reply"]
    assert "总结" in result["reply"]
    assert "已保存" in result["reply"]
    assert result["_turn_trace"]["outcome"]["reply_status"] == "receipt_only"
    assert all(context["current_tools"] == () for context in contexts[3:])


def test_summary_transport_failure_does_not_undo_or_replay_a_committed_write():
    result, _, completion = _run_after_write(LLMError("response disconnected"))

    assert completion.call_count == 4
    assert "已保存" in result["reply"]
    assert result["_turn_trace"]["outcome"]["reply_diagnostics"] == [
        {"reason": "summary_request_failed", "attempt": 1},
    ]


def test_receipt_for_a_started_task_does_not_claim_its_contents_are_complete():
    receipt = {"tool": "generate_creation_artifact", "status": "running", "detail": ""}
    reply = creation_receipt_reply([receipt], [receipt], tool_mode="native")

    assert "任务已启动" in reply
    assert "已保存" not in reply
    assert "已完成" not in reply


def test_created_project_closes_receipts_without_requesting_another_model_step():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        _call("set_tool_categories", {"enabled_categories": ["creation_completion","creation_session"]}),
        _call("get_creation_snapshot", {}),
        _call("finalize_creation_session", {}),
    ])
    try:
        with patch("app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools", new=completion):
            result = asyncio.run(run_creation_agent(
                db, session=session, message="把这个立项创建为正式作品", model="openai:test",
                provider_request_budget=request_budget,
                prepare_model_messages=_test_context_preparer("把这个立项创建为正式作品"),
            ))
        assert completion.call_count == 3
        assert result["created_project_id"]
        assert "正式作品已创建" in result["reply"]
        assert result["_turn_trace"]["pending_tool_transactions"] == []
        assert result["_turn_trace"]["outcome"]["reply_status"] == "project_created"
    finally:
        db.close()


def test_deepseek_api_tool_free_stream_preserves_content_for_validation_not_execution():
    async def chunks():
        for start in range(0, len(DSML), 7):
            yield SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=DSML[start:start + 7]), finish_reason=None,
            )])
        yield SimpleNamespace(choices=[SimpleNamespace(delta=None, finish_reason="stop")])

    adapter = DeepSeekAdapter(api_key="test-placeholder")
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=chunks())
    adapter._get_client = MagicMock(return_value=client)
    response = asyncio.run(collect_tool_turn(
        adapter, messages=[{"role": "user", "content": "总结已完成的写入"}],
        model="deepseek-flash", tools=[], tool_choice=None,
    ))

    assert response["content"] == DSML
    assert response["tool_calls"] == []
    assert creation_reply_error(response["content"], response["tool_calls"]) == "tool_protocol_text"
    sent = client.chat.completions.create.await_args.kwargs
    assert sent["model"] == "deepseek-flash"
    assert "tools" not in sent
    assert "tool_choice" not in sent


@pytest.mark.parametrize("markup", [
    "<tool_call><function=patch_creation_artifact></function></tool_call>",
    "&lt;tool_call&gt;&lt;function=patch_creation_artifact&gt;",
    '<｜｜DSML｜｜ invoke name="patch_creation_artifact">',
])
def test_reply_contract_rejects_tool_protocol_text(markup):
    assert creation_reply_error(markup, []) == "tool_protocol_text"


def test_creation_agent_repairs_tool_markup_instead_of_showing_it_to_author():
    db = _db()
    session = _ready_session(db)
    completion = _stream_completion([
        _call("set_tool_categories", {"enabled_categories": ["creation_session"]}),
        _call("get_creation_snapshot", {}),
        {"content": "<tool_call><function=patch_creation_artifact></function></tool_call>",
         "tool_calls": []},
        {"content": "已读取立项资料，本轮没有保存修改。", "tool_calls": []},
    ])

    with patch(
        "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
        new=completion,
    ):
        result = asyncio.run(run_creation_agent(
            db,
            session=session,
            message="查看立项状态",
            model="openai:test",
            provider_request_budget=request_budget,
            prepare_model_messages=_test_context_preparer("查看立项状态"),
        ))

    assert result["reply"] == (
        "本轮只读取了立项资料，尚未生成或保存新的阶段资料。\n\n"
        "已读取立项资料，本轮没有保存修改。"
    )
    assert result["_turn_trace"]["outcome"]["reply_diagnostics"] == [
        {"reason": "tool_protocol_text", "attempt": 1},
    ]
