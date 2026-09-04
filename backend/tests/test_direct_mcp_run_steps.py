"""Durable evidence regressions for workspace Direct-MCP calls."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.architecture.tool_categories import TOOL_CATEGORY_METADATA
from app.database.models import (
    AssistantRunStep,
    Base,
    ChapterDraft,
    Character,
    OutlineNode,
    Project,
)
from app.mcp.adapter import _log_mcp_tool_call, _safe_rollback
from app.mcp.server import _close_failed_scoped_workspace_step, handle_message, serve_stdio
from app.modules.assistant.infrastructure.models import AssistantConversation, AssistantRun
from app.modules.operations.infrastructure.models import OperationRun
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace
from app.services.tool_category_state import (
    activate_tool_categories,
    bind_tool_category_turn_guard,
    create_tool_category_state,
    read_tool_category_audits,
    remove_tool_category_state,
    replace_tool_categories,
)
from app.services.workspace.assistant_direct_mcp_turn import (
    DirectMcpCapture,
    WorkspaceDirectMcpTurn,
)
from app.services.workspace.assistant_turn_state import WorkspaceAssistantTurnState
from app.services.workspace.conversation_context_adapter import (
    workspace_execution_ledger_from_run_steps,
)
from app.services.workspace.direct_mcp_run_log import (
    DIRECT_MCP_CALL_KEY,
    begin_workspace_direct_mcp_step,
    issue_workspace_direct_mcp_lease,
)
from app.services.workspace.registry import registry
from app.services.workspace.run_log import finish_run_step
from app.services.workspace.terminal_draft_detection import local_cli_terminal_draft


@pytest.fixture(autouse=True)
def _keep_operation_events_in_test_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.workspace.run_log.record_operation_signal",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.workspace.assistant_response.record_operation_signal",
        lambda *_args, **_kwargs: None,
    )


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _workspace_run(db, title: str) -> tuple[Project, AssistantConversation, AssistantRun]:
    project = Project(title=title)
    db.add(project)
    db.flush()
    conversation = AssistantConversation(
        project_id=project.id,
        title=f"{title} conversation",
        scope="project",
    )
    db.add(conversation)
    db.flush()
    run = AssistantRun(
        project_id=project.id,
        conversation_id=conversation.id,
        status="running",
        scope="project",
    )
    db.add(run)
    db.flush()
    operation = OperationRun(
        source_kind="assistant",
        source_id=run.id,
        project_id=project.id,
        title=title,
        status="running",
    )
    db.add(operation)
    db.flush()
    run.operation_id = operation.id
    db.commit()
    return project, conversation, run


def _lease(db, run: AssistantRun, *, iteration: int = 2) -> str:
    return issue_workspace_direct_mcp_lease(db, run, iteration=iteration)


def _scoped_state_file(
    project: Project,
    conversation: AssistantConversation,
    run: AssistantRun,
    *,
    iteration: int = 2,
    categories: list[str] | None = None,
) -> str:
    state_file = create_tool_category_state()
    bind_tool_category_turn_guard(
        state_file,
        {
            "kind": "workspace",
            "project_id": project.id,
            "conversation_id": conversation.id,
            "run_id": run.id,
            "iteration": iteration,
        },
    )
    replace_tool_categories(state_file, categories or ["story_knowledge"])
    activate_tool_categories(state_file)
    return state_file


def _create_character_call(
    project_id: str,
    *,
    call_id: int = 7,
    background: str = "守护山门的执事",
) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": "create_character",
                "arguments": {
                    "project_id": project_id,
                    "name": "沈砚",
                    "background": background,
                },
            },
        },
        ensure_ascii=False,
    )


def _create_outline_nodes_call(project_id: str, *, call_id: int = 9) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": "create_outline_nodes",
                "arguments": {
                    "project_id": project_id,
                    "nodes": [
                        {"title": "第一章 雾门", "node_type": "chapter"},
                        {"title": "第二章 石阶", "node_type": "chapter"},
                    ],
                },
            },
        },
        ensure_ascii=False,
    )


def _tool_call(tool_name: str, arguments: dict, *, call_id: int) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        ensure_ascii=False,
    )


def test_scoped_direct_mcp_write_persists_run_step_refs_and_replays_receipt() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP durable write")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    try:
        raw_call = _create_character_call(project.id)
        first = json.loads(
            handle_message(
                raw_call,
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert first["result"]["isError"] is False
        wire_payload = json.loads(first["result"]["content"][0]["text"])
        assert "current_version" not in (wire_payload.get("data") or {})

        steps = db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).all()
        characters = db.query(Character).filter(Character.project_id == project.id).all()
        assert len(steps) == 1
        assert len(characters) == 1
        step = steps[0]
        assert step.tool == "create_character"
        assert step.step_type == "write"
        assert step.iteration == 2
        assert step.status == "ok"
        assert json.loads(step.output_refs or "{}") == {
            "character": {
                "id": characters[0].id,
                "revision": 1,
            }
        }
        request = json.loads(step.request_json or "{}")
        assert request["project_id"] == project.id
        assert request["_context_execution_route"] == "external_mcp"
        assert request[DIRECT_MCP_CALL_KEY].startswith(f"direct_mcp:{run.id}:2:")

        ledger = workspace_execution_ledger_from_run_steps(
            conversation,
            (run,),
            tuple(steps),
            project_id=project.id,
        )
        assert len(ledger) == 1
        assert ledger[0].step_id == step.id
        assert [
            (reference.type, reference.id, reference.revision)
            for reference in ledger[0].resource_refs
        ] == [("character", characters[0].id, 1)]

        second = json.loads(
            handle_message(
                raw_call,
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert second["result"]["isError"] is False
        assert db.query(Character).filter(Character.project_id == project.id).count() == 1
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 1
        audits = read_tool_category_audits(state_file)
        assert audits[-1]["assistant_run_step_id"] == step.id
        assert audits[-1]["result_ref"] == f"assistant_run_step:{step.id}"
        assert audits[-1]["replayed"] is True

        changed = json.loads(
            handle_message(
                _create_character_call(
                    project.id,
                    background="同一个调用 ID 被换成了另一组参数",
                ),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert changed["result"]["isError"] is True
        assert db.query(Character).filter(Character.project_id == project.id).count() == 1
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 1
        changed_audit = read_tool_category_audits(state_file)[-1]
        assert changed_audit["status"] == "denied"
        assert "不同参数" in changed_audit["result"]["detail"]
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_direct_mcp_batch_write_refs_use_full_raw_result() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP outline refs")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    try:
        response = json.loads(
            handle_message(
                _create_outline_nodes_call(project.id),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert response["result"]["isError"] is False
        nodes = (
            db.query(OutlineNode)
            .filter(OutlineNode.project_id == project.id)
            .order_by(OutlineNode.sort_order.asc())
            .all()
        )
        step = (
            db.query(AssistantRunStep)
            .filter(
                AssistantRunStep.run_id == run.id,
                AssistantRunStep.tool == "create_outline_nodes",
            )
            .one()
        )
        assert json.loads(step.output_refs or "{}") == {
            "outline": [{"id": nodes[0].id}, {"id": nodes[1].id}]
        }
        raw_result = json.loads(step.result_json or "{}")
        assert [item["id"] for item in raw_result["data"]["nodes"]] == [
            nodes[0].id,
            nodes[1].id,
        ]
        ledger = workspace_execution_ledger_from_run_steps(
            conversation,
            (run,),
            (step,),
            project_id=project.id,
        )
        assert [reference.id for reference in ledger[0].resource_refs] == [
            nodes[0].id,
            nodes[1].id,
        ]
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_ready_direct_mcp_receipt_replays_as_usable_without_handler() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP ready replay")
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=["story_knowledge"],
    )
    lease_token = _lease(db, run)
    call_id = 11
    arguments = {"project_id": project.id, "query": "主角"}
    started = begin_workspace_direct_mcp_step(
        db,
        state_file=state_file,
        project_id=project.id,
        tool_name="search_characters",
        arguments=dict(arguments),
        call_id=call_id,
        is_write=False,
        lease_token=lease_token,
    )
    ready_result = {
        "tool": "search_characters",
        "status": "ready",
        "detail": "上下文已准备",
        "data": {"items": []},
    }
    finish_run_step(
        db,
        started.step,
        status="ready",
        result=ready_result,
        detail=ready_result["detail"],
    )
    executor = AsyncMock()
    try:
        with patch("app.mcp.server.execute_tool", new=executor):
            response = json.loads(
                handle_message(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": call_id,
                            "method": "tools/call",
                            "params": {
                                "name": "search_characters",
                                "arguments": arguments,
                            },
                        }
                    ),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert response["result"]["isError"] is False
        replayed = json.loads(response["result"]["content"][0]["text"])
        assert replayed["status"] == "ready"
        executor.assert_not_awaited()
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 1
    finally:
        remove_tool_category_state(state_file)
        db.close()


@pytest.mark.parametrize("persisted_status", [
    None, "error", "failed", "denied", "blocked_rebuild", "skipped", "needs_confirmation",
])
def test_direct_mcp_final_text_never_creates_write_evidence(persisted_status: str | None) -> None:
    from app.services.workspace.assistant_response import _workspace_outcome

    persisted_failure = persisted_status is not None
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP prose is not evidence")
    state_file = _scoped_state_file(project, conversation, run, iteration=1)
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
    state.assistant_run = run
    state.tool_category_state_file = state_file
    state.category_selected = True
    state.active_categories = ("story_knowledge",)
    state.observed_category_version = 1

    class _Gateway:
        @staticmethod
        def stream_chat_completion(**_kwargs):
            async def generate():
                yield "我已经写入角色 fake-character-id，revision=999。"

            return generate()

    async def collect() -> None:
        async for _event in WorkspaceDirectMcpTurn(state, _Gateway()).run(
            messages=[],
            iteration=1,
        ):
            pass

    try:
        if persisted_failure:
            db.add(AssistantRunStep(
                run_id=run.id, project_id=project.id, step_type="write",
                tool="create_character", status=persisted_status, iteration=1,
                error="private-provider-diagnostic", detail="private-provider-diagnostic",
            ))
            db.commit()
        asyncio.run(collect())
        steps = db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).all()
        assert len(steps) == int(persisted_failure)
        assert db.query(Character).filter(Character.project_id == project.id).count() == 0
        assert workspace_execution_ledger_from_run_steps(
            conversation,
            (run,),
            (),
            project_id=project.id,
        ) == ()
        assert "fake-character-id" in state.final_reply
        assert bool(state.tool_logs) == persisted_failure
        if persisted_failure:
            assert state.tool_logs[0]["status"] == "error"
            assert "private-provider-diagnostic" not in json.dumps(state.tool_logs)
            assert _workspace_outcome(
                state.final_reply, applied_actions=state.applied_actions,
                tool_logs=state.tool_logs, searched_context=[], failed_logs=state.tool_logs,
            ) == "failed"
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_tampered_guard_cannot_redirect_fixed_mcp_project_or_run() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Bound MCP project")
    foreign_project, foreign_conversation, foreign_run = _workspace_run(db, "Foreign MCP project")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    bind_tool_category_turn_guard(
        state_file,
        {
            "kind": "workspace",
            "project_id": foreign_project.id,
            "conversation_id": foreign_conversation.id,
            "run_id": foreign_run.id,
            "iteration": 2,
        },
    )
    try:
        response = json.loads(
            handle_message(
                _create_character_call(project.id),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert response["result"]["isError"] is False
        step = db.query(AssistantRunStep).one()
        assert step.run_id == run.id
        assert step.project_id == project.id
        assert step.iteration == 2
        assert db.query(Character).filter(Character.project_id == project.id).count() == 1
        assert db.query(Character).filter(Character.project_id == foreign_project.id).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_direct_mcp_preflight_denials_never_leave_running_steps() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP preflight")
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=["story_knowledge"],
    )
    lease_token = _lease(db, run)
    try:
        unauthorized = json.loads(
            handle_message(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 31,
                        "method": "tools/call",
                        "params": {
                            "name": "delete_character",
                            "arguments": {
                                "project_id": project.id,
                                "character_id": "not-allowed",
                            },
                        },
                    }
                ),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert unauthorized["result"]["isError"] is True
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 0

        replace_tool_categories(state_file, ["cataloging"])
        activate_tool_categories(state_file)
        handler = AsyncMock()
        with patch("app.services.workspace.executor.execute_workspace_action", new=handler):
            confirmation_denied = json.loads(
                handle_message(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 32,
                            "method": "tools/call",
                            "params": {
                                "name": "cancel_cataloging_job",
                                "arguments": {
                                    "project_id": project.id,
                                    "job_id": "job-1",
                                },
                            },
                        }
                    ),
                    db=db,
                    project_id=project.id,
                        permission_pack="project_management",
                        tool_category_state_file=state_file,
                        direct_mcp_lease_token=lease_token,
                )
            )
        assert confirmation_denied["result"]["isError"] is True
        handler.assert_not_awaited()
        assert db.query(AssistantRunStep).filter(AssistantRunStep.run_id == run.id).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_lease_token_is_required_rotated_and_fenced_by_operation_state() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP lease fence")
    state_file = _scoped_state_file(project, conversation, run)
    old_token = _lease(db, run)
    handler = AsyncMock()
    try:
        with patch("app.mcp.server.execute_tool", new=handler):
            for call_id, token in ((41, ""), (42, "x" * 40)):
                response = json.loads(
                    handle_message(
                        _create_character_call(project.id, call_id=call_id),
                        db=db,
                        project_id=project.id,
                        permission_pack="project_management",
                        tool_category_state_file=state_file,
                        direct_mcp_lease_token=token,
                    )
                )
                assert response["result"]["isError"] is True
        handler.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 0

        new_token = _lease(db, run, iteration=3)
        with patch("app.mcp.server.execute_tool", new=handler):
            stale = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=43),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=old_token,
                )
            )
        assert stale["result"]["isError"] is True
        handler.assert_not_awaited()

        operation = db.get(OperationRun, run.operation_id)
        assert operation is not None
        operation.status = "cancelled"
        db.commit()
        with patch("app.mcp.server.execute_tool", new=handler):
            cancelled = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=44),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=new_token,
                )
            )
        assert cancelled["result"]["isError"] is True
        handler.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_lease_rejects_advanced_iteration_and_cross_project_conversation() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP owner fence")
    state_file = _scoped_state_file(project, conversation, run)
    token = _lease(db, run)
    try:
        run.current_iteration = 3
        db.commit()
        advanced = json.loads(
            handle_message(
                _create_character_call(project.id, call_id=51),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=token,
            )
        )
        assert advanced["result"]["isError"] is True
        assert db.query(AssistantRunStep).count() == 0

        token = _lease(db, run, iteration=4)
        foreign_project = Project(title="Foreign conversation project")
        db.add(foreign_project)
        db.flush()
        conversation.project_id = foreign_project.id
        db.commit()
        mismatched = json.loads(
            handle_message(
                _create_character_call(project.id, call_id=52),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=token,
            )
        )
        assert mismatched["result"]["isError"] is True
        assert db.query(AssistantRunStep).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_handler_exception_closes_once_and_replay_never_reexecutes(caplog) -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP exception")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    private_failure = "secret-token /private/project/chapter-content"
    executor = AsyncMock(side_effect=RuntimeError(private_failure))
    try:
        caplog.set_level(logging.ERROR, logger="app.mcp.adapter")
        with patch("app.services.workspace.executor.execute_workspace_action", new=executor):
            first = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=61),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
            replay = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=61),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert first["result"]["isError"] is True
        assert replay["result"]["isError"] is True
        executor.assert_awaited_once()
        step = db.query(AssistantRunStep).one()
        assert step.status == "error"
        assert step.completed_at is not None
        assert step.output_refs is None
        assert db.query(Character).count() == 0
        assert private_failure not in caplog.text
        assert "secret-token" not in caplog.text
        assert "/private/project" not in caplog.text
        assert "RuntimeError" in caplog.text
        assert "traceback_code=" in caplog.text
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_direct_commit_failure_logs_no_chained_secret_and_rolls_back() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP commit failure")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    private_failure = "secret manuscript /private/project/chapter.txt"
    try:
        with (
            patch(
                "app.mcp.adapter._safe_commit",
                side_effect=RuntimeError(private_failure),
            ),
            patch("app.mcp.server.logger.error") as safe_log,
        ):
            response = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=611),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )

        assert response["result"]["isError"] is True
        assert db.query(Character).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "error"
        assert step.output_refs is None
        rendered = repr(safe_log.call_args_list)
        assert private_failure not in rendered
        assert "/private/project" not in rendered
        assert "McpResultAuditError" in rendered
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_rollback_failure_log_contains_only_safe_exception_metadata(caplog) -> None:
    private_failure = "secret rollback content /private/project/chapter.txt"

    class FailingRollback:
        @staticmethod
        def rollback() -> None:
            raise RuntimeError(private_failure)

    caplog.set_level(logging.ERROR, logger="app.mcp.adapter")
    _safe_rollback(FailingRollback())

    assert private_failure not in caplog.text
    assert "/private/project" not in caplog.text
    assert "RuntimeError" in caplog.text
    assert "traceback_code=" in caplog.text


def test_direct_stream_error_log_does_not_emit_cli_stderr_or_secret(caplog) -> None:
    private_failure = "api_key=secret-value /private/mcp-config manuscript"

    async def failed_stream():
        if False:
            yield ""
        raise RuntimeError(private_failure)

    state = SimpleNamespace(
        payload=SimpleNamespace(model="opencode", temperature=0.3, max_tokens=100),
        local_cli_mcp_enabled=True,
        local_cli_extra_body={},
        assistant_run=SimpleNamespace(id="run-safe-log"),
        turn_telemetry=SimpleNamespace(report_model_activity=lambda *_a, **_k: None),
        event=lambda payload: payload,
    )
    requests = []

    def request_stream(**kwargs):
        requests.append(kwargs)
        return failed_stream()

    gateway = SimpleNamespace(stream_chat_completion=request_stream)
    turn = WorkspaceDirectMcpTurn(state, gateway)
    capture = DirectMcpCapture()

    async def collect() -> list[dict]:
        return [event async for event in turn._collect(capture, [])]

    caplog.set_level(
        logging.ERROR,
        logger="app.services.workspace.assistant_direct_mcp_turn",
    )
    events = asyncio.run(collect())

    assert capture.stream_error is not None
    assert events[-1]["tool"] == "stream_error"
    assert private_failure not in caplog.text
    assert "/private/mcp-config" not in caplog.text
    assert "RuntimeError" in caplog.text
    # A long managed step can read and write without the short completion
    # deadline, but failures still cannot automatically replay MCP mutations.
    assert requests[0]["timeout"] == 1_800
    assert requests[0]["retry"] == 0
    assert requests[0]["resume"] == 0


def test_direct_stream_rate_limit_is_classified_without_leaking_provider_text() -> None:
    secret = "api_key=private-value /private/provider/request"
    state = SimpleNamespace(
        payload=SimpleNamespace(model="opencode"),
        local_cli_mcp_enabled=True,
        tool_logs=[],
        final_reply="",
        final_model="",
        final_usage=None,
    )
    turn = WorkspaceDirectMcpTurn(state, SimpleNamespace())

    turn._complete_interrupted(RuntimeError(f"HTTP 429 rate limit exceeded {secret}"))

    assert state.tool_logs == [{
        "tool": "stream_error",
        "status": "error",
        "detail": "模型额度已耗尽或请求受限，请稍后重试或切换模型。",
        "error_code": "model_quota_or_rate_limit",
        "failure_class": "quota_or_rate_limit",
    }]
    assert "模型额度已耗尽或请求受限" in state.final_reply
    assert "没有自动重启" in state.final_reply
    assert secret not in state.final_reply


def test_direct_stream_provider_overload_is_reported_as_model_unavailable() -> None:
    state = SimpleNamespace(
        payload=SimpleNamespace(model="opencode"),
        local_cli_mcp_enabled=True,
        tool_logs=[],
        final_reply="",
        final_model="",
        final_usage=None,
    )
    turn = WorkspaceDirectMcpTurn(state, SimpleNamespace())

    turn._complete_interrupted(
        RuntimeError(
            "Streaming response failed: [502] Upstream error from Nvidia: "
            "Service temporarily overloaded"
        )
    )

    assert state.tool_logs == [{
        "tool": "stream_error",
        "status": "error",
        "detail": "当前模型暂不可用，请切换模型或稍后重试。",
        "error_code": "model_unavailable",
        "failure_class": "unavailable",
    }]
    assert "当前模型暂不可用" in state.final_reply
    assert "没有自动重启" in state.final_reply
    assert "Nvidia" not in state.final_reply


def test_direct_mcp_retried_schema_skip_stays_in_audit_without_partial_warning() -> None:
    steps = [
        SimpleNamespace(
            project_id="project-1",
            iteration=3,
            tool="search_characters",
            status="skipped",
        ),
        SimpleNamespace(
            project_id="project-1",
            iteration=3,
            tool="search_characters",
            status="ok",
        ),
    ]
    state = SimpleNamespace(
        local_cli_mcp_enabled=True,
        project_id="project-1",
        assistant_run=SimpleNamespace(id="run-1"),
        workspace=SimpleNamespace(run_steps=lambda _run_id: steps),
        tool_logs=[],
    )

    WorkspaceDirectMcpTurn(state, SimpleNamespace())._collect_tool_failures(3)

    assert state.tool_logs == []


def test_direct_mcp_execution_error_remains_visible_after_later_success() -> None:
    steps = [
        SimpleNamespace(
            project_id="project-1",
            iteration=3,
            tool="search_characters",
            status="error",
        ),
        SimpleNamespace(
            project_id="project-1",
            iteration=3,
            tool="search_characters",
            status="ok",
        ),
    ]
    state = SimpleNamespace(
        local_cli_mcp_enabled=True,
        project_id="project-1",
        assistant_run=SimpleNamespace(id="run-1"),
        workspace=SimpleNamespace(run_steps=lambda _run_id: steps),
        tool_logs=[],
    )

    WorkspaceDirectMcpTurn(state, SimpleNamespace())._collect_tool_failures(3)

    assert state.tool_logs == [{
        "tool": "search_characters",
        "status": "error",
        "detail": "本机 CLI 工具未完成，请检查工具记录、前置条件与当前项目状态。",
    }]


def test_direct_mcp_short_draft_keeps_actionable_public_retry_counts() -> None:
    raw_result = {
        "tool": "save_external_chapter_draft",
        "status": "needs_confirmation",
        "detail": "private diagnostic must not be copied",
        "data": {
            "reason_code": "draft_below_minimum",
            "actual_han_characters": 3_202,
            "minimum_han_characters": 3_400,
            "missing_han_characters": 999_999,
        },
    }
    step = SimpleNamespace(
        project_id="project-1",
        iteration=3,
        tool="save_external_chapter_draft",
        status="needs_confirmation",
        result_json=json.dumps(raw_result, ensure_ascii=False),
    )
    state = SimpleNamespace(
        local_cli_mcp_enabled=True,
        project_id="project-1",
        assistant_run=SimpleNamespace(id="run-1"),
        workspace=SimpleNamespace(run_steps=lambda _run_id: [step]),
        tool_logs=[],
    )

    WorkspaceDirectMcpTurn(state, SimpleNamespace())._collect_tool_failures(3)

    assert state.tool_logs == [{
        "tool": "save_external_chapter_draft",
        "status": "needs_confirmation",
        "detail": "正文有 3202 个汉字，低于最低要求 3400 个；至少还差 198 个。为减少反复退回，建议一次补至 3740 个汉字（约再补 538 个）后重试。",
        "remediation": {
            "code": "draft_below_minimum",
            "message": "正文有 3202 个汉字，低于最低要求 3400 个；至少还差 198 个。为减少反复退回，建议一次补至 3740 个汉字（约再补 538 个）后重试。",
            "retryable": True,
            "actual_han_characters": 3_202,
            "minimum_han_characters": 3_400,
            "missing_han_characters": 198,
            "recommended_han_characters": 3_740,
            "recommended_additional_han_characters": 538,
        },
    }]


def test_failed_step_closure_preserves_concurrent_cancelled_winner() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP closure winner")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    try:
        started = begin_workspace_direct_mcp_step(
            db,
            state_file=state_file,
            project_id=project.id,
            tool_name="create_character",
            arguments={"project_id": project.id, "name": "沈砚"},
            call_id=612,
            is_write=True,
            lease_token=lease_token,
        )
        cancelled = {
            "tool": "create_character",
            "status": "cancelled",
            "detail": "newer author turn won",
            "data": {"reason": "turn_superseded"},
        }
        finish_run_step(
            db,
            started.step,
            status="cancelled",
            result=cancelled,
            detail=cancelled["detail"],
            error=cancelled["detail"],
            allow_partial_commit_refs=False,
        )

        restored = _close_failed_scoped_workspace_step(
            db,
            started,
            tool_name="create_character",
        )

        db.refresh(started.step)
        assert restored == cancelled
        assert started.step.status == "cancelled"
        assert started.step.output_refs is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_cancelled_executor_closes_intent_and_reraises_cancellation() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP cancellation")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    executor = AsyncMock(side_effect=asyncio.CancelledError())
    try:
        with (
            patch("app.services.workspace.executor.execute_workspace_action", new=executor),
            pytest.raises(asyncio.CancelledError),
        ):
            handle_message(
                _create_character_call(project.id, call_id=62),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        executor.assert_awaited_once()
        step = db.query(AssistantRunStep).one()
        assert step.status == "cancelled"
        assert step.completed_at is not None
        assert step.output_refs is None
        assert db.query(Character).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_post_handler_cas_rejection_rolls_back_resource_and_refs() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP CAS rejection")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    try:
        with patch("app.mcp.server.cas_workspace_direct_mcp_lease", return_value=False):
            response = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=71),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        payload = json.loads(response["result"]["content"][0]["text"])
        assert response["result"]["isError"] is True
        assert payload["status"] == "denied"
        assert db.query(Character).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "denied"
        assert step.output_refs is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_stale_finalize_claim_rolls_back_business_write_and_closes_error() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP finalize claim")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    from app.mcp import server as mcp_server

    finish_scoped = mcp_server._finish_scoped_workspace_step

    def stale_finish(session, started, result, payload):
        assert started is not None
        session.query(AssistantRunStep).filter(
            AssistantRunStep.id == started.step.id
        ).update({AssistantRunStep.status: "cancelled"}, synchronize_session=False)
        session.flush()
        return finish_scoped(session, started, result, payload)

    try:
        with patch(
            "app.mcp.server._finish_scoped_workspace_step",
            new=stale_finish,
        ):
            response = json.loads(
                handle_message(
                    _create_character_call(project.id, call_id=72),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert response["result"]["isError"] is True
        assert db.query(Character).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "error"
        assert step.completed_at is not None
        assert step.output_refs is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_partial_outline_batch_error_rolls_back_every_node_and_ref() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP partial batch")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    from app.services.workspace.tools import outline as outline_tools

    create_one = outline_tools.create_outline_node
    calls = 0

    async def fail_second(session, project_id, arguments):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await create_one(session, project_id, arguments)
        return {
            "tool": "create_outline_node",
            "status": "error",
            "detail": "injected second-node failure",
            "data": None,
        }

    try:
        with patch(
            "app.services.workspace.tools.outline.create_outline_node",
            new=fail_second,
        ):
            response = json.loads(
                handle_message(
                    _create_outline_nodes_call(project.id, call_id=73),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert response["result"]["isError"] is True
        assert calls == 2
        assert db.query(OutlineNode).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "error"
        assert step.output_refs is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_concurrent_same_call_key_has_one_winner_and_one_handler(tmp_path) -> None:
    database_path = tmp_path / "direct-mcp-race.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    setup = Session()
    project, conversation, run = _workspace_run(setup, "Direct MCP concurrent claim")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(setup, run)
    project_id = str(project.id)
    setup.close()

    from app.services.workspace import direct_mcp_run_log as direct_log
    from app.services.workspace.executor import execute_workspace_action as execute_original

    claim_barrier = threading.Barrier(2)
    start_original = direct_log.start_run_step
    execution_lock = threading.Lock()
    execution_count = 0

    def synchronized_start(*args, **kwargs):
        claim_barrier.wait(timeout=10)
        return start_original(*args, **kwargs)

    async def counted_execute(*args, **kwargs):
        nonlocal execution_count
        with execution_lock:
            execution_count += 1
        return await execute_original(*args, **kwargs)

    def invoke() -> dict:
        session = Session()
        try:
            return json.loads(
                handle_message(
                    _create_character_call(project_id, call_id=81),
                    db=session,
                    project_id=project_id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        finally:
            session.close()

    try:
        with (
            patch(
                "app.services.workspace.direct_mcp_run_log.start_run_step",
                new=synchronized_start,
            ),
            patch(
                "app.services.workspace.executor.execute_workspace_action",
                new=counted_execute,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            responses = list(pool.map(lambda _index: invoke(), range(2)))

        verify = Session()
        try:
            assert execution_count == 1
            assert verify.query(AssistantRunStep).count() == 1
            assert verify.query(Character).count() == 1
            step = verify.query(AssistantRunStep).one()
            assert step.status == "ok"
            assert step.direct_mcp_call_key
            assert any(not item["result"]["isError"] for item in responses)
        finally:
            verify.close()
    finally:
        remove_tool_category_state(state_file)
        engine.dispose()


def test_draft_finalize_failure_has_no_phantom_cache_and_db_read_survives() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP draft UoW")
    outline = OutlineNode(
        project_id=project.id,
        title="第一章 雨门",
        node_type="chapter",
        status="pending",
        sort_order=1,
    )
    db.add(outline)
    db.commit()
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=["writing_context"],
    )
    lease_token = _lease(db, run)
    from app.mcp import server as mcp_server
    from app.services.workspace import generated_drafts

    finish_scoped = mcp_server._finish_scoped_workspace_step

    def stale_finish(session, started, result, payload):
        assert started is not None
        session.query(AssistantRunStep).filter(
            AssistantRunStep.id == started.step.id
        ).update({AssistantRunStep.status: "cancelled"}, synchronize_session=False)
        session.flush()
        return finish_scoped(session, started, result, payload)

    arguments = {
        "project_id": project.id,
        "content": "雨落石阶，沈砚推开山门。",
        "outline_node_id": outline.id,
        "context_manifest_id": "manifest-reviewed",
        "context_selection_token": "selection-reviewed",
    }
    isolated_cache: OrderedDict[str, dict] = OrderedDict()
    try:
        with (
            patch.object(generated_drafts, "_CHAPTER_DRAFTS", isolated_cache),
            patch(
                "app.services.workspace.tools.external_writing._external_draft_manifest_error",
                return_value=None,
            ),
        ):
            with patch(
                "app.mcp.server._finish_scoped_workspace_step",
                new=stale_finish,
            ):
                failed = json.loads(
                    handle_message(
                        _tool_call(
                            "save_external_chapter_draft",
                            arguments,
                            call_id=91,
                        ),
                        db=db,
                        project_id=project.id,
                        permission_pack="project_management",
                        tool_category_state_file=state_file,
                        direct_mcp_lease_token=lease_token,
                    )
                )
            assert failed["result"]["isError"] is True
            assert db.query(ChapterDraft).count() == 0
            assert isolated_cache == {}

            saved = json.loads(
                handle_message(
                    _tool_call(
                        "save_external_chapter_draft",
                        arguments,
                        call_id=92,
                    ),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
            assert saved["result"]["isError"] is False
            draft = db.query(ChapterDraft).one()
            assert isolated_cache == {}
            saved_step = db.query(AssistantRunStep).filter_by(
                tool="save_external_chapter_draft",
                status="ok",
            ).one()
            assert json.loads(saved_step.output_refs or "{}") == {
                "chapter_draft": {"id": draft.id}
            }

            restored = json.loads(
                handle_message(
                    _tool_call(
                        "get_external_chapter_draft",
                        {"project_id": project.id, "draft_id": draft.id},
                        call_id=93,
                    ),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
            restored_payload = json.loads(restored["result"]["content"][0]["text"])
            assert restored["result"]["isError"] is False
            assert restored_payload["data"]["content"] == arguments["content"]
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_terminal_draft_requires_exact_run_iteration_output_ref() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP terminal refs")
    foreign_run = AssistantRun(
        project_id=project.id,
        conversation_id=conversation.id,
        status="running",
        scope="project",
    )
    draft = ChapterDraft(
        project_id=project.id,
        title="并发草稿",
        status="pending",
        content="只可由精确步骤归因",
    )
    db.add_all([foreign_run, draft])
    db.flush()
    refs = json.dumps({"chapter_draft": {"id": draft.id}}, ensure_ascii=False)
    foreign_step = AssistantRunStep(
        run_id=foreign_run.id,
        project_id=project.id,
        step_type="write",
        tool="save_external_chapter_draft",
        status="ok",
        iteration=3,
        output_refs=refs,
    )
    wrong_iteration = AssistantRunStep(
        run_id=run.id,
        project_id=project.id,
        step_type="write",
        tool="save_external_chapter_draft",
        status="ok",
        iteration=2,
        output_refs=refs,
    )
    db.add_all([foreign_step, wrong_iteration])
    db.commit()
    try:
        assert local_cli_terminal_draft(db, project.id, run.id, 3) is None
        wrong_iteration.iteration = 3
        db.commit()
        detected = local_cli_terminal_draft(db, project.id, run.id, 3)
        assert detected is not None
        assert detected[0]["tool"] == "save_external_chapter_draft"
        assert detected[0]["data"]["draft_id"] == draft.id
    finally:
        db.close()


def test_direct_pack_is_explicit_and_blocks_prompts_and_unsafe_tools() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP safe pack")
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=list(TOOL_CATEGORY_METADATA),
    )
    lease_token = _lease(db, run)
    expected = {
        "search_characters",
        "search_chapters",
        "search_outline",
        "search_worldbuilding",
        "create_worldbuilding_entry",
        "update_worldbuilding_entry",
        "create_outline_node",
        "create_outline_nodes",
        "update_outline_node",
        "create_character",
        "update_character",
        "recall",
        "prepare_task_context",
        "search_task_context",
        "submit_context_evidence",
        "prepare_external_writing_context",
        "save_external_chapter_draft",
        "save_external_outline_draft",
        "get_external_chapter_draft",
    }
    unsafe = {
        "list_projects",
        "create_project",
        "import_file_as_project",
        "get_creation_session",
        "import_creation_material",
        "write_project_file",
        "export_project",
        "run_scheduled_task_now",
        "search_outline_tree",
    }
    executor = AsyncMock()
    try:
        direct_defs = registry.list_for_workspace_direct_mcp()
        assert {definition.name for definition in direct_defs} == expected
        assert all(
            definition.direct_mcp_project_scoped
            and definition.direct_mcp_transactional
            for definition in direct_defs
        )

        listed = json.loads(
            handle_message(
                _tool_call("unused", {}, call_id=94).replace(
                    '"method": "tools/call"', '"method": "tools/list"'
                ),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        listed_names = {item["name"] for item in listed["result"]["tools"]}
        assert listed_names == expected | {"set_tool_categories"}

        with patch("app.services.workspace.executor.execute_workspace_action", new=executor):
            for call_id, tool_name in enumerate(sorted(unsafe), start=100):
                denied = json.loads(
                    handle_message(
                        _tool_call(
                            tool_name,
                            {"project_id": project.id, "path": "/tmp/escape"},
                            call_id=call_id,
                        ),
                        db=db,
                        project_id=project.id,
                        permission_pack="project_management",
                        tool_category_state_file=state_file,
                        direct_mcp_lease_token=lease_token,
                    )
                )
                assert denied["result"]["isError"] is True
        executor.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 0

        initialize = json.loads(
            handle_message(
                json.dumps({"jsonrpc": "2.0", "id": 120, "method": "initialize"}),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert "prompts" not in initialize["result"]["capabilities"]
        prompts = json.loads(
            handle_message(
                json.dumps({"jsonrpc": "2.0", "id": 121, "method": "prompts/list"}),
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert prompts["result"]["prompts"] == []
        render = AsyncMock()
        with patch("app.mcp.server.render_prompt", new=render):
            denied_prompt = json.loads(
                handle_message(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 122,
                            "method": "prompts/get",
                            "params": {
                                "name": "writing_context",
                                "arguments": {"project_id": "foreign-project"},
                            },
                        }
                    ),
                    db=db,
                    project_id=project.id,
                    permission_pack="project_management",
                    tool_category_state_file=state_file,
                    direct_mcp_lease_token=lease_token,
                )
            )
        assert denied_prompt["error"]["code"] != 0
        render.assert_not_awaited()
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_managed_cataloging_env_cannot_override_explicit_direct_workspace_pack() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP env precedence")
    state_file = _scoped_state_file(
        project,
        conversation,
        run,
        categories=list(TOOL_CATEGORY_METADATA),
    )
    lease_token = _lease(db, run)
    expected = {
        definition.name for definition in registry.list_for_workspace_direct_mcp()
    }
    stdin = io.StringIO(
        "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 140, "method": "tools/list"}),
                _tool_call(
                    "save_external_cataloging_facts",
                    {"project_id": project.id},
                    call_id=141,
                ),
            ]
        )
        + "\n"
    )
    stdout = io.StringIO()
    executor = AsyncMock()
    try:
        with (
            patch("app.mcp.server.sys.stdin", stdin),
            patch("app.mcp.server.sys.stdout", stdout),
            patch("app.mcp.server.get_compatible_env", return_value="cataloging"),
            patch(
                "app.services.workspace.executor.execute_workspace_action",
                new=executor,
            ),
        ):
            serve_stdio(
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )

        listed, denied = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert {item["name"] for item in listed["result"]["tools"]} == expected | {
            "set_tool_categories"
        }
        assert denied["result"]["isError"] is True
        executor.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_managed_writing_context_chain_produces_only_one_unsaved_draft() -> None:
    from app.database.models import CatalogingJob, Chapter, ContextManifest, PublicPromptPack

    db = _db()
    project, conversation, run = _workspace_run(db, "Scoped writing workflow")
    project.writing_style = "restrained"
    project.narrative_perspective = "third_person"
    outline = OutlineNode(project_id=project.id, title="潮声", node_type="chapter", summary="调查档案")
    db.add(outline)
    db.commit()
    state_file = _scoped_state_file(project, conversation, run, categories=["writing_context"])
    lease_token = _lease(db, run)

    def call(name: str, arguments: dict, call_id: int) -> dict:
        response = json.loads(handle_message(
            _tool_call(name, {"project_id": project.id, **arguments}, call_id=call_id),
            db=db, project_id=project.id, permission_pack="project_management",
            tool_category_state_file=state_file, direct_mcp_lease_token=lease_token,
        ))
        assert response["result"]["isError"] is False, response
        return json.loads(response["result"]["content"][0]["text"])

    try:
        baseline = call("prepare_task_context", {
            "task_type": "writing", "outline_node_id": outline.id,
        }, 160)
        manifest_id = baseline["data"]["context_manifest_id"]
        db.expire_all()
        assert db.get(ContextManifest, manifest_id).project_id == project.id
        prepared = call("prepare_external_writing_context", {
            "outline_node_id": outline.id, "context_manifest_id": manifest_id,
        }, 161)
        assert prepared["data"]["context_manifest_id"] == manifest_id
        assert prepared["data"]["selection_required"] is True
        searched = call("search_task_context", {
            "context_manifest_id": manifest_id, "query": "潮声", "source_types": ["outline"],
        }, 162)
        assert searched["status"] == "ok"
        selected = call("submit_context_evidence", {
            "context_manifest_id": manifest_id, "sources": [],
        }, 163)
        token = selected["data"]["context_selection_token"]
        assert token
        saved = call("save_external_chapter_draft", {
            "outline_node_id": outline.id, "context_manifest_id": manifest_id,
            "context_selection_token": token, "content": "窗外有潮声。林澄将卷宗移到台灯下。",
        }, 164)
        draft = db.get(ChapterDraft, saved["data"]["draft_id"])
        assert draft.project_id == project.id
        assert draft.status == "pending"
        detected = local_cli_terminal_draft(db, project.id, run.id, 2)
        assert detected is not None
        assert detected[0]["data"]["draft_id"] == draft.id
        assert db.query(ChapterDraft).count() == 1
        assert db.query(Chapter).count() == 0
        assert db.query(CatalogingJob).count() == 0
        # Project-scoped preparation must not lazily create global prompt packs.
        assert db.query(PublicPromptPack).count() == 0
        assert db.query(AssistantRunStep).count() == 5
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_managed_writing_context_withholds_token_until_lossless_pages_are_read() -> None:
    from app.database.models import Chapter, ContextManifest
    from app.services.task_context_selection import render_generation_context, selection_state
    db = _db()
    project, conversation, run = _workspace_run(db, "Paged writing context gate")
    outline = OutlineNode(
        project_id=project.id,
        title="长上下文",
        node_type="chapter",
        summary="核验每一页证据后才能写作",
    )
    witness = Character(
        project_id=project.id,
        name="分页证人",
        background="每一段都是必须完整送达的精确角色档案。" * 1000,
    )
    db.add_all([outline, witness])
    db.commit()
    state_file = _scoped_state_file(project, conversation, run, categories=["writing_context"])
    lease_token = _lease(db, run)
    call_id = 200

    def call(name: str, arguments: dict, *, expect_error: bool = False) -> dict:
        nonlocal call_id
        call_id += 1
        response = json.loads(handle_message(
            _tool_call(name, {"project_id": project.id, **arguments}, call_id=call_id),
            db=db,
            project_id=project.id,
            permission_pack="project_management",
            tool_category_state_file=state_file,
            direct_mcp_lease_token=lease_token,
        ))
        assert response["result"]["isError"] is expect_error, response
        return json.loads(response["result"]["content"][0]["text"])

    try:
        baseline = call("prepare_task_context", {
            "task_type": "writing",
            "outline_node_id": outline.id,
            "requirements": "逐页核验上下文，不得提前生成。",
        })
        manifest_id = baseline["data"]["context_manifest_id"]
        searched = call("search_task_context", {
            "context_manifest_id": manifest_id,
            "query": "分页证人 精确角色档案",
            "source_types": ["character"],
        })
        witness_item = next(
            item for item in searched["data"]["items"]
            if item["source_id"] == witness.id
        )
        selected = call("submit_context_evidence", {
            "context_manifest_id": manifest_id,
            "sources": [{"item_id": witness_item["item_id"]}],
        })
        assert selected["status"] == "ok"
        assert selected["data"]["selection_ready"] is True
        assert selected["data"]["context_page"]["has_more"] is True
        assert selected["data"]["context_delivery_ready"] is False
        assert not selected["data"].get("context_selection_token")

        manifest = db.get(ContextManifest, manifest_id)
        actual_token = selection_state(manifest)["token"]
        blocked = call("save_external_chapter_draft", {
            "outline_node_id": outline.id,
            "context_manifest_id": manifest_id,
            "context_selection_token": actual_token,
            "content": "这段正文绝不能在上下文读完前形成草稿。",
        }, expect_error=True)
        assert blocked["status"] == "needs_confirmation"
        assert "not been read completely" in blocked["detail"]
        assert db.query(ChapterDraft).count() == 0
        assert db.query(Chapter).count() == 0

        next_arguments = dict(selected["data"]["next_arguments"])
        wrong_arguments = {**next_arguments, "content_cursor": next_arguments["content_cursor"] + 1}
        rejected = call("prepare_task_context", wrong_arguments, expect_error=True)
        assert rejected["status"] == "skipped"
        assert "out of order" in rejected["detail"]

        parts = [selected["data"]["context_page"]["text"]]
        final_token = None
        while next_arguments:
            page_result = call("prepare_task_context", next_arguments)
            assert page_result["status"] == "ready"
            parts.append(page_result["data"]["context_page"]["text"])
            if page_result["data"]["context_page"]["has_more"]:
                assert page_result["data"]["context_delivery_ready"] is False
                assert not page_result["data"].get("context_selection_token")
                next_arguments = page_result["data"]["next_arguments"]
            else:
                assert page_result["data"]["context_delivery_ready"] is True
                final_token = page_result["data"]["context_selection_token"]
                next_arguments = None

        assert "".join(parts) == render_generation_context(manifest)
        assert final_token == actual_token
        saved = call("save_external_chapter_draft", {
            "outline_node_id": outline.id,
            "context_manifest_id": manifest_id,
            "context_selection_token": final_token,
            "content": "读完全部证据后，窗外的潮声终于有了准确的刻度。",
        })
        assert saved["status"] == "ok"
        assert db.query(ChapterDraft).count() == 1
        assert db.query(Chapter).count() == 0
    finally:
        remove_tool_category_state(state_file)
        db.close()


@pytest.mark.parametrize("tool_name,argument_kind", [
    ("prepare_external_writing_context", "outline_node_id"),
    ("prepare_task_context", "context_manifest_id"),
    ("search_task_context", "context_manifest_id"),
    ("submit_context_evidence", "context_manifest_id"),
])
def test_managed_context_tools_reject_foreign_project_data(tool_name, argument_kind) -> None:
    from app.database.models import ContextManifest
    from app.services.context_orchestrator import ContextOrchestrator

    db = _db()
    project, conversation, run = _workspace_run(db, "Scoped context owner")
    foreign = Project(title="Foreign private project")
    db.add(foreign)
    db.flush()
    foreign_outline = OutlineNode(project_id=foreign.id, title="private-foreign-content", node_type="chapter")
    db.add(foreign_outline)
    db.flush()
    manifest = ContextOrchestrator(db).prepare(
        project_id=foreign.id, task_type="writing", arguments={"outline_node_id": foreign_outline.id},
    )
    db.commit()
    state_file = _scoped_state_file(project, conversation, run, categories=["writing_context"])
    lease_token = _lease(db, run)
    args = {"project_id": project.id, "task_type": "writing", "query": "private", "sources": []}
    args[argument_kind] = foreign_outline.id if argument_kind == "outline_node_id" else manifest.id
    try:
        response = json.loads(handle_message(
            _tool_call(tool_name, args, call_id=165), db=db, project_id=project.id,
            permission_pack="project_management", tool_category_state_file=state_file,
            direct_mcp_lease_token=lease_token,
        ))
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["status"] in {"skipped", "needs_confirmation"}
        assert "private-foreign-content" not in json.dumps(payload)
        assert db.query(ContextManifest).filter(ContextManifest.project_id == project.id).count() == 0
        assert db.get(ContextManifest, manifest.id).consumed_at is None
    finally:
        remove_tool_category_state(state_file)
        db.close()


@pytest.mark.parametrize("tool_name", ["prepare_task_context", "prepare_external_writing_context"])
@pytest.mark.parametrize("requested_model", [None, "invented:unlimited"])
def test_managed_context_budget_uses_the_pinned_executing_model(tool_name, requested_model) -> None:
    from app.database.models import APIConfig, ContextManifest

    db = _db()
    project, conversation, run = _workspace_run(db, "Pinned context budget")
    run.model = "opencode_cli:budget-test"
    outline = OutlineNode(project_id=project.id, title="真实目标", node_type="chapter", summary="核验档案")
    db.add_all([outline, APIConfig(
        provider="opencode_cli", default_model="budget-test", api_key_encrypted="test-only",
        available_models_json=[{
            "id": "budget-test", "context_window_tokens": 96_000,
            "max_output_tokens": 8_000, "safety_margin_tokens": 1_024,
            "capacity_source": "opencode_cli_metadata",
        }],
    )])
    db.commit()
    state_file = _scoped_state_file(project, conversation, run, categories=["writing_context"])
    lease_token = _lease(db, run)
    arguments = {"project_id": project.id, "outline_node_id": outline.id}
    if tool_name == "prepare_task_context":
        arguments["task_type"] = "writing"
    if requested_model:
        arguments["model"] = requested_model
    try:
        response = json.loads(handle_message(
            _tool_call(tool_name, arguments, call_id=190), db=db, project_id=project.id,
            permission_pack="project_management", tool_category_state_file=state_file,
            direct_mcp_lease_token=lease_token,
        ))
        assert response["result"]["isError"] is False, response
        result = json.loads(response["result"]["content"][0]["text"])
        manifest = db.get(ContextManifest, result["data"]["context_manifest_id"])
        assert manifest.provider == "opencode_cli"
        assert manifest.model == "budget-test"
        assert manifest.context_window_tokens == 96_000
        assert manifest.input_budget_tokens < 96_000
        step = db.query(AssistantRunStep).filter_by(run_id=run.id).one()
        assert json.loads(step.request_json)["model"] == run.model
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_managed_context_preparation_rolls_back_when_lease_is_revoked() -> None:
    from app.database.models import ContextManifest, ContextManifestItem, PublicPromptPack

    db = _db()
    project, conversation, run = _workspace_run(db, "Context lease rollback")
    outline = OutlineNode(project_id=project.id, title="潮声", node_type="chapter")
    db.add(outline)
    db.commit()
    state_file = _scoped_state_file(project, conversation, run, categories=["writing_context"])
    lease_token = _lease(db, run)
    try:
        with patch("app.mcp.server.cas_workspace_direct_mcp_lease", return_value=False):
            response = json.loads(handle_message(
                _tool_call("prepare_external_writing_context", {
                    "project_id": project.id, "outline_node_id": outline.id,
                }, call_id=166), db=db, project_id=project.id, permission_pack="project_management",
                tool_category_state_file=state_file, direct_mcp_lease_token=lease_token,
            ))
        assert response["result"]["isError"] is True
        assert db.query(ContextManifest).count() == 0
        assert db.query(ContextManifestItem).count() == 0
        assert db.query(PublicPromptPack).count() == 0
        step = db.query(AssistantRunStep).one()
        assert step.status == "denied"
    finally:
        remove_tool_category_state(state_file)
        db.close()


def test_stdio_auto_permission_resolution_log_redacts_exception(caplog) -> None:
    private_failure = "database secret /private/project/transcript.db"
    stdin = io.StringIO("")
    stdout = io.StringIO()
    caplog.set_level(logging.WARNING, logger="app.mcp.server")

    with (
        patch("app.mcp.server.sys.stdin", stdin),
        patch("app.mcp.server.sys.stdout", stdout),
        patch("app.mcp.server.get_compatible_env", return_value=""),
        patch(
            "app.services.external_agent.permissions.resolve_effective_pack",
            side_effect=RuntimeError(private_failure),
        ),
    ):
        serve_stdio(db=object(), project_id="project-1", permission_pack="auto")

    assert private_failure not in caplog.text
    assert "/private/project" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_mcp_argument_logs_are_structurally_redacted(caplog) -> None:
    caplog.set_level(logging.INFO, logger="app.mcp.adapter")
    secret = "secret-access-token-value"
    content = "完整章节正文不可进入日志"
    path = "/private/user/project/manuscript.txt"

    _log_mcp_tool_call(
        None,
        "project-1",
        "save_external_chapter_draft",
        {
            "accessToken": secret,
            "api_key": "api-key-value",
            "content": content,
            "path": path,
            "title": "也不记录任意字符串值",
            "limit": 10,
        },
        status="ok",
        detail="done",
    )

    rendered = caplog.text
    assert secret not in rendered
    assert "api-key-value" not in rendered
    assert content not in rendered
    assert path not in rendered
    assert "也不记录任意字符串值" not in rendered
    assert "[redacted]" in rendered
    assert "limit: 10" in rendered


def test_post_commit_state_audit_failure_replays_from_durable_winner() -> None:
    db = _db()
    project, conversation, run = _workspace_run(db, "Direct MCP audit replay")
    state_file = _scoped_state_file(project, conversation, run)
    lease_token = _lease(db, run)
    call = _create_character_call(project.id, call_id=130)
    try:
        with (
            patch(
                "app.mcp.server._record_scoped_tool_result",
                side_effect=OSError("state file unavailable after commit"),
            ),
            pytest.raises(OSError, match="state file unavailable"),
        ):
            handle_message(
                call,
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        assert db.query(Character).count() == 1
        step = db.query(AssistantRunStep).one()
        assert step.status == "ok"
        assert step.output_refs is not None

        replay = json.loads(
            handle_message(
                call,
                db=db,
                project_id=project.id,
                permission_pack="project_management",
                tool_category_state_file=state_file,
                direct_mcp_lease_token=lease_token,
            )
        )
        assert replay["result"]["isError"] is False
        assert db.query(Character).count() == 1
        assert db.query(AssistantRunStep).count() == 1
    finally:
        remove_tool_category_state(state_file)
        db.close()
