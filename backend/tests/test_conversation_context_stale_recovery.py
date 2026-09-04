from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.services.conversation_context import (
    AuthorQuote,
    CapacityAssurance,
    ConversationCheckpoint,
    ConversationContextError,
    ConversationContextErrorCode,
    ConversationIdentity,
    ConversationKind,
    ConversationMessage,
    ConversationTurn,
    ExecutionLedgerEntry,
    GenerationModelBinding,
    ResourceReference,
    SemanticNavigation,
    SourceRange,
    Utf8ByteTokenCounter,
    assemble_context_step,
)
from app.services.conversation_context.canonical import canonical_sha256, text_sha256
from app.services.conversation_context.contracts import ConversationRole, TurnStatus
from app.services.conversation_context.runtime import (
    _publish_or_resolve_checkpoint_race,
    checkpoint_record_payload,
    context_state_payload,
    prepare_conversation_context,
)


def _turn(index: int, *, size: int = 900, user_text: str | None = None) -> ConversationTurn:
    first = (index - 1) * 2 + 1
    return ConversationTurn(
        turn_id=f"turn-{index}",
        status=TurnStatus.COMPLETED,
        messages=(
            ConversationMessage(
                message_id=f"user-{index}",
                sequence_no=first,
                role=ConversationRole.USER,
                content=user_text if user_text is not None else f"u{index}-" + "x" * size,
            ),
            ConversationMessage(
                message_id=f"assistant-{index}",
                sequence_no=first + 1,
                role=ConversationRole.ASSISTANT,
                content=f"a{index}-" + "y" * size,
            ),
        ),
    )


class _Orchestrator:
    def resolve_model_profile(self, model, task_type):
        del model, task_type
        return SimpleNamespace(
            provider="openai",
            model_name="test",
            context_window_tokens=8_000,
            max_output_tokens=1_024,
            safety_margin_tokens=256,
            known=True,
        )


class _Store:
    def __init__(self) -> None:
        self.state = SimpleNamespace(
            revision=0,
            active_checkpoint_id=None,
            active_source_last_sequence=0,
            last_budget_json={},
            updated_at=datetime.utcnow(),
        )
        self.records: list[SimpleNamespace] = []
        self.sources: dict[str, list[SimpleNamespace]] = {}
        self.commits = 0
        self.invalidate_calls = 0

    def context_state(self, kind, conversation_id, *, owner_id):
        del kind, conversation_id, owner_id
        return self.state

    ensure_context_state = context_state

    def context_checkpoints(self, kind, conversation_id, *, owner_id):
        del kind, conversation_id, owner_id
        return list(self.records)

    def context_checkpoint(self, kind, conversation_id, checkpoint_id, *, owner_id):
        del kind, conversation_id, owner_id
        return next((item for item in self.records if item.id == checkpoint_id), None)

    def context_checkpoint_sources(self, kind, conversation_id, checkpoint_id, *, owner_id):
        del kind, conversation_id, owner_id
        return list(self.sources.get(checkpoint_id, ()))

    def create_context_checkpoint(self, kind, conversation_id, *, owner_id, **values):
        del kind, conversation_id, owner_id
        existing = next(
            (item for item in self.records if item.idempotency_key == values["idempotency_key"]),
            None,
        )
        if existing is not None:
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
        del kind, conversation_id, owner_id
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

    def invalidate_active_context_checkpoint(
        self,
        kind,
        conversation_id,
        checkpoint_id,
        expected_revision,
        *,
        owner_id,
        error_code,
        error_detail,
    ):
        self.invalidate_calls += 1
        record = self.context_checkpoint(kind, conversation_id, checkpoint_id, owner_id=owner_id)
        if (
            record is None
            or self.state.revision != expected_revision
            or self.state.active_checkpoint_id != checkpoint_id
        ):
            return False
        record.status = "superseded"
        record.error_code = error_code
        record.error_detail = error_detail
        record.completed_at = datetime.utcnow()
        self.state.active_checkpoint_id = None
        self.state.active_source_last_sequence = 0
        self.state.revision += 1
        return True

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
        del last_budget_json
        record = self.context_checkpoint(kind, conversation_id, checkpoint_id, owner_id=owner_id)
        if record is None or record.status != "ready" or self.state.revision != expected_revision:
            return False
        previous = self.context_checkpoint(
            kind,
            conversation_id,
            self.state.active_checkpoint_id,
            owner_id=owner_id,
        )
        if previous is not None and previous.id != checkpoint_id:
            previous.status = "superseded"
        self.state.active_checkpoint_id = checkpoint_id
        self.state.active_source_last_sequence = record.source_last_sequence
        self.state.revision += 1
        return True

    def supersede_inactive_context_checkpoint(
        self,
        kind,
        conversation_id,
        checkpoint_id,
        expected_revision,
        *,
        owner_id,
        error_code,
        error_detail,
    ):
        record = self.context_checkpoint(kind, conversation_id, checkpoint_id, owner_id=owner_id)
        if (
            record is None
            or record.status != "ready"
            or self.state.revision != expected_revision
            or self.state.active_checkpoint_id == checkpoint_id
        ):
            return False
        self.state.revision += 1
        record.status = "superseded"
        record.error_code = error_code
        record.error_detail = error_detail
        return True

    def commit_context_phase(self):
        self.commits += 1

    def refresh_context_phase(self):
        return None


