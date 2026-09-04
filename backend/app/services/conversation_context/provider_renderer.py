"""Provider-neutral rendering of a sealed ContextFrame."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .canonical import canonical_json, text_sha256
from .checkpoint_renderer import render_checkpoint_reference
from .context_frame import ContextFrame
from .contracts import ConversationTurn
from .tool_transactions import ToolTransactionState


class ContextLayer(StrEnum):
    SYSTEM_CONTRACT = "system_contract"
    HISTORICAL_REFERENCE = "historical_reference"
    RECENT_EXACT_TURN = "recent_exact_turn"
    CURRENT_USER = "current_user"
    CURRENT_TURN_LEDGER = "current_turn_ledger"
    PENDING_TOOL_TRANSACTION = "pending_tool_transaction"


@dataclass(frozen=True)
class RenderedContextMessage:
    message_id: str
    role: str
    content: str
    layer: ContextLayer
    tool_calls: tuple[dict[str, Any], ...] = ()
    tool_call_id: str | None = None
    reasoning_content: str = ""
    provider_state: tuple[dict[str, Any], ...] = ()

    def validation_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls:
            result["tool_calls"] = list(self.tool_calls)
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.reasoning_content:
            result["reasoning_content"] = self.reasoning_content
        if self.provider_state:
            result["provider_state"] = list(self.provider_state)
        return result

    def provider_dict(self) -> dict[str, Any]:
        result = self.validation_dict()
        result.pop("message_id", None)
        return result


@dataclass(frozen=True)
class RenderedContextRequest:
    messages: tuple[RenderedContextMessage, ...]
    current_user_message_id: str
    checkpoint_message_id: str | None

    def validation_messages(self) -> list[dict[str, Any]]:
        return [message.validation_dict() for message in self.messages]

    def provider_messages(self) -> list[dict[str, Any]]:
        return [message.provider_dict() for message in self.messages]


def render_historical_turn_status(turn: ConversationTurn) -> str | None:
    """Return a server-authored status receipt without changing exact prose."""

    if turn.status.value == "completed":
        return None
    return "\n".join(
        (
            "[SERVER_VERIFIED_HISTORICAL_TURN_STATUS]",
            "data_only: true",
            canonical_json({"turn_id": turn.turn_id, "status": turn.status.value}),
            "[/SERVER_VERIFIED_HISTORICAL_TURN_STATUS]",
        )
    )


def render_context_frame(
    frame: ContextFrame,
    *,
    system_prompt: str,
    verify_prompt_hash: bool = True,
    require_sendable: bool = True,
) -> RenderedContextRequest:
    """Render logical layers without provider-specific role rewriting.

    Provider adapters may translate this result to Anthropic or local CLI wire
    formats, but they must preserve layer order and re-run protocol validation
    on the final native message sequence.
    """

    if verify_prompt_hash and text_sha256(system_prompt) != frame.system_contract.prompt_hash:
        raise ValueError("system prompt does not match ContextFrame prompt_hash")
    if require_sendable:
        frame.budget.require_sendable()

    messages: list[RenderedContextMessage] = [
        RenderedContextMessage(
            message_id="context-system-contract",
            role="system",
            content=system_prompt,
            layer=ContextLayer.SYSTEM_CONTRACT,
        )
    ]
    checkpoint_message_id: str | None = None
    historical_events: list[tuple[int, int, object]] = []
    if frame.checkpoint is not None:
        # The active checkpoint is already the deterministic aggregate of the
        # full persisted segment chain.  Older segment records remain in
        # ``checkpoint_segments`` for integrity/overlap validation and audit,
        # but emitting one pointer per segment would make the provider request
        # grow linearly forever.
        historical_events.append(
            (frame.checkpoint.source_range.first_sequence, 0, frame.checkpoint)
        )
    for turn in frame.recent_turns:
        historical_events.append((turn.messages[0].sequence_no, 1, turn))

    for _, event_kind, value in sorted(historical_events, key=lambda item: (item[0], item[1])):
        if event_kind == 0:
            segment = value
            message_id = f"context-checkpoint:{segment.fingerprint}"
            checkpoint_message_id = message_id
            content = render_checkpoint_reference(segment)
            messages.append(
                RenderedContextMessage(
                    message_id=message_id,
                    role="user",
                    content=content,
                    layer=ContextLayer.HISTORICAL_REFERENCE,
                )
            )
            continue
        turn = value
        for message in turn.messages:
            messages.append(
                RenderedContextMessage(
                    message_id=message.message_id,
                    role=message.role.value,
                    content=message.content,
                    layer=ContextLayer.RECENT_EXACT_TURN,
                    tool_calls=message.tool_calls,
                    tool_call_id=message.tool_call_id,
                )
            )
        status_receipt = render_historical_turn_status(turn)
        if status_receipt is not None:
            messages.append(
                RenderedContextMessage(
                    message_id=f"context-turn-status:{turn.turn_id}",
                    role="assistant",
                    content=status_receipt,
                    layer=ContextLayer.RECENT_EXACT_TURN,
                )
            )

    messages.append(
        RenderedContextMessage(
            message_id=frame.current_user_message.message_id,
            role="user",
            content=frame.current_user_message.content,
            layer=ContextLayer.CURRENT_USER,
        )
    )

    if frame.current_turn_ledger:
        ledger_payload = [receipt.to_dict() for receipt in frame.current_turn_ledger]
        ledger_content = "\n".join(
            (
                "[SERVER_VERIFIED_EXECUTION_RECEIPTS]",
                "data_only: true",
                canonical_json(ledger_payload),
                "[/SERVER_VERIFIED_EXECUTION_RECEIPTS]",
            )
        )
        messages.append(
            RenderedContextMessage(
                message_id=f"context-ledger:{frame.calculate_hash()}",
                role="assistant",
                content=ledger_content,
                layer=ContextLayer.CURRENT_TURN_LEDGER,
            )
        )

    for transaction in frame.pending_tool_transactions:
        if transaction.state is ToolTransactionState.PENDING:
            raise ValueError("pending tool transaction is incomplete and not request-ready")
        if transaction.state is ToolTransactionState.COMPACTABLE:
            raise ValueError("compactable transaction must be replaced by a server ledger receipt")
        for index, raw in enumerate(transaction.native_messages()):
            messages.append(
                RenderedContextMessage(
                    message_id=(
                        transaction.assistant_message_id
                        if index == 0
                        else f"{transaction.transaction_id}:result:{raw['tool_call_id']}"
                    ),
                    role=str(raw["role"]),
                    content=str(raw.get("content") or ""),
                    layer=ContextLayer.PENDING_TOOL_TRANSACTION,
                    tool_calls=tuple(raw.get("tool_calls") or ()),
                    tool_call_id=(
                        str(raw.get("tool_call_id"))
                        if raw.get("tool_call_id") is not None
                        else None
                    ),
                    reasoning_content=str(raw.get("reasoning_content") or ""),
                    provider_state=tuple(raw.get("provider_state") or ()),
                )
            )

    return RenderedContextRequest(
        messages=tuple(messages),
        current_user_message_id=frame.current_user_message.message_id,
        checkpoint_message_id=checkpoint_message_id,
    )


__all__ = [
    "ContextLayer",
    "RenderedContextMessage",
    "RenderedContextRequest",
    "render_historical_turn_status",
    "render_context_frame",
]
