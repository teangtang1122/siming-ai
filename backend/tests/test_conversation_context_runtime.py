from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services.conversation_context import (
    CapacityAssurance,
    ContextFrame,
    ContextFrameIntegrity,
    ConversationCheckpoint,
    ConversationContextError,
    ConversationContextErrorCode,
    ConversationIdentity,
    ConversationKind,
    ConversationMessage,
    ConversationTurn,
    GenerationModelBinding,
    RequestTokenComponents,
    SemanticNavigation,
    SourceRange,
    SystemContract,
    Utf8ByteTokenCounter,
    build_request_budget,
    render_context_frame,
    select_recent_turns,
)
from app.services.conversation_context.canonical import text_sha256
from app.services.conversation_context.contracts import ConversationRole, TurnStatus
from app.services.conversation_context.runtime import (
    prepare_conversation_context,
    resolve_generation_model_binding,
)


def _turn(index: int, *, size: int = 10, status: TurnStatus = TurnStatus.COMPLETED):
    first = (index - 1) * 2 + 1
    return ConversationTurn(
        turn_id=f"turn-{index}",
        status=status,
        messages=(
            ConversationMessage(
                message_id=f"user-{index}",
                sequence_no=first,
                role=ConversationRole.USER,
                content=f"u{index}-" + "x" * size,
            ),
            ConversationMessage(
                message_id=f"assistant-{index}",
                sequence_no=first + 1,
                role=ConversationRole.ASSISTANT,
                content=f"a{index}-" + "y" * size,
            ),
        ),
    )


def _checkpoint(first: int, last: int, conversation_id: str = "conversation"):
    return ConversationCheckpoint(
        scope=ConversationKind.WORKSPACE,
        conversation_id=conversation_id,
        source_range=SourceRange(
            first_sequence=first,
            last_sequence=last,
            message_count=last - first + 1,
            source_hash=f"{first:02d}".ljust(64, "a"),
        ),
        semantic_navigation=SemanticNavigation(),
    )


def _binding(*, prompt: str = "system", window: int = 50_000):
    return GenerationModelBinding(
        task_type="assistant",
        provider="openai",
        model_name="test",
        normalized_model="openai:test",
        protocol="chat_completions",
        context_window_tokens=window,
        max_output_tokens=1_024,
        token_counter_id="conservative.utf8_bytes.v1",
        capacity_assurance=CapacityAssurance.CONSERVATIVE,
        prompt_contract_hash=text_sha256(prompt),
        tool_schema_hash="4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e1b17a84aa28ccad3549b4",
        config_fingerprint="config",
    )


def test_selector_crosses_aborted_gap_without_compacting_it() -> None:
    turns = (
        _turn(1),
        _turn(2, status=TurnStatus.ABORTED),
        _turn(3),
        _turn(4),
    )
    costs = {turn.turn_id: 10 for turn in turns}
    selection = select_recent_turns(
        turns,
        available_tokens=20,
        count_turn_tokens=lambda turn: costs[turn.turn_id],
    )
    assert [turn.turn_id for turn in selection.exact_turns] == ["turn-2", "turn-4"]
    assert [turn.turn_id for turn in selection.checkpoint_turns] == ["turn-1", "turn-3"]

    after_first_segment = select_recent_turns(
        turns,
        available_tokens=20,
        count_turn_tokens=lambda turn: costs[turn.turn_id],
        covered_sequence_ranges=((1, 2),),
    )
    assert [turn.turn_id for turn in after_first_segment.checkpoint_turns] == ["turn-3"]


