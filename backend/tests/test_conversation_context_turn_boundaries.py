"""Regression tests for exact-tail and checkpoint turn boundaries."""

from __future__ import annotations

import pytest

from app.services.conversation_context.contracts import (
    ConversationMessage,
    ConversationRole,
    ConversationTurn,
    TurnStatus,
)
from app.services.conversation_context.errors import (
    ConversationContextError,
    ConversationContextErrorCode,
)
from app.services.conversation_context.recent_turns import (
    MandatoryExactTurnsOverCapacity,
    select_recent_turns,
)
from app.services.conversation_context.transcript import (
    checkpoint_source_messages,
    validate_transcript_snapshot,
)


def _visible_turn(
    index: int,
    *,
    status: TurnStatus = TurnStatus.COMPLETED,
) -> ConversationTurn:
    first = index * 2 - 1
    return ConversationTurn(
        turn_id=f"turn-{index}",
        status=status,
        messages=(
            ConversationMessage(
                message_id=f"user-{index}",
                sequence_no=first,
                role=ConversationRole.USER,
                content=f"author {index}",
            ),
            ConversationMessage(
                message_id=f"assistant-{index}",
                sequence_no=first + 1,
                role=ConversationRole.ASSISTANT,
                content=f"answer {index}",
            ),
        ),
    )


def _current(sequence_no: int, *, message_id: str = "current") -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        sequence_no=sequence_no,
        role=ConversationRole.USER,
        content="  latest exact intent\n",
    )


def test_only_completed_safe_visible_turn_is_checkpoint_eligible() -> None:
    completed = _visible_turn(1)
    assert completed.safe_visible_projection is True
    assert completed.checkpoint_eligible is True

    for status in (TurnStatus.ERROR, TurnStatus.ABORTED, TurnStatus.CANCELLED):
        turn = _visible_turn(1, status=status)
        assert turn.safe_visible_projection is True
        assert turn.checkpoint_eligible is False

    raw_tool_turn = ConversationTurn(
        turn_id="raw-tool",
        status=TurnStatus.COMPLETED,
        messages=(
            completed.messages[0],
            ConversationMessage(
                message_id="assistant-tool",
                sequence_no=2,
                role=ConversationRole.ASSISTANT,
                tool_calls=(
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "read_outline", "arguments": "{}"},
                    },
                ),
            ),
        ),
    )
    assert raw_tool_turn.safe_visible_projection is False
    assert raw_tool_turn.checkpoint_eligible is False

    blank_assistant_turn = ConversationTurn(
        turn_id="blank-assistant",
        status=TurnStatus.COMPLETED,
        messages=(
            completed.messages[0],
            ConversationMessage(
                message_id="assistant-blank",
                sequence_no=2,
                role=ConversationRole.ASSISTANT,
                content=" \n\t",
            ),
        ),
    )
    assert blank_assistant_turn.safe_visible_projection is False
    assert blank_assistant_turn.checkpoint_eligible is False


def test_durable_message_sequences_are_strictly_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        ConversationMessage(
            message_id="zero-sequence",
            sequence_no=0,
            role=ConversationRole.USER,
            content="not durable",
        )

    with pytest.raises(ValueError, match="preserved verbatim"):
        validate_transcript_snapshot(
            (),
            current_user_message=ConversationMessage(
                message_id="blank-current",
                sequence_no=1,
                role=ConversationRole.USER,
                content=" \n\t",
            ),
        )


def test_selector_keeps_semantically_incomplete_completed_turn_exact() -> None:
    incomplete = ConversationTurn(
        turn_id="incomplete-completed",
        status=TurnStatus.COMPLETED,
        messages=(
            ConversationMessage(
                message_id="incomplete-user",
                sequence_no=1,
                role=ConversationRole.USER,
                content="persist me exactly",
            ),
        ),
    )
    later = ConversationTurn(
        turn_id="later-complete",
        status=TurnStatus.COMPLETED,
        messages=tuple(
            ConversationMessage(
                message_id=f"later-{message.role.value}",
                sequence_no=message.sequence_no + 1,
                role=message.role,
                content=message.content,
            )
            for message in _visible_turn(1).messages
        ),
    )

    selection = select_recent_turns(
        (incomplete, later),
        available_tokens=10,
        count_turn_tokens=lambda _turn: 10,
    )

    assert [turn.turn_id for turn in selection.exact_turns] == ["incomplete-completed"]
    assert [turn.turn_id for turn in selection.checkpoint_turns] == ["later-complete"]
    with pytest.raises(ConversationContextError) as caught:
        checkpoint_source_messages((incomplete,))
    assert caught.value.code is ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY


def test_cross_turn_projection_rejects_raw_or_incomplete_tool_protocol() -> None:
    raw_tool_turn = ConversationTurn(
        turn_id="unsafe-cross-turn-tool",
        status=TurnStatus.ERROR,
        messages=(
            ConversationMessage(
                message_id="tool-user",
                sequence_no=1,
                role=ConversationRole.USER,
                content="run a tool",
            ),
            ConversationMessage(
                message_id="tool-assistant",
                sequence_no=2,
                role=ConversationRole.ASSISTANT,
                content="tool failed",
                tool_calls=(
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "write", "arguments": "{}"},
                    },
                ),
            ),
        ),
    )

    with pytest.raises(ConversationContextError) as caught:
        validate_transcript_snapshot(
            (raw_tool_turn,),
            current_user_message=_current(3),
        )
    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID


def test_exception_visible_pair_stays_exact_and_latest_user_is_unique() -> None:
    failed = _visible_turn(1, status=TurnStatus.ERROR)
    current = _current(3)
    snapshot = validate_transcript_snapshot((failed,), current_user_message=current)

    assert snapshot.turns == (failed,)
    assert snapshot.current_user_message.content == "  latest exact intent\n"

    with pytest.raises(ValueError, match="must not be duplicated"):
        validate_transcript_snapshot(
            (failed,),
            current_user_message=_current(3, message_id=failed.messages[0].message_id),
        )


def test_negative_mandatory_turn_cost_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        select_recent_turns(
            (_visible_turn(1, status=TurnStatus.CANCELLED),),
            available_tokens=1,
            count_turn_tokens=lambda _turn: -1,
        )


def test_mandatory_exact_over_capacity_uses_a_stable_typed_failure() -> None:
    with pytest.raises(MandatoryExactTurnsOverCapacity):
        select_recent_turns(
            (_visible_turn(1, status=TurnStatus.ABORTED),),
            available_tokens=9,
            count_turn_tokens=lambda _turn: 10,
        )
