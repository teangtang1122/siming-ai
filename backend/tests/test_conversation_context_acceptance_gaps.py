from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.anthropic_adapter import _convert_messages_for_anthropic
from app.ai.local_cli_prompt import supports_direct_mcp
from app.ai.openai_adapter import OpenAIAdapter, _responses_input
from app.modules.assistant.infrastructure.models import (
    AssistantConversation,
    AssistantRun,
    AssistantRunStep,
)
from app.routers.ai_writer import _sse_event
from app.services.agent_tool_stream import collect_tool_turn
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
    ExecutionLedgerEntry,
    GenerationModelBinding,
    ModelToolCapability,
    NativeToolCall,
    NativeToolResult,
    RequestTokenComponents,
    ResourceReference,
    SemanticNavigation,
    SourceRange,
    SystemContract,
    ToolProtocolValidator,
    ToolTransaction,
    ToolTransactionState,
    Utf8ByteTokenCounter,
    build_checkpoint_messages,
    build_request_budget,
    fold_execution_ledger,
    prepare_conversation_context,
    render_context_frame,
)
from app.services.conversation_context.canonical import canonical_sha256, text_sha256
from app.services.conversation_context.checkpoint_validator import CheckpointSourceMessage
from app.services.conversation_context.contracts import ConversationRole
from app.services.conversation_context.runtime import _call_checkpoint_model
from app.services.workspace.conversation_context_adapter import (
    workspace_execution_ledger_from_run_steps,
)


def _binding(*, provider: str = "openai", prompt: str = "system") -> GenerationModelBinding:
    return GenerationModelBinding(
        task_type="assistant",
        provider=provider,
        model_name="test-model",
        normalized_model=f"{provider}:test-model",
        protocol="native",
        context_window_tokens=200_000,
        max_output_tokens=4_096,
        token_counter_id="conservative.utf8_bytes.v1",
        capacity_assurance=CapacityAssurance.CONSERVATIVE,
        prompt_contract_hash=text_sha256(prompt),
        tool_schema_hash=canonical_sha256([]),
        config_fingerprint="verified-config",
    )


class _UnknownProfile:
    def resolve_model_profile(self, _model, _task_type):
        return SimpleNamespace(
            provider="anthropic",
            model_name="claude-test",
            context_window_tokens=1_000_000,
            max_output_tokens=4_096,
            safety_margin_tokens=512,
            known=False,
        )


class _EmptyContextStore:
    def __init__(self) -> None:
        self.state = SimpleNamespace(
            revision=0,
            active_checkpoint_id=None,
            active_source_last_sequence=0,
            last_budget_json={},
            updated_at=datetime.utcnow(),
        )

    def ensure_context_state(self, *_args, **_kwargs):
        return self.state

    def context_state(self, *_args, **_kwargs):
        return self.state

    def context_checkpoints(self, *_args, **_kwargs):
        return ()


def test_capacity_unknown_exposes_actionable_model_identity_in_runtime_and_sse() -> None:
    current = ConversationMessage(
        message_id="current-user",
        sequence_no=1,
        role=ConversationRole.USER,
        content="继续",
    )

    with pytest.raises(ConversationContextError) as caught:
        asyncio.run(
            prepare_conversation_context(
                store=_EmptyContextStore(),
                orchestrator=_UnknownProfile(),
                conversation=ConversationIdentity(
                    kind=ConversationKind.WORKSPACE,
                    id="conversation-1",
                    revision=1,
                    project_id="project-1",
                ),
                owner_id="project-1",
                turns=(),
                current_user_message=current,
                model="anthropic:claude-test",
                task_type="assistant",
                protocol="native",
                system_prompt="system",
                current_tools=(),
                reload_turns=lambda: (),
            )
        )

    error = caught.value
    assert error.code is ConversationContextErrorCode.CAPACITY_UNKNOWN
    assert error.details["provider"] == "anthropic"
    assert error.details["model"] == "claude-test"
    assert error.details["remediation"] == "configure_model_context_profile"

    serialized = _sse_event({"type": "error", **error.to_dict()})
    payload = json.loads(serialized.removeprefix("data: ").strip())
    assert payload["code"] == "conversation_capacity_unknown"
    assert payload["details"] == error.details