def test_renderer_renders_latest_aggregate_and_interleaves_exception_exact_turn() -> None:
    counter = Utf8ByteTokenCounter()
    binding = _binding()
    budget = build_request_budget(
        binding=binding,
        counter=counter,
        components=RequestTokenComponents(current_user_tokens=1),
        safety_margin_tokens=100,
    )
    first = _checkpoint(1, 2)
    latest = _checkpoint(5, 6)
    current = ConversationMessage(
        message_id="current",
        sequence_no=9,
        role=ConversationRole.USER,
        content="continue",
    )
    frame = ContextFrame(
        conversation=ConversationIdentity(
            kind=ConversationKind.WORKSPACE,
            id="conversation",
            revision=9,
            project_id="project",
        ),
        model_binding=binding,
        system_contract=SystemContract(
            prompt_hash=text_sha256("system"),
            active_tool_category_hash="tools",
        ),
        checkpoint=latest,
        recent_turns=(_turn(2, status=TurnStatus.ABORTED), _turn(4)),
        current_user_message=current,
        current_turn_ledger=(),
        pending_tool_transactions=(),
        budget=budget,
        integrity=ContextFrameIntegrity(9, latest.fingerprint),
        checkpoint_segments=(first, latest),
    ).sealed()

    rendered = render_context_frame(frame, system_prompt="system")
    ids = [message.message_id for message in rendered.messages]
    assert f"context-checkpoint:{first.fingerprint}" not in ids
    assert ids.index("user-2") < ids.index("assistant-2")
    assert ids.index("assistant-2") < ids.index("context-turn-status:turn-2")
    assert ids.index("context-turn-status:turn-2") < ids.index(
        f"context-checkpoint:{latest.fingerprint}"
    )
    assert ids.index(f"context-checkpoint:{latest.fingerprint}") < ids.index("user-4")


def test_renderer_preserves_exception_prose_and_emits_exact_status_receipts() -> None:
    turns = (
        _turn(1),
        _turn(2, status=TurnStatus.ERROR),
        _turn(3, status=TurnStatus.ABORTED),
        _turn(4, status=TurnStatus.CANCELLED),
    )
    current = ConversationMessage(
        message_id="current",
        sequence_no=9,
        role=ConversationRole.USER,
        content="continue",
    )
    counter = Utf8ByteTokenCounter()
    binding = _binding()
    budget = build_request_budget(
        binding=binding,
        counter=counter,
        components=RequestTokenComponents(current_user_tokens=1),
        safety_margin_tokens=100,
    )
    frame = ContextFrame(
        conversation=ConversationIdentity(
            kind=ConversationKind.WORKSPACE,
            id="conversation",
            revision=9,
            project_id="project",
        ),
        model_binding=binding,
        system_contract=SystemContract(
            prompt_hash=text_sha256("system"),
            active_tool_category_hash="tools",
        ),
        checkpoint=None,
        recent_turns=turns,
        current_user_message=current,
        current_turn_ledger=(),
        pending_tool_transactions=(),
        budget=budget,
        integrity=ContextFrameIntegrity(9, None),
    ).sealed()

    rendered = render_context_frame(frame, system_prompt="system")
    by_id = {message.message_id: message for message in rendered.messages}
    assert "context-turn-status:turn-1" not in by_id
    for index, status in (
        (2, "error"),
        (3, "aborted"),
        (4, "cancelled"),
    ):
        assert by_id[f"user-{index}"].content == turns[index - 1].messages[0].content
        assert by_id[f"assistant-{index}"].content == turns[index - 1].messages[1].content
        receipt = by_id[f"context-turn-status:turn-{index}"].content
        assert f'"status":"{status}"' in receipt


class _Orchestrator:
    def __init__(self, *, known: bool, window: int = 50_000):
        self.known = known
        self.window = window

    def resolve_model_profile(self, model, task_type):
        return SimpleNamespace(
            provider="openai",
            model_name="test",
            context_window_tokens=self.window,
            max_output_tokens=1_024,
            safety_margin_tokens=256,
            known=self.known,
        )