def _checkpoint_completion(captured: list[dict]):
    async def completion(**kwargs):
        captured.append(kwargs)
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

    return completion


def _prepare(
    *,
    store: _Store,
    turns: tuple[ConversationTurn, ...],
    current: ConversationMessage,
    captured: list[dict],
    ledger: tuple[ExecutionLedgerEntry, ...] = (),
    source_hashes: dict[str, str] | None = None,
):
    return asyncio.run(
        prepare_conversation_context(
            store=store,
            orchestrator=_Orchestrator(),
            conversation=ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=current.sequence_no,
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
            trusted_execution_ledger=ledger,
            execution_source_hashes=source_hashes,
            checkpoint_completion=_checkpoint_completion(captured),
        )
    )


def _seed_checkpoint(
    *,
    ledger: tuple[ExecutionLedgerEntry, ...] = (),
    source_hashes: dict[str, str] | None = None,
) -> tuple[_Store, tuple[ConversationTurn, ...], ConversationMessage]:
    store = _Store()
    turns = tuple(_turn(index) for index in range(1, 9))
    current = ConversationMessage(
        message_id="current-1",
        sequence_no=17,
        role=ConversationRole.USER,
        content="第一条当前任务",
    )
    prepared = _prepare(
        store=store,
        turns=turns,
        current=current,
        captured=[],
        ledger=ledger,
        source_hashes=source_hashes,
    )
    assert prepared.checkpoint is not None
    return store, turns, current


def _next_request(
    turns: tuple[ConversationTurn, ...],
    prior_current: ConversationMessage,
) -> tuple[tuple[ConversationTurn, ...], ConversationMessage]:
    closed_prior = ConversationTurn(
        turn_id="turn-9",
        status=TurnStatus.COMPLETED,
        messages=(
            prior_current,
            ConversationMessage(
                message_id="assistant-9",
                sequence_no=18,
                role=ConversationRole.ASSISTANT,
                content="第一条任务已完成",
            ),
        ),
    )
    current = ConversationMessage(
        message_id="current-2",
        sequence_no=19,
        role=ConversationRole.USER,
        content="第二条最新任务",
    )
    return (*turns, closed_prior), current


def test_policy_mismatch_is_superseded_then_rebuilt_from_full_transcript() -> None:
    store, turns, prior_current = _seed_checkpoint()
    stale_id = store.state.active_checkpoint_id
    stale = store.context_checkpoint("workspace", "conversation", stale_id, owner_id="project")
    stale.policy_version = 999
    next_turns, current = _next_request(turns, prior_current)
    captured: list[dict] = []

    prepared = _prepare(
        store=store,
        turns=next_turns,
        current=current,
        captured=captured,
    )

    assert stale.status == "superseded"
    assert stale.error_code == ConversationContextErrorCode.SOURCE_CHANGED.value
    assert store.state.active_checkpoint_id != stale_id
    assert store.invalidate_calls == 1
    source_request = json.loads(captured[0]["messages"][1]["content"])
    assert source_request["new_source_messages"][0]["message_id"] == "user-1"
    assert prepared.provider_messages[-1]["content"] == "第二条最新任务"


