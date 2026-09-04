"""Security boundaries specific to conversation checkpointing."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.ai.local_cli_adapter import LOCAL_CLI_PROVIDERS
from app.database.models import Project
from app.database.session import Base
from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    AssistantRun,
    AssistantRunStep,
    SystemAssistantConversation,
    SystemAssistantMessage,
)
from app.services.conversation_context import (
    CapacityAssurance,
    ConversationContextError,
    ConversationContextErrorCode,
    ConversationKind,
    GenerationModelBinding,
    Utf8ByteTokenCounter,
)
from app.services.conversation_context.canonical import canonical_sha256, text_sha256
from app.services.conversation_context.checkpoint_generation import (
    _call_checkpoint_model,
    _emit_attempt_started,
)
from app.services.conversation_context.checkpoint_state import cancel_checkpoint_attempt
from app.services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace
from app.services.workspace.assistant_public_errors import public_context_failure


def _binding(provider: str) -> GenerationModelBinding:
    return GenerationModelBinding(
        task_type="assistant",
        provider=provider,
        model_name="security-test",
        normalized_model=f"{provider}:security-test",
        protocol="chat_completions",
        context_window_tokens=200_000,
        max_output_tokens=4_096,
        token_counter_id="conservative.utf8_bytes.v1",
        capacity_assurance=CapacityAssurance.CONSERVATIVE,
        prompt_contract_hash=text_sha256("checkpoint system"),
        tool_schema_hash=canonical_sha256([]),
        config_fingerprint="security-test-profile",
    )


def _valid_checkpoint_result() -> dict[str, object]:
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
        )
    }


def test_api_checkpoint_call_has_no_tools_or_task_manifest() -> None:
    calls: list[dict[str, object]] = []

    async def completion(**kwargs):
        calls.append(kwargs)
        return _valid_checkpoint_result()

    asyncio.run(
        _call_checkpoint_model(
            completion=completion,
            messages=[
                {"role": "system", "content": "checkpoint system"},
                {"role": "user", "content": "untrusted history"},
            ],
            binding=_binding("openai"),
            counter=Utf8ByteTokenCounter(),
            safety_margin_tokens=512,
        )
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["tools"] == []
    assert call["tool_choice"] == "none"
    body = call["extra_body"]
    assert isinstance(body, dict)
    assert body["moshu_context_manifest_disabled"] is True
    assert "local_cli_mcp_project_id" not in body
    assert "local_cli_mcp_creation_session_id" not in body


@pytest.mark.parametrize(
    "provider",
    sorted(LOCAL_CLI_PROVIDERS),
)
def test_every_local_agent_cli_checkpoint_is_rejected_before_process_execution(
    provider: str,
) -> None:
    called = False

    async def completion(**_kwargs):
        nonlocal called
        called = True
        return _valid_checkpoint_result()

    with pytest.raises(ConversationContextError) as caught:
        asyncio.run(
            _call_checkpoint_model(
                completion=completion,
                messages=[
                    {"role": "system", "content": "checkpoint system"},
                    {"role": "user", "content": "untrusted history"},
                ],
                binding=_binding(provider),
                counter=Utf8ByteTokenCounter(),
                safety_margin_tokens=512,
            )
        )

    assert called is False
    assert caught.value.code is ConversationContextErrorCode.CHECKPOINT_FAILED
    assert "无工具、无文件、无 MCP 硬隔离" in str(caught.value)
    assert "API 模型" in str(caught.value)
    assert "新建对话" in str(caught.value)
    public = public_context_failure(caught.value)
    assert "API 模型" in public.message
    assert "新建对话" in public.message
    assert "API 模型" in public.details["remediation"]


def test_checkpoint_start_projection_closes_read_transaction_before_observer() -> None:
    class TransactionAwareStore:
        def __init__(self) -> None:
            self.in_transaction = False
            self.commits = 0

        def context_state(self, kind, conversation_id, *, owner_id):
            assert (kind, conversation_id, owner_id) == (
                "workspace",
                "conversation-1",
                "project-1",
            )
            self.in_transaction = True
            return None

        def context_checkpoint(self, kind, conversation_id, checkpoint_id, *, owner_id):
            assert (kind, conversation_id, checkpoint_id, owner_id) == (
                "workspace",
                "conversation-1",
                "checkpoint-1",
                "project-1",
            )
            self.in_transaction = True
            return SimpleNamespace(
                id="checkpoint-1",
                status="compressing",
                policy_version=1,
                schema_version="conversation_checkpoint.v1",
                source_first_sequence=1,
                source_last_sequence=2,
                source_message_count=2,
                source_hash="a" * 64,
            )

        def commit_context_phase(self) -> None:
            self.in_transaction = False
            self.commits += 1

    store = TransactionAwareStore()
    observed: list[str] = []

    async def sink(event: str, _payload: dict[str, object]) -> None:
        # An event observer may itself perform network I/O.  The checkpoint
        # runtime must release projection reads before awaiting it; the model
        # call that follows this helper therefore also starts transaction-free.
        assert store.in_transaction is False
        observed.append(event)

    request = SimpleNamespace(
        store=store,
        conversation=SimpleNamespace(kind=ConversationKind.WORKSPACE, id="conversation-1"),
        owner_id="project-1",
        event_sink=sink,
    )
    asyncio.run(
        _emit_attempt_started(
            request,
            SimpleNamespace(record_id="checkpoint-1"),
        )
    )

    assert observed == ["conversation_context", "conversation_checkpoint"]
    assert store.commits == 1
    assert store.in_transaction is False


def test_creation_checkpoint_store_rejects_cross_session_and_cross_kind_access() -> None:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    try:
        with Session(engine) as db:
            conversation = SystemAssistantConversation(
                id="creation-conversation-1",
                title="Creation",
                scope_type="creation",
                scope_id="creation-session-1",
                creation_session_id="creation-session-1",
            )
            db.add(conversation)
            db.flush()
            store = SqlAlchemyAssistantWorkspace(db)
            checkpoint = store.create_context_checkpoint(
                "creation",
                conversation.id,
                owner_id="creation-session-1",
                id="creation-checkpoint-1",
                idempotency_key="creation-security-attempt",
                source_first_sequence=1,
                source_last_sequence=2,
                source_message_count=2,
                source_hash="a" * 64,
                transcript_revision=2,
                model_binding_json={"provider": "openai", "model": "test"},
            )
            assert checkpoint is not None
            db.commit()

            assert store.context_state(
                "creation", conversation.id, owner_id="creation-session-2"
            ) is None
            assert store.ensure_context_state(
                "creation", conversation.id, owner_id="creation-session-2"
            ) is None
            assert store.context_checkpoint(
                "creation",
                conversation.id,
                checkpoint.id,
                owner_id="creation-session-2",
            ) is None
            assert store.context_checkpoints(
                "creation", conversation.id, owner_id="creation-session-2"
            ) == []
            assert store.context_checkpoint_sources(
                "creation",
                conversation.id,
                checkpoint.id,
                owner_id="creation-session-2",
            ) == []
            assert store.update_context_checkpoint_status(
                "creation",
                conversation.id,
                checkpoint.id,
                "compressing",
                owner_id="creation-session-2",
                expected_statuses=["pending"],
            ) is None
            assert not store.publish_context_checkpoint(
                "creation",
                conversation.id,
                checkpoint.id,
                0,
                owner_id="creation-session-2",
            )
            assert not store.delete_context_checkpoint(
                "creation",
                conversation.id,
                checkpoint.id,
                owner_id="creation-session-2",
            )
            assert store.context_state(
                "workspace", conversation.id, owner_id="creation-session-1"
            ) is None
            with pytest.raises(ConversationContextError) as caught:
                cancel_checkpoint_attempt(
                    store=store,
                    conversation_kind="creation",
                    conversation_id=conversation.id,
                    owner_id="creation-session-2",
                    checkpoint_id=checkpoint.id,
                )
            assert caught.value.code is ConversationContextErrorCode.SOURCE_CHANGED
            db.rollback()
            owned = store.context_checkpoint(
                "creation",
                conversation.id,
                checkpoint.id,
                owner_id="creation-session-1",
            )
            assert owned is not None and owned.status == "pending"
    finally:
        engine.dispose()


def test_creation_checkpoint_source_uses_canonical_turn_id_and_message_owner() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    try:
        with Session(engine) as db:
            owned = SystemAssistantConversation(
                id="creation-conversation-owned",
                title="Owned creation",
                scope_type="creation",
                scope_id="creation-session-1",
                creation_session_id="creation-session-1",
            )
            foreign = SystemAssistantConversation(
                id="creation-conversation-foreign",
                title="Foreign creation",
                scope_type="creation",
                scope_id="creation-session-2",
                creation_session_id="creation-session-2",
            )
            assistant_message_id = "11111111-1111-1111-1111-111111111111"
            foreign_message_id = "22222222-2222-2222-2222-222222222222"
            user_message_id = "33333333-3333-3333-3333-333333333333"
            db.add_all(
                [
                    owned,
                    foreign,
                    SystemAssistantMessage(
                        id=assistant_message_id,
                        conversation_id=owned.id,
                        role="assistant",
                        sequence_no=2,
                        content="sealed receipt",
                        # Receipt-bearing Creation turns do not require a stage
                        # run, so provenance must not depend on this column.
                        run_id=None,
                    ),
                    SystemAssistantMessage(
                        id=user_message_id,
                        conversation_id=owned.id,
                        role="user",
                        sequence_no=1,
                        content="author request",
                    ),
                    SystemAssistantMessage(
                        id=foreign_message_id,
                        conversation_id=foreign.id,
                        role="assistant",
                        sequence_no=1,
                        content="foreign receipt",
                    ),
                ]
            )
            db.flush()
            store = SqlAlchemyAssistantWorkspace(db)
            checkpoint = store.create_context_checkpoint(
                "creation",
                owned.id,
                owner_id="creation-session-1",
                id="creation-checkpoint-source-owner",
                idempotency_key="creation-source-owner-attempt",
                source_first_sequence=1,
                source_last_sequence=2,
                source_message_count=2,
                source_hash="a" * 64,
                transcript_revision=2,
                model_binding_json={"provider": "openai", "model": "test"},
            )
            assert checkpoint is not None

            def add_source(source_id: str):
                return store.add_context_checkpoint_sources(
                    "creation",
                    owned.id,
                    checkpoint.id,
                    [
                        {
                            "source_kind": "run_step",
                            "source_id": source_id,
                            "source_sequence": None,
                            "source_hash": "b" * 64,
                        }
                    ],
                    owner_id="creation-session-1",
                )

            assert add_source(assistant_message_id) is None
            assert add_source(f"creation-turn:{user_message_id}") is None
            assert add_source(f"creation-turn:{foreign_message_id}") is None

            canonical_source_id = f"creation-turn:{assistant_message_id}"
            assert len(canonical_source_id) > 36
            sources = add_source(canonical_source_id)
            assert sources is not None
            assert [(item.source_kind, item.source_id) for item in sources] == [
                ("run_step", canonical_source_id)
            ]
    finally:
        engine.dispose()


def test_workspace_checkpoint_source_rejects_inconsistent_cross_project_run() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    try:
        with Session(engine) as db:
            db.add_all(
                [
                    Project(id="project-1", title="Owned"),
                    Project(id="project-2", title="Foreign"),
                    AssistantConversation(
                        id="conversation-1",
                        project_id="project-1",
                        title="Owned conversation",
                    ),
                    # Simulate a legacy/corrupt row whose conversation points
                    # at project-1 while its explicit run owner is project-2.
                    AssistantRun(
                        id="foreign-run",
                        project_id="project-2",
                        conversation_id="conversation-1",
                    ),
                    AssistantRunStep(
                        id="foreign-step",
                        run_id="foreign-run",
                        project_id="project-2",
                        tool="search_chapters",
                        status="error",
                    ),
                    AssistantRun(
                        id="owned-run",
                        project_id="project-1",
                        conversation_id="conversation-1",
                    ),
                    # A corrupt step owner must not be hidden by an otherwise
                    # valid run/conversation join.
                    AssistantRunStep(
                        id="foreign-step-on-owned-run",
                        run_id="owned-run",
                        project_id="project-2",
                        tool="search_chapters",
                        status="error",
                    ),
                ]
            )
            db.flush()
            store = SqlAlchemyAssistantWorkspace(db)
            checkpoint = store.create_context_checkpoint(
                "workspace",
                "conversation-1",
                owner_id="project-1",
                id="workspace-checkpoint-1",
                idempotency_key="workspace-security-attempt",
                source_first_sequence=1,
                source_last_sequence=2,
                source_message_count=2,
                source_hash="b" * 64,
                transcript_revision=2,
                model_binding_json={"provider": "openai", "model": "test"},
            )
            assert checkpoint is not None

            assert store.add_context_checkpoint_sources(
                "workspace",
                "conversation-1",
                checkpoint.id,
                [
                    {
                        "source_kind": "run_step",
                        "source_id": "foreign-step",
                        "source_sequence": None,
                        "source_hash": "c" * 64,
                    }
                ],
                owner_id="project-1",
            ) is None
            assert store.context_checkpoint_sources(
                "workspace",
                "conversation-1",
                checkpoint.id,
                owner_id="project-1",
            ) == []
            assert store.add_context_checkpoint_sources(
                "workspace",
                "conversation-1",
                checkpoint.id,
                [
                    {
                        "source_kind": "run_step",
                        "source_id": "foreign-step-on-owned-run",
                        "source_sequence": None,
                        "source_hash": "d" * 64,
                    }
                ],
                owner_id="project-1",
            ) is None
            assert store.context_checkpoint_sources(
                "workspace",
                "conversation-1",
                checkpoint.id,
                owner_id="project-1",
            ) == []
    finally:
        engine.dispose()
