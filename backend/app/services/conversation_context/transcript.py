"""Canonical, provider-neutral transcript projection for conversation context.

The durable transcript remains the authority.  This module only validates the
closed, human-visible turn projection supplied by a workspace or Creation
adapter; it never truncates text and never reconstructs historical tool calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .checkpoint_validator import CheckpointSourceMessage, checkpoint_source_hash
from .contracts import (
    ConversationMessage,
    ConversationRole,
    ConversationTurn,
    SourceRange,
)
from .errors import ConversationContextError, ConversationContextErrorCode


@dataclass(frozen=True)
class TranscriptSnapshot:
    """A validated immutable view used by one context assembly attempt."""

    turns: tuple[ConversationTurn, ...]
    current_user_message: ConversationMessage

    @property
    def revision(self) -> int:
        return self.current_user_message.sequence_no

    @property
    def messages(self) -> tuple[ConversationMessage, ...]:
        return tuple(message for turn in self.turns for message in turn.messages)


def validate_transcript_snapshot(
    turns: Sequence[ConversationTurn],
    *,
    current_user_message: ConversationMessage,
) -> TranscriptSnapshot:
    """Validate the single cross-agent transcript contract.

    Historical projections intentionally contain only the visible user and
    final assistant messages.  Replaying an old assistant ``tool_calls`` batch
    without its native result protocol is unsafe, while replaying the entire
    protocol across turns is unnecessary.  Same-turn native transactions are
    carried separately by :class:`ToolTransaction`.
    """

    if current_user_message.role is not ConversationRole.USER:
        raise ValueError("current_user_message must have the user role")
    if current_user_message.sequence_no <= 0:
        raise ValueError("current_user_message sequence must be positive")
    if not current_user_message.content.strip():
        raise ValueError("current_user_message must be preserved verbatim")

    ordered = tuple(turns)
    seen_turn_ids: set[str] = set()
    seen_message_ids: set[str] = set()
    previous_sequence = 0
    for turn in ordered:
        if turn.turn_id in seen_turn_ids:
            raise ValueError("historical turn IDs must be unique")
        seen_turn_ids.add(turn.turn_id)
        if not turn.closed:
            raise ValueError("historical transcript may only contain closed turns")
        if len(turn.messages) != 2:
            raise ConversationContextError(
                ConversationContextErrorCode.PROTOCOL_INVALID,
                "跨回合历史必须投影为一条逐字 user 和一条最终逐字 assistant。",
                details={"turn_id": turn.turn_id},
            )
        user, assistant = turn.messages
        if (
            user.role is not ConversationRole.USER
            or assistant.role is not ConversationRole.ASSISTANT
        ):
            raise ConversationContextError(
                ConversationContextErrorCode.PROTOCOL_INVALID,
                "跨回合历史必须保持 user/final assistant 顺序。",
                details={"turn_id": turn.turn_id},
            )
        if user.tool_calls or assistant.tool_calls or user.tool_call_id or assistant.tool_call_id:
            raise ConversationContextError(
                ConversationContextErrorCode.PROTOCOL_INVALID,
                "跨回合历史不得重放不完整的工具协议。",
                details={"turn_id": turn.turn_id},
            )
        if not user.content.strip() or not assistant.content.strip():
            raise ConversationContextError(
                ConversationContextErrorCode.PROTOCOL_INVALID,
                "跨回合可见 user/final assistant 原文不得为空。",
                details={"turn_id": turn.turn_id},
            )
        if assistant.sequence_no != user.sequence_no + 1:
            raise ConversationContextError(
                ConversationContextErrorCode.PROTOCOL_INVALID,
                "跨回合可见 user/final assistant 必须占用连续 sequence。",
                details={"turn_id": turn.turn_id},
            )
        if user.sequence_no <= previous_sequence:
            raise ValueError("historical turns must be globally chronological")
        previous_sequence = assistant.sequence_no
        if previous_sequence >= current_user_message.sequence_no:
            raise ValueError("historical turns must precede the current user message")
        for message in turn.messages:
            if message.message_id in seen_message_ids:
                raise ValueError("historical message IDs must be unique")
            seen_message_ids.add(message.message_id)

    if current_user_message.message_id in seen_message_ids:
        raise ValueError("current user message must not be duplicated in history")
    return TranscriptSnapshot(ordered, current_user_message)


def turns_after_checkpoint(
    turns: Sequence[ConversationTurn],
    *,
    checkpoint_last_sequence: int | None,
) -> tuple[ConversationTurn, ...]:
    """Return turns not covered by an active checkpoint without splitting one."""

    if checkpoint_last_sequence is None:
        return tuple(turns)
    remaining: list[ConversationTurn] = []
    for turn in turns:
        first = turn.messages[0].sequence_no
        last = turn.messages[-1].sequence_no
        if last <= checkpoint_last_sequence:
            continue
        if first <= checkpoint_last_sequence < last:
            raise ConversationContextError(
                ConversationContextErrorCode.SOURCE_CHANGED,
                "checkpoint 边界切入了一个完整回合。",
                details={"turn_id": turn.turn_id},
            )
        remaining.append(turn)
    return tuple(remaining)


def checkpoint_source_messages(
    turns: Sequence[ConversationTurn],
) -> tuple[CheckpointSourceMessage, ...]:
    """Project completed closed turns into the immutable checkpoint source."""

    result: list[CheckpointSourceMessage] = []
    for turn in turns:
        if not turn.checkpoint_eligible:
            raise ConversationContextError(
                ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY,
                "只有 completed 且语义完整的可见回合可以进入 checkpoint。",
                details={
                    "turn_id": turn.turn_id,
                    "status": turn.status.value,
                    "safe_visible_projection": turn.safe_visible_projection,
                },
            )
        result.extend(
            CheckpointSourceMessage(
                message_id=message.message_id,
                sequence_no=message.sequence_no,
                role=message.role,
                content=message.content,
                status="completed",
            )
            for message in turn.messages
        )
    if not result:
        return ()
    sequences = [message.sequence_no for message in result]
    if any(right != left + 1 for left, right in zip(sequences, sequences[1:], strict=False)):
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 来源必须是连续的完整消息范围。",
            details={"first_sequence": sequences[0], "last_sequence": sequences[-1]},
        )
    return tuple(result)


def source_range_for_turns(turns: Sequence[ConversationTurn]) -> SourceRange:
    messages = checkpoint_source_messages(turns)
    if not messages:
        raise ValueError("checkpoint source must contain at least one completed turn")
    return SourceRange(
        first_sequence=messages[0].sequence_no,
        last_sequence=messages[-1].sequence_no,
        message_count=len(messages),
        source_hash=checkpoint_source_hash(messages),
    )


__all__ = [
    "TranscriptSnapshot",
    "checkpoint_source_messages",
    "source_range_for_turns",
    "turns_after_checkpoint",
    "validate_transcript_snapshot",
]
