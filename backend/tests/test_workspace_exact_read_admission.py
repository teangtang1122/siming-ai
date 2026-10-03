"""Context reads use their complete projected result while preserving rollback."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Project
from app.services.workspace.assistant_native_turn import NativeStepCapture, WorkspaceNativeTurn
from app.services.workspace.assistant_turn_state import WorkspaceAssistantTurnState
from app.services.workspace.registry import registry
from tests.tool_budget_helpers import request_budget


@pytest.mark.parametrize("tool_name", ["get_project_info", "prepare_task_context", "prepare_external_writing_context", "search_chapters", "search_context"])
def test_context_read_admits_actual_result_and_rolls_back_rejected_result(tool_name: str):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        project = Project(id=str(uuid4()), title="原题")
        db.add(project)
        db.commit()
        executions = 0

        async def execute_action(session, project_id, action, **kwargs):
            nonlocal executions
            executions += 1
            row = session.get(Project, project_id)
            row.title = "暂存标题"
            session.flush()
            return {
                "tool": tool_name, "status": "ready", "detail": "prepared",
                "data": {"context_page": {"text": "正文" * 1000}},
            }

        state = WorkspaceAssistantTurnState(
            db=db, project_id=project.id, payload=SimpleNamespace(model="fixture"),
            selected_provider="fixture", supports_function_calling=True,
            local_cli_selected=False, local_cli_mcp_enabled=False,
            encode_event=json.dumps, execute_action=execute_action, prepare_context=None,
        )
        state.assistant_run = SimpleNamespace(id="run-1")
        state.assistant_message = SimpleNamespace(id="message-1")
        state.workspace_tool_name_set = {tool_name}
        turn = WorkspaceNativeTurn(state, None, registry)
        call = {"id": "call-1", "type": "function", "function": {
            "name": tool_name, "arguments": '{"task_type":"writing"}',
        }}

        state.request_budget = request_budget(12_000)
        _, accepted_error = asyncio.run(turn._admit(NativeStepCapture(), [call], 1))
        assert accepted_error is None
        assert executions == 1
        assert "call-1" in turn._staged_results
        assert db.get(Project, project.id).title == "暂存标题"
        db.rollback()
        assert db.get(Project, project.id).title == "原题"

        state.request_budget = request_budget(500)
        _, denied_error = asyncio.run(turn._admit(NativeStepCapture(), [call], 2))
        assert denied_error is not None
        assert denied_error.reason == "tool_result_batch_over_capacity"
        assert executions == 2
        assert not turn._staged_results
        assert db.get(Project, project.id).title == "原题"
        db.rollback()
    engine.dispose()


def test_two_project_fields_fit_without_reserving_two_maximum_documents():
    from app.routers.ai_writer import _execute_workspace_action

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        project = Project(id=str(uuid4()), title="真实小结果", writing_style="natural")
        db.add(project)
        db.commit()
        state = WorkspaceAssistantTurnState(
            db=db, project_id=project.id, payload=SimpleNamespace(model="fixture"),
            selected_provider="fixture", supports_function_calling=True,
            local_cli_selected=False, local_cli_mcp_enabled=False,
            encode_event=json.dumps, execute_action=_execute_workspace_action, prepare_context=None,
        )
        state.assistant_run = SimpleNamespace(id="project-fields")
        state.assistant_message = SimpleNamespace(id="project-fields-message")
        state.workspace_tool_name_set = {"get_project_info"}
        state.request_budget = request_budget(2_000)
        turn = WorkspaceNativeTurn(state, None, registry)
        calls = [
            {"id": f"field-{field}", "type": "function", "function": {
                "name": "get_project_info", "arguments": json.dumps({"field": field}),
            }}
            for field in ("writing_style", "forbidden_sentence_patterns")
        ]
        _, error = asyncio.run(turn._admit(NativeStepCapture(), calls, 1))
        assert error is None
        assert len(turn._staged_results) == 2
        assert all(item[0]["status"] == "ok" for item in turn._staged_results.values())
        assert "natural" in turn._staged_results["field-writing_style"][1]
    engine.dispose()


def test_two_context_reads_admit_exact_batch_and_roll_back_together():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        project = Project(id=str(uuid4()), title="原题")
        db.add(project)
        db.commit()
        executions: list[str] = []

        async def execute_action(session, project_id, action, **kwargs):
            name = action["tool"]
            executions.append(name)
            row = session.get(Project, project_id)
            row.title = f"暂存{len(executions)}"
            session.flush()
            return {
                "tool": name, "status": "ready", "detail": "prepared",
                "data": {"context_page": {"text": "正文" * 200}},
            }

        state = WorkspaceAssistantTurnState(
            db=db, project_id=project.id, payload=SimpleNamespace(model="fixture"),
            selected_provider="fixture", supports_function_calling=True,
            local_cli_selected=False, local_cli_mcp_enabled=False,
            encode_event=json.dumps, execute_action=execute_action,
            prepare_context=None,
        )
        state.assistant_run = SimpleNamespace(id="run-2")
        state.assistant_message = SimpleNamespace(id="message-2")
        state.workspace_tool_name_set = {"prepare_task_context"}
        turn = WorkspaceNativeTurn(state, None, registry)
        calls = [
            {"id": f"call-{index}", "type": "function", "function": {
                "name": "prepare_task_context",
                "arguments": '{"task_type":"writing"}',
            }}
            for index in (1, 2)
        ]

        state.request_budget = request_budget(4_000)
        transaction, accepted_error = asyncio.run(turn._admit(
            NativeStepCapture(), calls, 1,
        ))
        assert accepted_error is None
        assert len(transaction.calls) == 2
        assert executions == ["prepare_task_context"] * 2
        assert set(turn._staged_results) == {"call-1", "call-2"}
        assert db.get(Project, project.id).title == "暂存2"
        db.rollback()
        assert db.get(Project, project.id).title == "原题"

        state.request_budget = request_budget(500)
        _, denied_error = asyncio.run(turn._admit(NativeStepCapture(), calls, 2))
        assert denied_error is not None
        assert denied_error.call_count == 2
        assert len(executions) == 4
        assert not turn._staged_results
        assert db.get(Project, project.id).title == "原题"
        db.rollback()
    engine.dispose()


def test_character_and_worldbuilding_reads_admit_their_actual_small_batch():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        project = Project(id=str(uuid4()), title="测试作品")
        db.add(project)
        db.commit()
        executed: list[str] = []

        async def execute_action(session, project_id, action, **kwargs):
            name = action["tool"]
            executed.append(name)
            if name == "search_characters":
                return {
                    "tool": name, "status": "ok", "detail": "找到 1 个角色",
                    "data": [{"id": "character-1", "name": "陆生", "role_type": "protagonist",
                              "fields": {"personality": "谨慎"}}],
                }
            return {
                "tool": name, "status": "ok", "detail": "共 1 个世界观条目",
                "data": [{"id": "world-1", "title": "潮汐规则", "dimension": "rules"}],
            }

        state = WorkspaceAssistantTurnState(
            db=db, project_id=project.id, payload=SimpleNamespace(model="fixture"),
            selected_provider="fixture", supports_function_calling=True,
            local_cli_selected=False, local_cli_mcp_enabled=False,
            encode_event=json.dumps, execute_action=execute_action, prepare_context=None,
        )
        state.assistant_run = SimpleNamespace(id="run-reads")
        state.assistant_message = SimpleNamespace(id="message-reads")
        state.workspace_tool_name_set = {"search_characters", "list_worldbuilding"}
        state.request_budget = request_budget(4_000)
        turn = WorkspaceNativeTurn(state, None, registry)
        calls = [
            {"id": "character-call", "type": "function", "function": {
                "name": "search_characters", "arguments": '{"query":"陆生"}',
            }},
            {"id": "world-call", "type": "function", "function": {
                "name": "list_worldbuilding", "arguments": '{"limit":10}',
            }},
        ]

        transaction, error = asyncio.run(turn._admit(NativeStepCapture(), calls, 1))
        assert error is None
        assert len(transaction.calls) == 2
        assert executed == ["search_characters", "list_worldbuilding"]
        assert set(turn._staged_results) == {"character-call", "world-call"}
        db.rollback()
    engine.dispose()


def test_category_switch_uses_exact_receipt_without_applying_selection_early():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        state = WorkspaceAssistantTurnState(
            db=db, project_id=str(uuid4()), payload=SimpleNamespace(model="fixture"),
            selected_provider="fixture", supports_function_calling=True,
            local_cli_selected=False, local_cli_mcp_enabled=False,
            encode_event=json.dumps, execute_action=None, prepare_context=None,
        )
        state.assistant_run = SimpleNamespace(id="run-3")
        state.assistant_message = SimpleNamespace(id="message-3")
        state.authorized_tool_names = {"list_chapters"}
        state.request_budget = request_budget(1_000)
        turn = WorkspaceNativeTurn(state, None, registry)
        call = {"id": "category", "type": "function", "function": {
            "name": "set_tool_categories",
            "arguments": '{"enabled_categories":["chapters"]}',
        }}

        transaction, error = asyncio.run(turn._admit(NativeStepCapture(), [call], 1))
        assert error is None
        assert transaction.calls[0].name == "set_tool_categories"
        assert not state.category_selected
        assert not turn._staged_results
    engine.dispose()