class _Store:
    def __init__(self):
        self.state = SimpleNamespace(
            revision=0,
            active_checkpoint_id=None,
            active_source_last_sequence=0,
            last_budget_json={},
            updated_at=datetime.utcnow(),
        )
        self.records = []
        self.sources = {}
        self.commits = 0

    def context_state(self, kind, conversation_id, *, owner_id):
        return self.state

    ensure_context_state = context_state

    def context_checkpoints(self, kind, conversation_id, *, owner_id):
        return list(self.records)

    def context_checkpoint(self, kind, conversation_id, checkpoint_id, *, owner_id):
        return next((item for item in self.records if item.id == checkpoint_id), None)

    def context_checkpoint_sources(self, kind, conversation_id, checkpoint_id, *, owner_id):
        return list(self.sources.get(checkpoint_id, ()))

    def create_context_checkpoint(self, kind, conversation_id, *, owner_id, **values):
        existing = next(
            (item for item in self.records if item.idempotency_key == values["idempotency_key"]),
            None,
        )
        if existing:
            return existing
        record = SimpleNamespace(
            id=f"checkpoint-{len(self.records) + 1}",
            semantic_navigation_json={},
            author_quotes_json=[],
            execution_ledger_json=[],
            project_refs_json=[],
            validation_json={},
            original_tokens=None,
            checkpoint_tokens=None,
            error_code=None,
            error_detail=None,
            cancel_requested_at=None,
            created_at=datetime.utcnow(),
            completed_at=None,
            updated_at=datetime.utcnow(),
            **values,
        )
        self.records.append(record)
        return record

    def add_context_checkpoint_sources(
        self, kind, conversation_id, checkpoint_id, sources, *, owner_id
    ):
        rows = [SimpleNamespace(**source) for source in sources]
        self.sources[checkpoint_id] = rows
        return rows

    def update_context_checkpoint_status(
        self,
        kind,
        conversation_id,
        checkpoint_id,
        new_status,
        *,
        owner_id,
        expected_statuses=None,
        **values,
    ):
        record = self.context_checkpoint(kind, conversation_id, checkpoint_id, owner_id=owner_id)
        if record is None or (
            expected_statuses is not None and record.status not in set(expected_statuses)
        ):
            return None
        record.status = new_status
        for key, value in values.items():
            setattr(record, key, value)
        record.updated_at = datetime.utcnow()
        if new_status in {"ready", "failed", "cancelled", "superseded"}:
            record.completed_at = record.updated_at
        return record

    def publish_context_checkpoint(
        self,
        kind,
        conversation_id,
        checkpoint_id,
        expected_revision,
        *,
        owner_id,
        last_budget_json=None,
    ):
        record = self.context_checkpoint(kind, conversation_id, checkpoint_id, owner_id=owner_id)
        if record is None or record.status != "ready" or self.state.revision != expected_revision:
            return False
        old = self.context_checkpoint(
            kind,
            conversation_id,
            self.state.active_checkpoint_id,
            owner_id=owner_id,
        )
        if old is not None and old.id != record.id:
            old.status = "superseded"
        self.state.active_checkpoint_id = record.id
        self.state.active_source_last_sequence = record.source_last_sequence
        self.state.revision += 1
        return True

    def commit_context_phase(self):
        self.commits += 1

    def refresh_context_phase(self):
        return None


def test_prepare_short_history_uses_exact_turns_without_checkpoint_call() -> None:
    turns = (_turn(1), _turn(2))
    current = ConversationMessage(
        message_id="current",
        sequence_no=5,
        role=ConversationRole.USER,
        content="continue",
    )
    called = False

    async def completion(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("checkpoint model must not be called")

    prepared = asyncio.run(
        prepare_conversation_context(
            store=_Store(),
            orchestrator=_Orchestrator(known=True),
            conversation=ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=5,
                project_id="project",
            ),
            owner_id="project",
            turns=turns,
            current_user_message=current,
            model="openai:test",
            task_type="assistant",
            protocol="chat_completions",
            system_prompt="system",
            current_tools=(),
            reload_turns=lambda: turns,
            checkpoint_completion=completion,
        )
    )

    assert called is False
    assert prepared.checkpoint is None
    assert [turn.turn_id for turn in prepared.frame.recent_turns] == ["turn-1", "turn-2"]


def test_runtime_instruction_is_rendered_in_system_not_as_second_user() -> None:
    current = ConversationMessage(
        message_id="current",
        sequence_no=1,
        role=ConversationRole.USER,
        content="作者当前要求",
    )
    prepared = asyncio.run(
        prepare_conversation_context(
            store=_Store(),
            orchestrator=_Orchestrator(known=True),
            conversation=ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=1,
                project_id="project",
            ),
            owner_id="project",
            turns=(),
            current_user_message=current,
            model="openai:test",
            task_type="assistant",
            protocol="chat_completions",
            system_prompt="system",
            current_tools=(),
            reload_turns=lambda: (),
            extra_runtime_instruction="只总结真实执行结果。",
        )
    )

    assert "[SERVER_RUNTIME_INSTRUCTION]" in prepared.provider_messages[0]["content"]
    assert "只总结真实执行结果。" in prepared.provider_messages[0]["content"]
    assert [message["role"] for message in prepared.provider_messages].count("user") == 1
    assert prepared.provider_messages[-1] == {
        "role": "user",
        "content": "作者当前要求",
    }