def test_changed_source_hash_is_superseded_then_rebuilt() -> None:
    store, turns, prior_current = _seed_checkpoint()
    stale_id = store.state.active_checkpoint_id
    changed = (_turn(1, user_text="合法修改后的第一条消息"), *turns[1:])
    next_turns, current = _next_request(changed, prior_current)
    captured: list[dict] = []

    _prepare(store=store, turns=next_turns, current=current, captured=captured)

    stale = store.context_checkpoint("workspace", "conversation", stale_id, owner_id="project")
    assert stale.status == "superseded"
    source_request = json.loads(captured[0]["messages"][1]["content"])
    assert source_request["new_source_messages"][0]["content"] == "合法修改后的第一条消息"


def test_source_turn_status_change_invalidates_checkpoint_and_stays_exact() -> None:
    store, turns, prior_current = _seed_checkpoint()
    stale_id = store.state.active_checkpoint_id
    changed_first = ConversationTurn(
        turn_id=turns[0].turn_id,
        status=TurnStatus.ERROR,
        messages=turns[0].messages,
    )
    next_turns, current = _next_request((changed_first, *turns[1:]), prior_current)

    prepared = _prepare(
        store=store,
        turns=next_turns,
        current=current,
        captured=[],
    )

    stale = store.context_checkpoint("workspace", "conversation", stale_id, owner_id="project")
    assert stale.status == "superseded"
    assert stale.error_code == ConversationContextErrorCode.SOURCE_CHANGED.value
    contents = [str(message.get("content") or "") for message in prepared.provider_messages]
    assert changed_first.messages[0].content in contents
    assert changed_first.messages[1].content in contents
    assert any('"status":"error"' in content for content in contents)


def test_run_step_retry_hash_is_superseded_and_uses_a_new_attempt_key() -> None:
    ledger = (
        ExecutionLedgerEntry(
            run_id="run-1",
            step_id="step-1",
            tool="update_chapter",
            status="ok",
            resource_refs=(ResourceReference("chapter", "chapter-1", 1),),
        ),
    )
    store, turns, prior_current = _seed_checkpoint(
        ledger=ledger,
        source_hashes={"step-1": "a" * 64},
    )
    stale_id = store.state.active_checkpoint_id
    next_turns, current = _next_request(turns, prior_current)

    _prepare(
        store=store,
        turns=next_turns,
        current=current,
        captured=[],
        ledger=ledger,
        source_hashes={"step-1": "b" * 64},
    )

    stale = store.context_checkpoint("workspace", "conversation", stale_id, owner_id="project")
    assert stale.status == "superseded"
    assert store.state.active_checkpoint_id != stale_id
    active_sources = store.sources[store.state.active_checkpoint_id]
    run_step = next(source for source in active_sources if source.source_kind == "run_step")
    assert run_step.source_hash == "b" * 64


def test_resolved_retry_fold_still_invalidates_old_run_step_provenance() -> None:
    original = ExecutionLedgerEntry(
        run_id="run-1",
        step_id="step-original",
        tool="update_chapter",
        status="error",
        error_code="provider_timeout",
    )
    store, turns, prior_current = _seed_checkpoint(
        ledger=(original,),
        source_hashes={"step-original": "a" * 64},
    )
    stale_id = store.state.active_checkpoint_id
    resolved = ExecutionLedgerEntry(
        run_id="run-1",
        step_id="step-success",
        tool="update_chapter",
        status="ok",
        resource_refs=(ResourceReference("chapter", "chapter-1", 2),),
    )
    next_turns, current = _next_request(turns, prior_current)

    _prepare(
        store=store,
        turns=next_turns,
        current=current,
        captured=[],
        ledger=(resolved,),
        source_hashes={
            # The durable original remains auditable, but its resolution
            # fields changed and therefore its provenance hash must stale the
            # checkpoint before trusted-ledger folding removes it.
            "step-original": "b" * 64,
            "step-success": "c" * 64,
        },
    )

    stale = store.context_checkpoint(
        "workspace", "conversation", stale_id, owner_id="project"
    )
    assert stale.status == "superseded"
    assert stale.error_code == ConversationContextErrorCode.SOURCE_CHANGED.value
    active_sources = store.sources[store.state.active_checkpoint_id]
    assert {
        source.source_id: source.source_hash
        for source in active_sources
        if source.source_kind == "run_step"
    } == {"step-success": "c" * 64}


