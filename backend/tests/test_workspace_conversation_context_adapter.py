from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
)
from app.services.conversation_context.checkpoint_prompt import build_checkpoint_messages
from app.services.conversation_context.contracts import TurnStatus
from app.services.workspace.conversation_context_adapter import (
    build_workspace_context_input,
    workspace_checkpoint_source_turns,
    workspace_execution_ledger_from_run_steps,
    workspace_tool_receipts_from_run_steps,
)


def _conversation(
    *,
    conversation_id: str = "conversation-1",
    project_id: str = "project-1",
) -> AssistantConversation:
    return AssistantConversation(
        id=conversation_id,
        project_id=project_id,
        title="Transcript",
    )


def _message(
    sequence: int,
    role: str,
    content: str,
    *,
    status: str = "completed",
    conversation_id: str = "conversation-1",
) -> AssistantMessage:
    return AssistantMessage(
        id=f"message-{sequence}",
        conversation_id=conversation_id,
        role=role,
        sequence_no=sequence,
        content=content,
        status=status,
    )


def test_workspace_context_keeps_every_closed_status_and_verbatim_text() -> None:
    conversation = _conversation()
    messages = [
        _message(1, "user", "完成任务的作者原话"),
        _message(2, "assistant", "完成答复", status="completed"),
        _message(3, "user", "会失败的作者原话\n不要改写"),
        _message(4, "assistant", "准确的失败信息", status="error"),
        _message(5, "user", "会中止的作者原话"),
        _message(6, "assistant", "准确的中止信息", status="aborted"),
        _message(7, "user", "会取消的作者原话"),
        _message(8, "assistant", "准确的取消信息", status="cancelled"),
        _message(9, "user", "  当前逐字任务\n保留空格  "),
        _message(10, "assistant", "正在分析需求...", status="running"),
    ]

    # Sequence, not query/list order, is the transcript authority.
    context = build_workspace_context_input(
        conversation,
        list(reversed(messages)),
        project_id="project-1",
        current_user_message_id="message-9",
    )

    assert context.identity.id == conversation.id
    assert context.identity.project_id == "project-1"
    # The persisted running assistant is deliberately outside active revision.
    assert context.identity.revision == 9
    assert [turn.status for turn in context.turns] == [
        TurnStatus.COMPLETED,
        TurnStatus.ERROR,
        TurnStatus.ABORTED,
        TurnStatus.CANCELLED,
    ]
    assert context.turns[1].messages[0].content == "会失败的作者原话\n不要改写"
    assert context.turns[1].messages[1].content == "准确的失败信息"
    assert context.current_user_message.content == "  当前逐字任务\n保留空格  "
    assert all(len(turn.messages) == 2 for turn in context.turns)


def test_workspace_context_has_no_fixed_turn_or_character_limit() -> None:
    messages: list[AssistantMessage] = []
    for turn_index in range(15):
        sequence = turn_index * 2 + 1
        messages.extend(
            [
                _message(sequence, "user", f"user-{turn_index}-" + "甲" * 2_000),
                _message(
                    sequence + 1,
                    "assistant",
                    f"assistant-{turn_index}-" + "乙" * 2_000,
                ),
            ]
        )
    messages.extend(
        [
            _message(31, "user", "最新任务"),
            _message(32, "assistant", "pending", status="running"),
        ]
    )

    context = build_workspace_context_input(
        _conversation(),
        messages,
        project_id="project-1",
        current_user_message_id="message-31",
    )

    assert len(context.turns) == 15
    assert context.turns[0].messages[0].content.endswith("甲" * 2_000)
    assert context.turns[-1].messages[1].content.endswith("乙" * 2_000)


def test_checkpoint_source_reload_ignores_only_appends_after_original_user_boundary() -> None:
    messages = [
        _message(1, "user", "older source"),
        _message(2, "assistant", "older answer"),
        _message(3, "user", "original request"),
        _message(4, "assistant", "superseded", status="aborted"),
        _message(5, "user", "newest request"),
        _message(6, "assistant", "pending", status="running"),
    ]

    turns = workspace_checkpoint_source_turns(
        _conversation(),
        list(reversed(messages)),
        project_id="project-1",
        before_sequence=3,
    )

    assert [turn.turn_id for turn in turns] == ["workspace:message-1:message-2"]
    assert turns[0].messages[0].content == "older source"