def test_unknown_profile_uses_bounded_fallback_and_can_send() -> None:
    binding, counter, _ = resolve_generation_model_binding(
        orchestrator=_Orchestrator(known=False, window=256_000),
        model="custom:test",
        task_type="assistant",
        protocol="chat_completions",
        system_prompt="system",
        current_tools=(),
    )
    assert binding.capacity_assurance is CapacityAssurance.UNVERIFIED
    assert counter.counter_id == "fallback.utf8_bytes.v1"
    budget = build_request_budget(
        binding=binding,
        counter=counter,
        components=RequestTokenComponents(current_user_tokens=1),
        safety_margin_tokens=0,
    )
    assert budget.bounded_fallback is True
    budget.require_sendable()


def test_prepare_generates_durable_segments_before_business_context() -> None:
    turns = tuple(_turn(index, size=900) for index in range(1, 9))
    current = ConversationMessage(
        message_id="current",
        sequence_no=17,
        role=ConversationRole.USER,
        content="continue",
    )
    store = _Store()
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["temperature"] == 0
        assert kwargs["tools"] == []
        assert kwargs["extra_body"]["response_format"]["json_schema"]["schema"]
        return {
            "content": json.dumps(
                {
                    "schema": "conversation_checkpoint_navigation.v1",
                    "semantic_navigation": {
                        "authority": "non_authoritative_navigation",
                        "current_objectives": [],
                        "resolved_decisions": [],
                        "superseded_directions": [],
                        "unresolved_questions": [],
                        "next_context_needed": [],
                    },
                    "author_quote_positions": [],
                    "prior_author_quote_states": [],
                }
            ),
            "tool_calls": None,
        }

    prepared = asyncio.run(
        prepare_conversation_context(
            store=store,
            orchestrator=_Orchestrator(known=True, window=8_000),
            conversation=ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=17,
                project_id="project",
            ),
            owner_id="project",
            turns=turns,
            current_user_message=current,
            model="openai:test",
            task_type="assistant",
            protocol="chat_completions",
            system_prompt="system",
            current_tools=(),
            reload_turns=lambda: turns,
            checkpoint_completion=completion,
        )
    )

    assert calls >= 1
    assert prepared.checkpoint is not None
    assert store.state.active_checkpoint_id == prepared.checkpoint["id"]
    assert all(record.status in {"ready", "superseded"} for record in store.records)
    assert store.commits >= calls * 2
    assert prepared.provider_messages[-1]["content"] == "continue"


def test_checkpoint_publish_allows_closed_turns_appended_after_immutable_source() -> None:
    turns = tuple(_turn(index, size=900) for index in range(1, 9))
    current = ConversationMessage(
        message_id="current",
        sequence_no=17,
        role=ConversationRole.USER,
        content="old request now superseded",
    )
    reloaded = list(turns)
    store = _Store()

    async def completion(**_kwargs):
        # Simulate the original current turn closing and a newer user being
        # appended while this immutable older source is outside the DB phase.
        if len(reloaded) == len(turns):
            reloaded.append(_turn(9, size=20))
        return {
            "content": json.dumps(
                {
                    "schema": "conversation_checkpoint_navigation.v1",
                    "semantic_navigation": {
                        "authority": "non_authoritative_navigation",
                        "current_objectives": [],
                        "resolved_decisions": [],
                        "superseded_directions": [],
                        "unresolved_questions": [],
                        "next_context_needed": [],
                    },
                    "author_quote_positions": [],
                    "prior_author_quote_states": [],
                }
            ),
            "tool_calls": None,
        }

    prepared = asyncio.run(
        prepare_conversation_context(
            store=store,
            orchestrator=_Orchestrator(known=True, window=8_000),
            conversation=ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=17,
                project_id="project",
            ),
            owner_id="project",
            turns=turns,
            current_user_message=current,
            model="openai:test",
            task_type="assistant",
            protocol="chat_completions",
            system_prompt="system",
            current_tools=(),
            reload_turns=lambda: tuple(reloaded),
            checkpoint_completion=completion,
        )
    )

    assert prepared.checkpoint is not None
    assert store.records[-1].status == "ready"
    assert prepared.provider_messages[-1] == {
        "role": "user",
        "content": current.content,
    }