def test_checkpoint_failed_is_not_invalidated_or_hidden() -> None:
    store, turns, prior_current = _seed_checkpoint()
    active_id = store.state.active_checkpoint_id
    active = store.context_checkpoint("workspace", "conversation", active_id, owner_id="project")
    active.validation_json = {"schema": "corrupt"}
    next_turns, current = _next_request(turns, prior_current)

    with pytest.raises(ConversationContextError) as caught:
        _prepare(store=store, turns=next_turns, current=current, captured=[])

    assert caught.value.code is ConversationContextErrorCode.CHECKPOINT_FAILED
    assert store.invalidate_calls == 0
    assert store.state.active_checkpoint_id == active_id
    assert active.status == "ready"


def test_owner_failure_is_not_treated_as_a_stale_checkpoint() -> None:
    class ForeignStore(_Store):
        def context_state(self, kind, conversation_id, *, owner_id):
            del kind, conversation_id, owner_id
            return None

        ensure_context_state = context_state

    store = ForeignStore()
    current = ConversationMessage(
        message_id="current",
        sequence_no=1,
        role=ConversationRole.USER,
        content="不能读取别人的会话",
    )

    with pytest.raises(ConversationContextError) as caught:
        _prepare(store=store, turns=(), current=current, captured=[])

    assert caught.value.code is ConversationContextErrorCode.SOURCE_CHANGED
    assert store.invalidate_calls == 0


def test_active_checkpoint_over_capacity_is_required_state_error() -> None:
    exact_constraint = "必须逐字保留" + "甲" * 3_000
    checkpoint = ConversationCheckpoint(
        scope=ConversationKind.WORKSPACE,
        conversation_id="conversation",
        source_range=SourceRange(
            first_sequence=1,
            last_sequence=2,
            message_count=2,
            source_hash="a" * 64,
        ),
        semantic_navigation=SemanticNavigation(),
        author_quotes=(
            AuthorQuote(
                message_id="user-1",
                start_char=0,
                end_char=len(exact_constraint),
                exact_quote=exact_constraint,
                quote_sha256=text_sha256(exact_constraint),
                purpose="active_constraint",
            ),
        ),
    )
    binding = GenerationModelBinding(
        task_type="assistant",
        provider="openai",
        model_name="test",
        normalized_model="openai:test",
        protocol="chat_completions",
        context_window_tokens=4_000,
        max_output_tokens=512,
        token_counter_id="conservative.utf8_bytes.v1",
        capacity_assurance=CapacityAssurance.CONSERVATIVE,
        prompt_contract_hash=text_sha256("system"),
        tool_schema_hash=canonical_sha256([]),
        config_fingerprint="config",
    )
    current = ConversationMessage(
        message_id="current",
        sequence_no=3,
        role=ConversationRole.USER,
        content="继续",
    )

    with pytest.raises(ConversationContextError) as caught:
        assemble_context_step(
            conversation=ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=3,
                project_id="project",
            ),
            turns=(),
            current_user_message=current,
            model_binding=binding,
            token_counter=Utf8ByteTokenCounter(),
            system_prompt="system",
            current_tools=(),
            safety_margin_tokens=128,
            active_checkpoint=checkpoint,
            checkpoint_segments=(checkpoint,),
        )

    assert caught.value.code is ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY
    assert caught.value.details["required_state_tokens"] > binding.context_window_tokens