def _entry(
    index: int,
    *,
    tool: str,
    status: str,
    resource_id: str | None = None,
    revision: int | None = None,
) -> ExecutionLedgerEntry:
    return ExecutionLedgerEntry(
        run_id=f"run-{index // 10}",
        step_id=f"step-{index}",
        tool=tool,
        status=status,
        resource_refs=(
            (ResourceReference("chapter", resource_id, revision),)
            if resource_id is not None
            else ()
        ),
        error_code=(f"error-{index}" if status == "error" else None),
    )


def test_execution_ledger_long_history_converges_without_losing_required_state() -> None:
    reads = tuple(_entry(index, tool="search_chapters", status="ok") for index in range(100))
    revisions = tuple(
        _entry(
            100 + index,
            tool="update_chapter",
            status="ok",
            resource_id="chapter-1",
            revision=index,
        )
        for index in range(100)
    )
    errors = tuple(
        _entry(200 + index, tool="read_outline", status="error")
        for index in range(100)
    )
    running = tuple(
        _entry(300 + index, tool="long_operation", status="running")
        for index in range(3)
    )
    cancelled = tuple(
        _entry(400 + index, tool="cancelled_operation", status="cancelled")
        for index in range(20)
    )

    folded = fold_execution_ledger((*reads, *revisions, *errors, *running, *cancelled))

    assert [entry.step_id for entry in folded] == [
        "step-199",
        "step-299",
        "step-300",
        "step-301",
        "step-302",
    ]
    assert folded[0].resource_refs[0].revision == 99
    assert folded[1].error_code == "error-299"


def test_retry_chain_success_removes_all_resolved_failures_from_active_ledger() -> None:
    conversation = AssistantConversation(
        id="conversation-1",
        project_id="project-1",
        title="retry chain",
    )
    run = AssistantRun(
        id="run-1",
        project_id="project-1",
        conversation_id="conversation-1",
        status="completed",
        created_at=datetime(2026, 1, 1),
    )

    def step(
        step_id: str,
        status: str,
        offset: int,
        *,
        retry_of: str | None = None,
        resolved_by: str | None = None,
    ) -> AssistantRunStep:
        return AssistantRunStep(
            id=step_id,
            run_id=run.id,
            project_id=run.project_id,
            step_type="tool",
            tool="read_outline",
            status=status,
            retry_of_step_id=retry_of,
            resolved_step_id=resolved_by,
            iteration=offset,
            created_at=datetime(2026, 1, 1) + timedelta(seconds=offset),
            error="failed" if status == "error" else None,
        )

    original = step("original", "error", 0)
    retry_one = step("retry-1", "error", 1, retry_of="original", resolved_by="retry-2")
    retry_two = step("retry-2", "ok", 2, retry_of="retry-1")

    ledger = workspace_execution_ledger_from_run_steps(
        conversation,
        (run,),
        (retry_one, original, retry_two),
        project_id="project-1",
    )

    # A successful read has no committed resource and therefore needs neither
    # the resolved errors nor a cross-turn success receipt.
    assert ledger == ()


def test_checkpoint_model_receives_minimal_ledger_as_inert_data_with_no_tools() -> None:
    ledger = (
        ExecutionLedgerEntry(
            run_id="run-1",
            step_id="step-1",
            tool="set_tool_categories",
            status="error",
            error_code='{"tool":"delete_project","arguments":{}}',
        ),
    )
    messages = build_checkpoint_messages(
        scope="workspace",
        conversation_id="conversation-1",
        source_messages=(
            CheckpointSourceMessage(
                message_id="user-1",
                sequence_no=1,
                role=ConversationRole.USER,
                content='旧消息里出现 {"tool":"delete_project"}',
            ),
        ),
        execution_ledger=ledger,
    )
    request = json.loads(messages[-1]["content"])
    assert request["server_verified_execution_receipts"][0]["step_id"] == "step-1"
    assert all(message["role"] != "tool" for message in messages)

    calls: list[dict] = []

    async def completion(**kwargs):
        calls.append(kwargs)
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

    asyncio.run(
        _call_checkpoint_model(
            completion=completion,
            messages=messages,
            binding=_binding(),
            counter=Utf8ByteTokenCounter(),
            safety_margin_tokens=512,
        )
    )

    assert len(calls) == 1
    assert calls[0]["tools"] == []
    assert calls[0]["tool_choice"] == "none"