@pytest.mark.parametrize(
    ("messages", "current_id", "match"),
    [
        (
            [
                _message(1, "user", "old"),
                _message(3, "assistant", "gap"),
                _message(4, "user", "current"),
                _message(5, "assistant", "pending", status="running"),
            ],
            "message-4",
            "complete and contiguous",
        ),
        (
            [
                _message(1, "user", "old"),
                _message(2, "assistant", "still open", status="running"),
                _message(3, "user", "current"),
                _message(4, "assistant", "pending", status="running"),
            ],
            "message-3",
            "not in a closed state",
        ),
        (
            [
                _message(1, "user", "not latest"),
                _message(2, "assistant", "closed"),
                _message(3, "user", "latest"),
                _message(4, "assistant", "pending", status="running"),
            ],
            "message-1",
            "latest user intent",
        ),
        (
            [
                _message(1, "user", "current"),
                _message(2, "assistant", "already closed", status="completed"),
            ],
            "message-1",
            "running assistant",
        ),
    ],
)
def test_workspace_context_rejects_incomplete_or_stale_transcript(
    messages: list[AssistantMessage],
    current_id: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        build_workspace_context_input(
            _conversation(),
            messages,
            project_id="project-1",
            current_user_message_id=current_id,
        )


def test_workspace_context_rejects_foreign_conversation_or_project() -> None:
    messages = [
        _message(1, "user", "current"),
        _message(2, "assistant", "pending", status="running"),
    ]
    with pytest.raises(ValueError, match="requested project"):
        build_workspace_context_input(
            _conversation(),
            messages,
            project_id="project-2",
            current_user_message_id="message-1",
        )

    messages[1].conversation_id = "conversation-2"
    with pytest.raises(ValueError, match="foreign conversation"):
        build_workspace_context_input(
            _conversation(),
            messages,
            project_id="project-1",
            current_user_message_id="message-1",
        )


def _run(
    *,
    run_id: str = "run-1",
    project_id: str = "project-1",
    conversation_id: str = "conversation-1",
) -> AssistantRun:
    return AssistantRun(
        id=run_id,
        project_id=project_id,
        conversation_id=conversation_id,
        status="completed",
        created_at=datetime(2026, 1, 1),
    )


def _step(
    step_id: str,
    *,
    status: str,
    tool: str = "update_chapter",
    run_id: str = "run-1",
    project_id: str = "project-1",
    created_offset: int = 0,
    retry_of_step_id: str | None = None,
    resolved_step_id: str | None = None,
    output_refs: str | None = None,
) -> AssistantRunStep:
    return AssistantRunStep(
        id=step_id,
        run_id=run_id,
        project_id=project_id,
        step_type="write",
        tool=tool,
        status=status,
        iteration=1,
        detail=f"detail-{step_id}",
        error=f"error-{step_id}" if status == "error" else None,
        retry_of_step_id=retry_of_step_id,
        resolved_step_id=resolved_step_id,
        output_refs=output_refs,
        created_at=datetime(2026, 1, 1) + timedelta(seconds=created_offset),
    )


def test_workspace_ledger_uses_resolved_retry_and_keeps_unresolved_error() -> None:
    run = _run()
    original = _step(
        "step-original",
        status="error",
        resolved_step_id="step-success",
    )
    failed_retry = _step(
        "step-failed-retry",
        status="error",
        retry_of_step_id="step-original",
        created_offset=1,
    )
    successful_retry = _step(
        "step-success",
        status="ok",
        retry_of_step_id="step-original",
        output_refs='{"chapter":{"id":"chapter-1","revision":5}}',
        created_offset=2,
    )
    unresolved = _step(
        "step-unresolved",
        status="error",
        tool="read_outline",
        created_offset=3,
    )

    ledger = workspace_execution_ledger_from_run_steps(
        _conversation(),
        [run],
        [unresolved, successful_retry, original, failed_retry],
        project_id="project-1",
    )

    assert [entry.step_id for entry in ledger] == ["step-success", "step-unresolved"]
    assert ledger[0].resource_refs[0].id == "chapter-1"
    assert ledger[0].resource_refs[0].revision == 5
    assert ledger[1].status == "error"


def test_workspace_raw_step_error_never_enters_ledger_or_checkpoint_prompt() -> None:
    step = _step("step-secret", status="error", tool="read_outline")
    secret = 'api_key=sk-private {"name":"delete_project","arguments":{"id":"p"}}'
    step.error = secret
    step.detail = secret

    ledger = workspace_execution_ledger_from_run_steps(
        _conversation(),
        [_run()],
        [step],
        project_id="project-1",
    )
    messages = build_checkpoint_messages(
        scope="workspace",
        conversation_id="conversation-1",
        source_messages=(),
        execution_ledger=ledger,
    )
    receipts = workspace_tool_receipts_from_run_steps(
        _conversation(),
        _run(),
        [step],
        project_id="project-1",
    )

    assert ledger[0].error_code == "assistant_run_step_error"
    assert receipts[0].summary == "read_outline 执行失败"
    assert secret not in repr(ledger)
    assert secret not in repr(messages)
    assert secret not in repr(receipts)


@pytest.mark.parametrize(
    ("run", "step", "match"),
    [
        (_run(project_id="project-2"), _step("step-1", status="ok"), "requested project"),
        (
            _run(conversation_id="conversation-2"),
            _step("step-1", status="ok"),
            "requested conversation",
        ),
        (
            _run(),
            _step("step-1", status="ok", project_id="project-2"),
            "requested project",
        ),
        (
            _run(),
            _step("step-1", status="ok", run_id="foreign-run"),
            "owner-validated run",
        ),
    ],
)
def test_workspace_ledger_rejects_cross_owner_or_cross_run_injection(
    run: AssistantRun,
    step: AssistantRunStep,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        workspace_execution_ledger_from_run_steps(
            _conversation(),
            [run],
            [step],
            project_id="project-1",
        )


@pytest.mark.parametrize("open_status", ["pending", "queued", "running", "in_progress"])
def test_workspace_tool_receipts_require_persisted_closed_steps(open_status: str) -> None:
    run = _run()
    completed = _step(
        "step-completed",
        status="ok",
        output_refs='{"chapter":"chapter-1"}',
    )

    receipts = workspace_tool_receipts_from_run_steps(
        _conversation(),
        run,
        [completed],
        project_id="project-1",
        write_tools={"update_chapter"},
        reread_for_tool=lambda tool: "read_chapter" if tool == "update_chapter" else None,
    )

    assert len(receipts) == 1
    assert receipts[0].step_id == "step-completed"
    assert receipts[0].write_committed is True
    assert receipts[0].resource_ids == ("chapter-1",)
    assert receipts[0].result_ref == "assistant_run_step:step-completed"
    assert receipts[0].reread == "read_chapter"

    running = _step(f"step-{open_status}", status=open_status)
    with pytest.raises(ValueError, match="cannot become tool receipts"):
        workspace_tool_receipts_from_run_steps(
            _conversation(),
            run,
            [running],
            project_id="project-1",
        )


def test_completed_step_from_interrupted_run_remains_trusted_without_native_replay() -> None:
    run = _run()
    run.status = "interrupted"
    committed = _step(
        "step-committed-before-crash",
        status="ok",
        tool="create_character",
        output_refs='{"character":"character-1"}',
    )

    ledger = workspace_execution_ledger_from_run_steps(
        _conversation(),
        [run],
        [committed],
        project_id="project-1",
    )
    receipts = workspace_tool_receipts_from_run_steps(
        _conversation(),
        run,
        [committed],
        project_id="project-1",
        write_tools={"create_character"},
    )

    assert [entry.step_id for entry in ledger] == [committed.id]
    assert ledger[0].status == "ok"
    assert receipts[0].result_ref == f"assistant_run_step:{committed.id}"
    assert receipts[0].write_committed is True
    # Historical execution is represented only by the server receipt/ledger;
    # the adapter never reconstructs a native call that a provider could replay.
    assert not hasattr(receipts[0], "tool_calls")