def test_switching_to_a_smaller_model_replans_without_changing_transcript() -> None:
    turns = tuple(_turn(index, size=900) for index in range(1, 9))
    original_first_user = turns[0].messages[0].content
    current = ConversationMessage(
        message_id="current",
        sequence_no=17,
        role=ConversationRole.USER,
        content="continue after model switch",
    )
    store = _Store()
    calls = 0

    async def completion(**kwargs):
        nonlocal calls
        calls += 1
        return {
            "content": json.dumps(
                {
                    "schema": "conversation_checkpoint_navigation.v1",
                    "semantic_navigation": {
                        "authority": "non_authoritative_navigation",
                        "current_objectives": [],
                        "resolved_decisions": [],
                        "superseded_directions": [],
                        "unresolved_questions": [],
                        "next_context_needed": [],
                    },
                    "author_quote_positions": [],
                    "prior_author_quote_states": [],
                }
            ),
            "tool_calls": None,
        }

    async def scenario():
        common = {
            "store": store,
            "conversation": ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=17,
                project_id="project",
            ),
            "owner_id": "project",
            "turns": turns,
            "current_user_message": current,
            "model": "openai:test",
            "task_type": "assistant",
            "protocol": "chat_completions",
            "system_prompt": "system",
            "current_tools": (),
            "reload_turns": lambda: turns,
            "checkpoint_completion": completion,
        }
        large = await prepare_conversation_context(
            orchestrator=_Orchestrator(known=True, window=50_000),
            **common,
        )
        small = await prepare_conversation_context(
            orchestrator=_Orchestrator(known=True, window=8_000),
            **common,
        )
        return large, small

    large, small = asyncio.run(scenario())

    assert calls >= 1
    assert large.checkpoint is None
    assert len(large.frame.recent_turns) == len(turns)
    assert small.checkpoint is not None
    assert len(small.frame.recent_turns) < len(turns)
    assert small.provider_messages[-1] == {
        "role": "user",
        "content": current.content,
    }
    assert turns[0].messages[0].content == original_first_user


def test_prepare_rolls_completed_segments_across_multiple_exception_gaps() -> None:
    turns = (
        _turn(1, size=900),
        _turn(2, size=900, status=TurnStatus.CANCELLED),
        _turn(3, size=900),
        _turn(4, size=900, status=TurnStatus.ERROR),
        _turn(5, size=900),
    )
    current = ConversationMessage(
        message_id="current",
        sequence_no=11,
        role=ConversationRole.USER,
        content="continue after exceptions",
    )
    store = _Store()

    async def completion(**kwargs):
        return {
            "content": json.dumps(
                {
                    "schema": "conversation_checkpoint_navigation.v1",
                    "semantic_navigation": {
                        "authority": "non_authoritative_navigation",
                        "current_objectives": [],
                        "resolved_decisions": [],
                        "superseded_directions": [],
                        "unresolved_questions": [],
                        "next_context_needed": [],
                    },
                    "author_quote_positions": [],
                    "prior_author_quote_states": [],
                }
            ),
            "tool_calls": None,
        }

    prepared = asyncio.run(
        prepare_conversation_context(
            store=store,
            orchestrator=_Orchestrator(known=True, window=10_000),
            conversation=ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=11,
                project_id="project",
            ),
            owner_id="project",
            turns=turns,
            current_user_message=current,
            model="openai:test",
            task_type="assistant",
            protocol="chat_completions",
            system_prompt="system",
            current_tools=(),
            reload_turns=lambda: turns,
            checkpoint_completion=completion,
        )
    )

    ranges = [
        (segment.source_range.first_sequence, segment.source_range.last_sequence)
        for segment in prepared.frame.checkpoint_segments
    ]
    assert len(ranges) >= 2
    assert (1, 2) in ranges
    assert (5, 6) in ranges
    rendered = prepared.provider_messages
    contents = [str(message.get("content") or "") for message in rendered]
    for turn_index, status in ((2, "cancelled"), (4, "error")):
        assert turns[turn_index - 1].messages[0].content in contents
        assert turns[turn_index - 1].messages[1].content in contents
        assert any(f'"status":"{status}"' in content for content in contents)
    positions = {content: index for index, content in enumerate(contents)}
    assert positions[turns[1].messages[1].content] < next(
        index for index, content in enumerate(contents) if '"status":"cancelled"' in content
    )
    assert positions[turns[3].messages[1].content] < next(
        index for index, content in enumerate(contents) if '"status":"error"' in content
    )
