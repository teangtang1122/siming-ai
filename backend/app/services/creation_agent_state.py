"""Creation turn state and durable transaction receipts."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.services.conversation_context.tool_transactions import (
    ToolExecutionReceipt,
    ToolTransaction,
)
from app.services.creation_agent_turn_records import seal_creation_runtime_snapshot

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
CompleteTurn = Callable[..., Awaitable[dict[str, Any]]]
EmitProgress = Callable[..., Awaitable[None]]
PersistRuntimeState = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class CreationTurnState:
    db: Session
    session: Any
    message: str
    model: str | None
    tool_mode: str
    system_prompt: str
    prepare_model_messages: Callable[..., Awaitable[list[dict[str, Any]]]]
    provider_max_tokens: Callable[[], int | None]
    provider_request_budget: Callable[[], Any]
    persist_runtime_state: PersistRuntimeState
    messages: list[dict[str, Any]]
    schemas: list[dict[str, Any]]
    baseline_revision: int
    extra_body: dict[str, Any] | None
    on_event: ProgressCallback | None
    reference_context: dict[str, Any] | None = None
    turn_execution_id: str = ""
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    write_results: list[dict[str, Any]] = field(default_factory=list)
    protocol_messages: list[dict[str, Any]] = field(default_factory=list)
    seen_write_calls: set[str] = field(default_factory=set)
    active_read_calls: set[str] = field(default_factory=set)
    final_reply: str = ""
    reply_status: str = "model"
    reply_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    prompt_metrics: list[dict[str, Any]] = field(default_factory=list)
    direct_mcp_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_transactions: list[ToolTransaction] = field(default_factory=list)
    pending_transaction_receipts: dict[
        str, tuple[ToolExecutionReceipt, ...]
    ] = field(default_factory=dict)
    current_ledger: list[ToolExecutionReceipt] = field(default_factory=list)
    compacted_transactions: list[dict[str, Any]] = field(default_factory=list)
    native_transaction_count: int = 0
    consecutive_capacity_rejections: int = 0
    active_categories: tuple[str, ...] = ()
    successful_write_count: int = 0
    failed_write_count: int = 0
    successful_read_count: int = 0


@dataclass(frozen=True)
class CreationExecutionBindings:
    complete_tool_turn: CompleteTurn
    emit_progress: EmitProgress
    tool_schemas: Callable[[tuple[str, ...]], list[dict[str, Any]]]
    category_tool_result: Callable[
        [dict[str, Any]],
        tuple[dict[str, Any], tuple[str, ...] | None],
    ]


def _consume_delivered_transactions(state: CreationTurnState) -> bool:
    """Acknowledge delivery without removing facts from the active turn."""

    consumed_any = False
    for index, transaction in enumerate(state.tool_transactions):
        if transaction.state.value == "delivered":
            state.tool_transactions[index] = transaction.mark_consumed()
            consumed_any = True
    if consumed_any:
        state.active_read_calls.clear()
    return consumed_any


def _archive_consumed_transactions(state: CreationTurnState) -> None:
    """Create audit receipts only after the final reply is complete."""

    pending = []
    for transaction in state.tool_transactions:
        if transaction.state.value != "consumed":
            pending.append(transaction)
            continue
        compactable = transaction.mark_compactable(turn_closed=True)
        receipts = state.pending_transaction_receipts.pop(
            transaction.transaction_id,
            (),
        )
        state.current_ledger.extend(receipts)
        state.compacted_transactions.append(
            compactable.to_dict(include_native_payload=False)
        )
    state.tool_transactions[:] = pending


def _all_execution_receipts(
    state: CreationTurnState,
) -> tuple[ToolExecutionReceipt, ...]:
    return (
        *state.current_ledger,
        *(
            receipt
            for transaction in state.tool_transactions
            for receipt in state.pending_transaction_receipts.get(
                transaction.transaction_id,
                (),
            )
        ),
    )


def _durable_runtime_snapshot(
    state: CreationTurnState,
    *,
    status: str = "running",
    in_progress_transaction: ToolTransaction | None = None,
    in_progress_receipts: tuple[ToolExecutionReceipt, ...] = (),
) -> dict[str, Any]:
    """Serialize only server state needed to audit/recover an interrupted turn."""

    return seal_creation_runtime_snapshot({
        "session_id": str(state.session.id),
        "status": status,
        "tool_mode": state.tool_mode,
        "tool_results": list(state.tool_results),
        "execution_receipts": [
            receipt.to_dict()
            for receipt in (
                *_all_execution_receipts(state),
                *in_progress_receipts,
            )
        ],
        "compacted_tool_transactions": list(state.compacted_transactions),
        "pending_tool_transactions": [
            transaction.to_dict()
            for transaction in (
                *state.tool_transactions,
                *(
                    (in_progress_transaction,)
                    if in_progress_transaction is not None
                    else ()
                ),
            )
        ],
        "successful_write_count": state.successful_write_count,
        "failed_write_count": state.failed_write_count,
        "successful_read_count": state.successful_read_count,
        "reference_context": state.reference_context,
        "turn_execution_id": state.turn_execution_id,
        "reply_status": state.reply_status,
        "reply_diagnostics": list(state.reply_diagnostics),
    })