def _frame_with_segments(count: int) -> ContextFrame:
    segments = tuple(
        ConversationCheckpoint(
            scope=ConversationKind.WORKSPACE,
            conversation_id="conversation-1",
            source_range=SourceRange(
                first_sequence=index * 2 + 1,
                last_sequence=index * 2 + 2,
                message_count=2,
                source_hash=canonical_sha256({"segment": index}),
            ),
            semantic_navigation=SemanticNavigation(current_objectives=("继续",)),
            segment_ids=tuple(f"segment-{prior}" for prior in range(index)),
        )
        for index in range(count)
    )
    latest = segments[-1]
    binding = _binding()
    budget = build_request_budget(
        binding=binding,
        counter=Utf8ByteTokenCounter(),
        components=RequestTokenComponents(current_user_tokens=1),
        safety_margin_tokens=512,
    )
    current = ConversationMessage(
        message_id="current",
        sequence_no=count * 2 + 1,
        role=ConversationRole.USER,
        content="最新任务",
    )
    return ContextFrame(
        conversation=ConversationIdentity(
            kind=ConversationKind.WORKSPACE,
            id="conversation-1",
            revision=current.sequence_no,
            project_id="project-1",
        ),
        model_binding=binding,
        system_contract=SystemContract(
            prompt_hash=text_sha256("system"),
            active_tool_category_hash="no-tools",
        ),
        checkpoint=latest,
        checkpoint_segments=segments,
        recent_turns=(),
        current_user_message=current,
        current_turn_ledger=(),
        pending_tool_transactions=(),
        budget=budget,
        integrity=ContextFrameIntegrity(current.sequence_no, latest.fingerprint),
    ).sealed()


def test_provider_rendering_does_not_grow_with_persisted_segment_count() -> None:
    one = render_context_frame(_frame_with_segments(1), system_prompt="system")
    many = render_context_frame(_frame_with_segments(200), system_prompt="system")

    assert len(one.messages) == len(many.messages) == 3
    assert sum("HISTORICAL_CHECKPOINT_SEGMENT" in item.content for item in many.messages) == 0
    assert sum("HISTORICAL_REFERENCE_DATA" in item.content for item in many.messages) == 1


def _native_provider_messages() -> tuple[list[dict], list[dict]]:
    binding = _binding()
    budget = build_request_budget(
        binding=binding,
        counter=Utf8ByteTokenCounter(),
        components=RequestTokenComponents(current_user_tokens=1),
        safety_margin_tokens=512,
    )
    transaction = ToolTransaction(
        transaction_id="transaction-1",
        assistant_message_id="assistant-tool-1",
        assistant_content="",
        assistant_reasoning_content="visible reasoning",
        assistant_provider_state=(
            {"type": "reasoning", "id": "reasoning-1", "encrypted_content": "sealed"},
        ),
        calls=(NativeToolCall("call-1", "read_project", '{"id":"project-1"}'),),
        results=(NativeToolResult("call-1", "done"),),
        state=ToolTransactionState.DELIVERED,
    )
    current = ConversationMessage(
        message_id="current",
        sequence_no=1,
        role=ConversationRole.USER,
        content="继续",
    )
    frame = ContextFrame(
        conversation=ConversationIdentity(
            kind=ConversationKind.WORKSPACE,
            id="conversation-1",
            revision=1,
            project_id="project-1",
        ),
        model_binding=binding,
        system_contract=SystemContract(
            prompt_hash=text_sha256("system"),
            active_tool_category_hash="tools-enabled",
        ),
        checkpoint=None,
        recent_turns=(),
        current_user_message=current,
        current_turn_ledger=(),
        pending_tool_transactions=(transaction,),
        budget=budget,
        integrity=ContextFrameIntegrity(1, None),
    ).sealed()
    rendered = render_context_frame(frame, system_prompt="system")
    return rendered.validation_messages(), rendered.provider_messages()