def test_publish_race_retires_ready_inactive_loser() -> None:
    store = _Store()
    loser = store.create_context_checkpoint(
        "workspace",
        "conversation",
        owner_id="project",
        idempotency_key="loser",
        status="ready",
        source_first_sequence=1,
        source_last_sequence=2,
        source_message_count=2,
        source_hash="a" * 64,
        transcript_revision=2,
        model_binding_json={},
    )
    winner = store.create_context_checkpoint(
        "workspace",
        "conversation",
        owner_id="project",
        idempotency_key="winner",
        status="ready",
        source_first_sequence=3,
        source_last_sequence=4,
        source_message_count=2,
        source_hash="b" * 64,
        transcript_revision=4,
        model_binding_json={},
    )
    store.state.active_checkpoint_id = winner.id
    store.state.active_source_last_sequence = 4
    store.state.revision = 1

    with pytest.raises(ConversationContextError) as caught:
        _publish_or_resolve_checkpoint_race(
            store=store,
            conversation=ConversationIdentity(
                kind=ConversationKind.WORKSPACE,
                id="conversation",
                revision=5,
                project_id="project",
            ),
            owner_id="project",
            checkpoint_id=loser.id,
            expected_revision=0,
        )

    assert caught.value.code is ConversationContextErrorCode.CHECKPOINT_SUPERSEDED
    assert loser.status == "superseded"
    assert winner.status == "ready"
    assert store.state.active_checkpoint_id == winner.id
    assert store.state.revision == 2


def test_publish_race_accepts_same_idempotent_checkpoint_published_elsewhere() -> None:
    store = _Store()
    checkpoint = store.create_context_checkpoint(
        "workspace",
        "conversation",
        owner_id="project",
        idempotency_key="shared",
        status="ready",
        source_first_sequence=1,
        source_last_sequence=2,
        source_message_count=2,
        source_hash="a" * 64,
        transcript_revision=2,
        model_binding_json={},
    )
    store.state.active_checkpoint_id = checkpoint.id
    store.state.active_source_last_sequence = 2
    store.state.revision = 1

    _publish_or_resolve_checkpoint_race(
        store=store,
        conversation=ConversationIdentity(
            kind=ConversationKind.WORKSPACE,
            id="conversation",
            revision=3,
            project_id="project",
        ),
        owner_id="project",
        checkpoint_id=checkpoint.id,
        expected_revision=0,
    )

    assert checkpoint.status == "ready"
    assert store.state.active_checkpoint_id == checkpoint.id
    assert store.state.revision == 1


def test_observer_failure_does_not_strand_or_fail_checkpoint() -> None:
    store = _Store()
    turns = tuple(_turn(index) for index in range(1, 9))
    current = ConversationMessage(
        message_id="current",
        sequence_no=17,
        role=ConversationRole.USER,
        content="继续",
    )

    async def run():
        return await prepare_conversation_context(
            store=store,
            orchestrator=_Orchestrator(),
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
            checkpoint_completion=_checkpoint_completion([]),
            event_sink=lambda event, payload: (_ for _ in ()).throw(
                RuntimeError(f"observer failed: {event}: {bool(payload)}")
            ),
        )

    prepared = asyncio.run(run())

    assert prepared.checkpoint is not None
    assert store.records[-1].status == "ready"


