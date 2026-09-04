"""Workspace persistence adapters for the shared conversation-context runtime.

The workspace transcript tables and ``AssistantRunStep`` audit log are the
authorities.  This module performs only structural/ownership validation and
projects those durable records into the provider-neutral contracts in
``app.services.conversation_context``.  It never selects a fixed-size history
tail, truncates visible text, or reconstructs historical tool protocol.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantRun,
    AssistantRunStep,
)
from app.services.conversation_context.contracts import (
    ConversationIdentity,
    ConversationKind,
    ConversationMessage,
    ConversationRole,
    ConversationTurn,
    ExecutionLedgerEntry,
    TurnStatus,
)
from app.services.conversation_context.execution_ledger import (
    execution_ledger_from_run_steps,
    fold_execution_ledger,
    tool_receipts_from_run_steps,
)
from app.services.conversation_context.tool_transactions import ToolExecutionReceipt
from app.services.conversation_context.transcript import validate_transcript_snapshot

_CLOSED_ASSISTANT_STATUSES = {
    "completed": TurnStatus.COMPLETED,
    "error": TurnStatus.ERROR,
    "aborted": TurnStatus.ABORTED,
    "cancelled": TurnStatus.CANCELLED,
}
_SUCCESS_STEP_STATUSES = {"ok", "completed", "success", "succeeded"}
_OPEN_STEP_STATUSES = {"pending", "queued", "running", "in_progress"}


@dataclass(frozen=True)
class WorkspaceConversationContextInput:
    """One owner-validated, immutable input for a workspace model request."""

    identity: ConversationIdentity
    turns: tuple[ConversationTurn, ...]
    current_user_message: ConversationMessage


def _required_id(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must not be empty")
    return result


def _positive_sequence(value: Any, *, message_id: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"message {message_id} has an invalid sequence_no")
    try:
        sequence = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"message {message_id} has an invalid sequence_no") from exc
    if sequence <= 0:
        raise ValueError(f"message {message_id} has an invalid sequence_no")
    return sequence


def _message_content(message: AssistantMessage) -> str:
    content = message.content
    if not isinstance(content, str) or not content:
        raise ValueError(f"message {message.id} must preserve non-empty verbatim content")
    return content


def _message_status(message: AssistantMessage) -> str:
    return str(message.status or "").strip().lower()


def _validate_conversation_owner(
    conversation: AssistantConversation,
    *,
    project_id: str,
) -> tuple[str, str]:
    expected_project_id = _required_id(project_id, "project_id")
    conversation_id = _required_id(conversation.id, "conversation.id")
    actual_project_id = _required_id(conversation.project_id, "conversation.project_id")
    if actual_project_id != expected_project_id:
        raise ValueError("workspace conversation does not belong to the requested project")
    return conversation_id, expected_project_id


def _ordered_workspace_messages(
    conversation: AssistantConversation,
    messages: Sequence[AssistantMessage],
    *,
    project_id: str,
) -> tuple[str, str, list[tuple[int, AssistantMessage]]]:
    conversation_id, owner_id = _validate_conversation_owner(
        conversation,
        project_id=project_id,
    )
    ordered: list[tuple[int, AssistantMessage]] = []
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    for message in messages:
        message_id = _required_id(message.id, "message.id")
        if message_id in seen_ids:
            raise ValueError("workspace transcript contains duplicate message IDs")
        if str(message.conversation_id or "") != conversation_id:
            raise ValueError("workspace transcript contains a foreign conversation message")
        sequence = _positive_sequence(message.sequence_no, message_id=message_id)
        if sequence in seen_sequences:
            raise ValueError("workspace transcript contains duplicate sequence_no values")
        role = str(message.role or "").strip().lower()
        if role not in {ConversationRole.USER.value, ConversationRole.ASSISTANT.value}:
            raise ValueError(f"workspace message {message_id} has unsupported role {role!r}")
        seen_ids.add(message_id)
        seen_sequences.add(sequence)
        ordered.append((sequence, message))

    if not ordered:
        raise ValueError("workspace transcript must not be empty")
    ordered.sort(key=lambda item: item[0])
    sequences = [sequence for sequence, _ in ordered]
    if sequences != list(range(1, len(sequences) + 1)):
        raise ValueError("workspace transcript sequence_no values must be complete and contiguous")
    return conversation_id, owner_id, ordered


def _closed_workspace_turns(
    messages: Sequence[tuple[int, AssistantMessage]],
) -> tuple[ConversationTurn, ...]:
    if len(messages) % 2:
        raise ValueError("closed workspace transcript contains an incomplete turn")
    turns: list[ConversationTurn] = []
    for index in range(0, len(messages), 2):
        user_sequence, user = messages[index]
        assistant_sequence, assistant = messages[index + 1]
        user_id = _required_id(user.id, "message.id")
        assistant_id = _required_id(assistant.id, "message.id")
        if (
            str(user.role or "").strip().lower() != ConversationRole.USER.value
            or str(assistant.role or "").strip().lower() != ConversationRole.ASSISTANT.value
            or assistant_sequence != user_sequence + 1
        ):
            raise ValueError("closed workspace turns must be consecutive user/assistant pairs")
        if _message_status(user) != "completed":
            raise ValueError("historical workspace user messages must be completed")
        assistant_status = _message_status(assistant)
        turn_status = _CLOSED_ASSISTANT_STATUSES.get(assistant_status)
        if turn_status is None:
            raise ValueError(
                f"historical assistant message {assistant_id} is not in a closed state"
            )
        turns.append(
            ConversationTurn(
                turn_id=f"workspace:{user_id}:{assistant_id}",
                status=turn_status,
                messages=(
                    ConversationMessage(
                        message_id=user_id,
                        sequence_no=user_sequence,
                        role=ConversationRole.USER,
                        content=_message_content(user),
                    ),
                    ConversationMessage(
                        message_id=assistant_id,
                        sequence_no=assistant_sequence,
                        role=ConversationRole.ASSISTANT,
                        content=_message_content(assistant),
                    ),
                ),
            )
        )
    return tuple(turns)


def build_workspace_context_input(
    conversation: AssistantConversation,
    messages: Sequence[AssistantMessage],
    *,
    project_id: str,
    current_user_message_id: str,
) -> WorkspaceConversationContextInput:
    """Build the complete closed transcript plus the latest exact user task.

    ``messages`` must be the full durable workspace transcript.  Stable
    ``sequence_no`` values are the only ordering authority.  The current turn's
    already-persisted running assistant placeholder is validated but is not
    included in the active transcript revision or historical turns.
    """

    conversation_id, owner_id, ordered = _ordered_workspace_messages(
        conversation, messages, project_id=project_id
    )
    requested_current_id = _required_id(
        current_user_message_id,
        "current_user_message_id",
    )

    current_index = next(
        (
            index
            for index, (_, message) in enumerate(ordered)
            if str(message.id) == requested_current_id
        ),
        None,
    )
    if current_index is None:
        raise ValueError("current user message is not present in the workspace transcript")
    current_sequence, current_record = ordered[current_index]
    if str(current_record.role or "").strip().lower() != ConversationRole.USER.value:
        raise ValueError("current_user_message_id must identify a user message")
    if _message_status(current_record) != "completed":
        raise ValueError("current user message must be durably completed")

    later_users = [
        message
        for _, message in ordered[current_index + 1 :]
        if str(message.role or "").strip().lower() == ConversationRole.USER.value
    ]
    if later_users:
        raise ValueError("current user message must be the latest user intent")
    following = ordered[current_index + 1 :]
    if len(following) != 1:
        raise ValueError("current user message must be followed only by its running assistant")
    assistant_sequence, running_assistant = following[0]
    if (
        assistant_sequence != current_sequence + 1
        or str(running_assistant.role or "").strip().lower() != ConversationRole.ASSISTANT.value
        or _message_status(running_assistant) != "running"
    ):
        raise ValueError("current user message must be followed by its running assistant")

    turns = _closed_workspace_turns(ordered[:current_index])

    current_user = ConversationMessage(
        message_id=requested_current_id,
        sequence_no=current_sequence,
        role=ConversationRole.USER,
        content=_message_content(current_record),
    )
    snapshot = validate_transcript_snapshot(
        turns,
        current_user_message=current_user,
    )
    return WorkspaceConversationContextInput(
        identity=ConversationIdentity(
            kind=ConversationKind.WORKSPACE,
            id=conversation_id,
            revision=current_sequence,
            project_id=owner_id,
        ),
        turns=snapshot.turns,
        current_user_message=snapshot.current_user_message,
    )


def workspace_checkpoint_source_turns(
    conversation: AssistantConversation,
    messages: Sequence[AssistantMessage],
    *,
    project_id: str,
    before_sequence: int,
) -> tuple[ConversationTurn, ...]:
    """Reload the immutable closed prefix while allowing later appends.

    ``before_sequence`` is the original current-user boundary captured before
    checkpoint generation.  Messages at or after that boundary are deliberately
    ignored: they are not checkpoint sources and may grow while the model is
    compacting the older range.
    """

    if isinstance(before_sequence, bool) or before_sequence <= 0:
        raise ValueError("before_sequence must be positive")
    _, _, ordered = _ordered_workspace_messages(
        conversation, messages, project_id=project_id
    )
    boundary = next(
        (message for sequence, message in ordered if sequence == before_sequence),
        None,
    )
    if boundary is None or str(boundary.role or "").strip().lower() != "user":
        raise ValueError("checkpoint boundary must still identify a user message")
    return _closed_workspace_turns(
        tuple(item for item in ordered if item[0] < before_sequence)
    )


def _sortable_datetime(value: Any) -> tuple[int, str]:
    if isinstance(value, datetime):
        return (0, value.isoformat(timespec="microseconds"))
    return (1, "")


def _validated_workspace_run_steps(
    conversation: AssistantConversation,
    runs: Sequence[AssistantRun],
    steps: Sequence[AssistantRunStep],
    *,
    project_id: str,
    resolve_retries: bool,
) -> tuple[AssistantRunStep, ...]:
    conversation_id, owner_id = _validate_conversation_owner(
        conversation,
        project_id=project_id,
    )
    run_by_id: dict[str, AssistantRun] = {}
    for run in runs:
        run_id = _required_id(run.id, "run.id")
        if run_id in run_by_id:
            raise ValueError("workspace execution input contains duplicate run IDs")
        if str(run.project_id or "") != owner_id:
            raise ValueError("workspace run does not belong to the requested project")
        if str(run.conversation_id or "") != conversation_id:
            raise ValueError("workspace run does not belong to the requested conversation")
        run_by_id[run_id] = run

    ordered_steps: list[AssistantRunStep] = []
    step_ids: set[str] = set()
    for step in steps:
        step_id = _required_id(step.id, "run_step.id")
        if step_id in step_ids:
            raise ValueError("workspace execution input contains duplicate run-step IDs")
        if str(step.project_id or "") != owner_id:
            raise ValueError("workspace run step does not belong to the requested project")
        if str(step.run_id or "") not in run_by_id:
            raise ValueError("workspace run step does not belong to an owner-validated run")
        step_ids.add(step_id)
        ordered_steps.append(step)

    run_order = {
        run_id: index
        for index, run_id in enumerate(
            sorted(
                run_by_id,
                key=lambda item: (
                    _sortable_datetime(getattr(run_by_id[item], "created_at", None)),
                    item,
                ),
            )
        )
    }
    ordered_steps.sort(
        key=lambda step: (
            run_order[str(step.run_id)],
            _sortable_datetime(getattr(step, "created_at", None)),
            int(getattr(step, "iteration", 0) or 0),
            str(step.id),
        )
    )
    if not resolve_retries:
        return tuple(ordered_steps)

    by_id = {str(step.id): step for step in ordered_steps}
    parent_by_id: dict[str, str] = {}
    for step in ordered_steps:
        step_id = str(step.id)
        retry_of_id = str(getattr(step, "retry_of_step_id", None) or "").strip()
        if not retry_of_id:
            continue
        parent = by_id.get(retry_of_id)
        if parent is None:
            raise ValueError("retry run step is missing its durable original")
        if str(parent.run_id) != str(step.run_id):
            raise ValueError("retry run step belongs to a different run")
        if str(parent.tool or "") != str(step.tool or ""):
            raise ValueError("retry run step changed the original tool")
        parent_by_id[step_id] = retry_of_id

    explicit_resolutions: dict[str, str] = {}
    for original in ordered_steps:
        original_id = str(original.id)
        resolved_id = str(getattr(original, "resolved_step_id", None) or "").strip()
        if not resolved_id:
            continue
        resolved = by_id.get(resolved_id)
        if resolved is None:
            raise ValueError("resolved run step is missing from the durable step snapshot")
        if str(resolved.run_id) != str(original.run_id):
            raise ValueError("resolved run step belongs to a different run")
        if str(getattr(resolved, "retry_of_step_id", None) or "") != str(original.id):
            raise ValueError("resolved run step does not identify the original retry family")
        if str(resolved.status or "").strip().lower() not in _SUCCESS_STEP_STATUSES:
            raise ValueError("resolved run step is not successful")
        explicit_resolutions[original_id] = resolved_id

    def retry_root(step_id: str) -> str:
        seen: set[str] = set()
        current = step_id
        while current in parent_by_id:
            if current in seen:
                raise ValueError("workspace retry lineage contains a cycle")
            seen.add(current)
            current = parent_by_id[current]
        return current

    family_by_id = {step_id: retry_root(step_id) for step_id in by_id}
    successful_families = {
        family_by_id[str(step.id)]
        for step in ordered_steps
        if str(step.status or "").strip().lower() in _SUCCESS_STEP_STATUSES
        and str(step.id) in parent_by_id
    }
    superseded_ids = {
        str(step.id)
        for step in ordered_steps
        if family_by_id[str(step.id)] in successful_families
        and str(step.status or "").strip().lower() not in _SUCCESS_STEP_STATUSES
    }
    superseded_ids.update(explicit_resolutions)
    return tuple(step for step in ordered_steps if str(step.id) not in superseded_ids)


def workspace_execution_ledger_from_run_steps(
    conversation: AssistantConversation,
    runs: Sequence[AssistantRun],
    steps: Sequence[AssistantRunStep],
    *,
    project_id: str,
    revision_resolver: Callable[[str, str], int | str | None] | None = None,
    fold: bool = True,
) -> tuple[ExecutionLedgerEntry, ...]:
    """Build a trusted ledger from owner-checked durable workspace run steps."""

    validated = _validated_workspace_run_steps(
        conversation,
        runs,
        steps,
        project_id=project_id,
        resolve_retries=True,
    )
    entries = execution_ledger_from_run_steps(
        validated,
        revision_resolver=revision_resolver,
    )
    return fold_execution_ledger(entries) if fold else entries


def workspace_tool_receipts_from_run_steps(
    conversation: AssistantConversation,
    run: AssistantRun,
    steps: Sequence[AssistantRunStep],
    *,
    project_id: str,
    write_tools: Iterable[str] = (),
    reread_for_tool: Callable[[str], str | None] | None = None,
) -> tuple[ToolExecutionReceipt, ...]:
    """Build compact same-turn receipts after complete step persistence.

    A running/queued step is not a consumed transaction and therefore cannot
    be replaced by a receipt.
    """

    validated = _validated_workspace_run_steps(
        conversation,
        (run,),
        steps,
        project_id=project_id,
        resolve_retries=False,
    )
    open_steps = [
        str(step.id)
        for step in validated
        if str(step.status or "").strip().lower() in _OPEN_STEP_STATUSES
    ]
    if open_steps:
        raise ValueError("running workspace run steps cannot become tool receipts")
    return tool_receipts_from_run_steps(
        validated,
        write_tools=write_tools,
        reread_for_tool=reread_for_tool,
    )


__all__ = [
    "WorkspaceConversationContextInput",
    "build_workspace_context_input",
    "workspace_checkpoint_source_turns",
    "workspace_execution_ledger_from_run_steps",
    "workspace_tool_receipts_from_run_steps",
]