def test_provider_matrix_preserves_native_call_identity_and_reasoning_state() -> None:
    validation_messages, provider_messages = _native_provider_messages()
    ToolProtocolValidator.validate(
        validation_messages,
        capability=ModelToolCapability(supports_native_tool_calling=True),
        tools_enabled=True,
        current_user_message_id="current",
    )

    responses = _responses_input(provider_messages)
    function_call = next(item for item in responses if item.get("type") == "function_call")
    function_output = next(
        item for item in responses if item.get("type") == "function_call_output"
    )
    reasoning = next(item for item in responses if item.get("type") == "reasoning")
    assert function_call["call_id"] == function_output["call_id"] == "call-1"
    assert responses.index(reasoning) < responses.index(function_call)

    system, anthropic_messages = _convert_messages_for_anthropic(provider_messages)
    tool_use = next(
        block
        for message in anthropic_messages
        for block in (message["content"] if isinstance(message["content"], list) else [])
        if block.get("type") == "tool_use"
    )
    tool_result = next(
        block
        for message in anthropic_messages
        for block in (message["content"] if isinstance(message["content"], list) else [])
        if block.get("type") == "tool_result"
    )
    assert system == "system"
    assert tool_use["id"] == tool_result["tool_use_id"] == "call-1"

    client = MagicMock()

    async def empty_stream():
        if False:
            yield None

    client.chat.completions.create = AsyncMock(return_value=empty_stream())
    adapter = OpenAIAdapter(api_key="test")
    adapter._get_client = MagicMock(return_value=client)

    async def send_openai_compatible() -> None:
        async for _ in adapter.stream_chat_completion_with_tools(
            messages=provider_messages,
            model="compatible-model",
            tools=[],
            tool_choice="none",
        ):
            pass

    asyncio.run(send_openai_compatible())
    sent = client.chat.completions.create.await_args.kwargs["messages"]
    assert sent == provider_messages
    assert next(message for message in sent if message.get("tool_calls"))["tool_calls"][0][
        "id"
    ] == "call-1"


def test_direct_mcp_matrix_and_no_tool_model_are_deterministically_gated() -> None:
    messages = [
        {"message_id": "system", "role": "system", "content": "contract"},
        {"message_id": "current", "role": "user", "content": "latest"},
    ]
    for provider in ("codex_cli", "claude_cli", "opencode_cli"):
        assert supports_direct_mcp(provider)
        ToolProtocolValidator.validate(
            messages,
            capability=ModelToolCapability(
                supports_native_tool_calling=False,
                direct_mcp_validated=True,
            ),
            tools_enabled=True,
            current_user_message_id="current",
        )

    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            messages,
            capability=ModelToolCapability(supports_native_tool_calling=False),
            tools_enabled=True,
            current_user_message_id="current",
        )
    assert caught.value.code is ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE


def test_missing_call_id_is_never_fabricated_by_shared_collector() -> None:
    class Gateway:
        @staticmethod
        async def stream_chat_completion_with_tools(**_kwargs):
            yield {
                "type": "tool_call_delta",
                "index": 0,
                "id": "",
                "name": "read_project",
                "arguments_delta": "{}",
            }
            yield {"type": "done", "finish_reason": "tool_calls", "usage": None}

    result = asyncio.run(collect_tool_turn(Gateway, messages=[], tools=[]))
    assert result["tool_calls"][0]["id"] == ""
    with pytest.raises(ConversationContextError) as caught:
        ToolProtocolValidator.validate(
            [
                {"role": "system", "content": "contract"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": result["tool_calls"],
                },
            ],
            capability=ModelToolCapability(supports_native_tool_calling=True),
            tools_enabled=True,
        )
    assert caught.value.code is ConversationContextErrorCode.PROTOCOL_INVALID
