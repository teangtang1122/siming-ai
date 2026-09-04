"""Deterministic selection of the newest complete exact-turn tail."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .contracts import ConversationTurn


class MandatoryExactTurnsOverCapacity(ValueError):
    """Required exact history cannot fit and must not be checkpointed."""


@dataclass(frozen=True)
class RecentTurnSelection:
    checkpoint_turns: tuple[ConversationTurn, ...]
    exact_turns: tuple[ConversationTurn, ...]
    exact_turn_tokens: int
    available_tokens: int

    @property
    def exact_turn_count(self) -> int:
        return len(self.exact_turns)


def select_recent_turns(
    turns: Sequence[ConversationTurn],
    *,
    available_tokens: int,
    count_turn_tokens: Callable[[ConversationTurn], int],
    checkpoint_source_last_sequence: int | None = None,
    covered_sequence_ranges: Sequence[tuple[int, int]] = (),
) -> RecentTurnSelection:
    """Keep the largest newest exact history that fits ``available_tokens``.

    A turn is never split. Only completed, semantically complete visible turns
    may be selected for checkpoint segments, including eligible ranges after
    an ERROR/ABORTED/CANCELLED turn. Every other closed turn is mandatory exact;
    if those mandatory turns do not fit, selection fails explicitly.
    ``covered_sequence_ranges`` identifies already durable checkpoint segments
    and may therefore be non-contiguous.

    ``checkpoint_source_last_sequence`` is retained as a compatibility input
    for one legacy contiguous checkpoint and is normalized into the same range
    algorithm; integrations should pass explicit covered ranges.
    """

    if available_tokens < 0:
        raise ValueError("available_tokens must not be negative")
    ordered = list(turns)
    first_sequences = [turn.messages[0].sequence_no for turn in ordered]
    if first_sequences != sorted(first_sequences) or len(first_sequences) != len(
        set(first_sequences)
    ):
        raise ValueError("historical turns must be unique and chronological")
    previous_last = -1
    for turn in ordered:
        if not turn.closed:
            raise ValueError("recent-turn selection only accepts closed turns")
        first = turn.messages[0].sequence_no
        last = turn.messages[-1].sequence_no
        if first <= previous_last:
            raise ValueError("historical turns must not overlap")
        previous_last = last

    ranges = list(covered_sequence_ranges)
    if checkpoint_source_last_sequence is not None:
        ranges.append((0, checkpoint_source_last_sequence))
    normalized_ranges: list[tuple[int, int]] = []
    for first, last in sorted(ranges):
        if first < 0 or last < first:
            raise ValueError("covered checkpoint range is invalid")
        if normalized_ranges and first <= normalized_ranges[-1][1]:
            raise ValueError("covered checkpoint ranges must not overlap")
        normalized_ranges.append((first, last))

    candidates: list[ConversationTurn] = []
    for turn in ordered:
        first = turn.messages[0].sequence_no
        last = turn.messages[-1].sequence_no
        covering = [
            (range_first, range_last)
            for range_first, range_last in normalized_ranges
            if range_first <= first and last <= range_last
        ]
        if covering:
            continue
        if any(
            first <= range_first <= last or first <= range_last <= last
            for range_first, range_last in normalized_ranges
        ):
            raise ValueError("a turn cannot straddle the active checkpoint boundary")
        candidates.append(turn)

    costs: dict[str, int] = {}
    for turn in candidates:
        cost = int(count_turn_tokens(turn))
        if cost < 0:
            raise ValueError("turn token count must not be negative")
        costs[turn.turn_id] = cost

    mandatory = [turn for turn in candidates if not turn.checkpoint_eligible]
    mandatory_cost = sum(costs[turn.turn_id] for turn in mandatory)
    if mandatory_cost > available_tokens:
        raise MandatoryExactTurnsOverCapacity(
            "mandatory exact turns exceed available_tokens"
        )
    exact_ids = {turn.turn_id for turn in mandatory}
    used = mandatory_cost
    completed_tail_closed = False
    for index in range(len(candidates) - 1, -1, -1):
        turn = candidates[index]
        if not turn.checkpoint_eligible:
            continue
        if completed_tail_closed:
            continue
        cost = costs[turn.turn_id]
        if used + cost > available_tokens:
            completed_tail_closed = True
            continue
        exact_ids.add(turn.turn_id)
        used += cost

    exact_turns = tuple(turn for turn in candidates if turn.turn_id in exact_ids)
    checkpoint_turns = tuple(
        turn
        for turn in candidates
        if turn.checkpoint_eligible and turn.turn_id not in exact_ids
    )

    return RecentTurnSelection(
        checkpoint_turns=checkpoint_turns,
        exact_turns=exact_turns,
        exact_turn_tokens=used,
        available_tokens=available_tokens,
    )


__all__ = [
    "MandatoryExactTurnsOverCapacity",
    "RecentTurnSelection",
    "select_recent_turns",
]