def test_provider_failure_never_exposes_private_diagnostics_in_state_or_sse_payload() -> None:
    store = _Store()
    turns = tuple(_turn(index) for index in range(1, 9))
    current = ConversationMessage(
        message_id="current",
        sequence_no=17,
        role=ConversationRole.USER,
        content="继续",
    )
    secret = "api_key=sk-private arguments={chapter:42} reasoning=hidden-chain"

    async def failed_completion(**kwargs):
        del kwargs
        raise RuntimeError(secret)

    with pytest.raises(ConversationContextError) as caught:
        asyncio.run(
            prepare_conversation_context(
                store=store,
                orchestrator=_Orchestrator(),
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
                checkpoint_completion=failed_completion,
            )
        )

    record = store.records[-1]
    detail = checkpoint_record_payload(
        store=store,
        conversation_kind="workspace",
        conversation_id="conversation",
        owner_id="project",
        checkpoint_id=record.id,
    )
    state = context_state_payload(
        store=store,
        conversation_kind="workspace",
        conversation_id="conversation",
        owner_id="project",
        error=caught.value,
    )
    visible = json.dumps(
        {"exception": str(caught.value), "checkpoint": detail, "state": state},
        ensure_ascii=False,
    )
    assert caught.value.code is ConversationContextErrorCode.CHECKPOINT_FAILED
    assert record.status == "failed"
    assert secret not in visible
    assert "sk-private" not in str(record.error_detail)


@pytest.mark.parametrize(
    ("unsafe_change", "expected_status"),
    [("status", "error"), ("native_tool_protocol", "completed")],
)
def test_source_turn_becoming_ineligible_during_generation_fails_without_publish(
    unsafe_change: str,
    expected_status: str,
) -> None:
    store = _Store()
    initial_turns = tuple(_turn(index) for index in range(1, 9))
    reloaded_turns = list(initial_turns)
    current = ConversationMessage(
        message_id="current",
        sequence_no=17,
        role=ConversationRole.USER,
        content="继续",
    )

    async def completion_then_change_source(**kwargs):
        result = await _checkpoint_completion([])(**kwargs)
        if unsafe_change == "status":
            reloaded_turns[0] = ConversationTurn(
                turn_id=initial_turns[0].turn_id,
                status=TurnStatus.ERROR,
                messages=initial_turns[0].messages,
            )
        else:
            user, assistant = initial_turns[0].messages
            reloaded_turns[0] = ConversationTurn(
                turn_id=initial_turns[0].turn_id,
                status=TurnStatus.COMPLETED,
                messages=(
                    user,
                    ConversationMessage(
                        message_id=assistant.message_id,
                        sequence_no=assistant.sequence_no,
                        role=ConversationRole.ASSISTANT,
                        content=assistant.content,
                        tool_calls=(
                            {
                                "id": "raw-call",
                                "type": "function",
                                "function": {"name": "delete_project", "arguments": "{}"},
                            },
                        ),
                    ),
                ),
            )
        return result

    with pytest.raises(ConversationContextError) as caught:
        asyncio.run(
            prepare_conversation_context(
                store=store,
                orchestrator=_Orchestrator(),
                conversation=ConversationIdentity(
                    kind=ConversationKind.WORKSPACE,
                    id="conversation",
                    revision=17,
                    project_id="project",
                ),
                owner_id="project",
                turns=initial_turns,
                current_user_message=current,
                model="openai:test",
                task_type="assistant",
                protocol="chat_completions",
                system_prompt="system",
                current_tools=(),
                reload_turns=lambda: tuple(reloaded_turns),
                checkpoint_completion=completion_then_change_source,
            )
        )

    attempt = store.records[-1]
    assert caught.value.code is ConversationContextErrorCode.SOURCE_CHANGED
    assert caught.value.details == {"turn_id": "turn-1", "status": expected_status}
    assert attempt.status == "failed"
    assert attempt.error_code == ConversationContextErrorCode.SOURCE_CHANGED.value
    assert store.state.active_checkpoint_id is None
    assert store.state.revision == 0


def test_async_task_cancellation_closes_compressing_attempt() -> None:
    store = _Store()
    turns = tuple(_turn(index) for index in range(1, 9))
    current = ConversationMessage(
        message_id="current",
        sequence_no=17,
        role=ConversationRole.USER,
        content="继续",
    )

    async def scenario() -> None:
        started = asyncio.Event()

        async def blocked_completion(**kwargs):
            del kwargs
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(
            prepare_conversation_context(
                store=store,
                orchestrator=_Orchestrator(),
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
                checkpoint_completion=blocked_completion,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert store.records[-1].status == "cancelled"
    assert store.records[-1].error_code == ConversationContextErrorCode.CHECKPOINT_CANCELLED.value
