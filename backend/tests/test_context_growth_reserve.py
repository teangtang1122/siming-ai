"""Future result estimates must not prevent delivery of a rejected batch."""

import json
from dataclasses import replace

import pytest

from app.architecture.tool_definition import ToolDef
from app.architecture.tool_result_policy import ModelResultContract
from app.services.conversation_context import (
    NativeToolCall,
    NativeToolResult,
    ToolTransaction,
    Utf8ByteTokenCounter,
)
from app.services.conversation_context.assembly import assemble_context_step
from app.services.conversation_context.budget import RequestTokenComponents, build_request_budget
from app.services.conversation_context.canonical import canonical_sha256
from app.services.conversation_context.contracts import (
    ConversationIdentity,
    ConversationKind,
    ConversationMessage,
    ConversationRole,
    TurnStatus,
)
from app.services.conversation_context.protocol_validator import ModelToolCapability
from app.services.conversation_context.recent_turns import (
    MandatoryExactTurnsOverCapacity,
    select_recent_turns,
)
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import (
    ToolResultBatchOverCapacity,
    admit_native_assistant_transaction,
)
from tests.test_conversation_context_runtime import _binding, _turn


def test_creation_64k_confirmation_fits_after_tool_step_output_is_bounded():
    # Captured from a failed 64K creation turn after two successful reads.
    binding = replace(_binding(), context_window_tokens=64_000, max_output_tokens=16_384)
    components = RequestTokenComponents(
        system_prompt_tokens=4_275,
        tool_schema_tokens=24_780,
        message_wrapper_tokens=2_921,
        checkpoint_tokens=7_622,
        current_user_tokens=60,
        pending_tool_transaction_tokens=6_945,
    )
    tool = registry.get("confirm_creation_artifact")
    assert tool is not None
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [NativeToolCall(
            "confirm-constraints",
            "confirm_creation_artifact",
            json.dumps({"session_id": "session", "artifact": "constraints", "expected_revision": 20}),
        ).to_provider_dict()],
    }
    old_budget = build_request_budget(
        binding=binding,
        counter=Utf8ByteTokenCounter(),
        components=components,
        output_reserve_tokens=16_384,
        safety_margin_tokens=512,
    )
    with pytest.raises(ToolResultBatchOverCapacity):
        admit_native_assistant_transaction(assistant, [tool], request_budget=old_budget)
    retry_components = replace(components, recent_exact_turn_tokens=512)
    bounded_budget = build_request_budget(
        binding=binding,
        counter=Utf8ByteTokenCounter(),
        components=retry_components,
        output_reserve_tokens=4_096,
        safety_margin_tokens=512,
    )
    assert bounded_budget.current_input_tokens == 47_115
    assert bounded_budget.tool_transaction_budget_tokens == 12_277
    assert admit_native_assistant_transaction(
        assistant, [tool], request_budget=bounded_budget,
    ) <= bounded_budget.tool_transaction_budget_tokens


def test_rejected_batch_remains_sendable_when_future_largest_result_would_not_fit():
    transaction = (
        ToolTransaction(
            transaction_id="tx",
            assistant_message_id="assistant",
            assistant_content="",
            assistant_reasoning_content="保留完整思考状态" * 200,
            calls=(NativeToolCall("call", "read", "{}"),),
        )
        .add_result(
            NativeToolResult(
                "call",
                json.dumps({"status": "error", "retryable": True}),
                persisted_step_id="step",
                result_ref="assistant_run_step:step",
            )
        )
        .mark_delivered()
    )
    options = dict(
        conversation=ConversationIdentity(
            kind=ConversationKind.WORKSPACE, id="conversation", revision=1, project_id="project"
        ),
        turns=(),
        current_user_message=ConversationMessage(
            message_id="user", sequence_no=1, role=ConversationRole.USER, content="写当前章"
        ),
        model_binding=replace(_binding(), tool_schema_hash=canonical_sha256([])),
        token_counter=Utf8ByteTokenCounter(),
        system_prompt="system",
        current_tools=(),
        model_capability=ModelToolCapability(supports_native_tool_calling=True),
        safety_margin_tokens=512,
        delivered_transactions=(transaction,),
    )
    baseline = assemble_context_step(**options)
    binding = replace(
        options["model_binding"],
        context_window_tokens=baseline.budget.current_input_tokens + 1024 + 512 + 1500,
    )
    prepared = assemble_context_step(
        **{
            **options,
            "model_binding": binding,
            "max_model_visible_result_tokens_for_open_tools": 32768,
            "next_step_wrapper": 1024,
        }
    )
    assert prepared.budget.fits_current
    assert not prepared.budget.fits_projected
    assert prepared.budget.tool_transaction_budget_tokens == 1500
    assert prepared.provider_messages[-2:] == list(transaction.native_messages())
    assert prepared.budget.output_reserve_tokens == 1024
    assert prepared.budget.safety_margin_tokens == 512
    # The model can choose a smaller call; the oversized actual batch is still
    # rejected before any handler can run, using the unchanged same budget.
    payload = {
        "role": "assistant",
        "content": "",
        "tool_calls": [NativeToolCall("next", "read", "{}").to_provider_dict()],
    }
    small = ToolDef(
        name="read",
        description="read",
        input_schema={},
        handler=lambda: None,
        model_result_contract=ModelResultContract(max_json_bytes=100),
    )
    assert (
        admit_native_assistant_transaction(payload, [small], request_budget=prepared.budget) < 1500
    )
    large = replace(small, model_result_contract=ModelResultContract(max_json_bytes=32768))
    with pytest.raises(ToolResultBatchOverCapacity):
        admit_native_assistant_transaction(payload, [large], request_budget=prepared.budget)


def test_future_reserve_cannot_displace_mandatory_exact_history():
    turns = (_turn(1), _turn(2, status=TurnStatus.ERROR), _turn(3))
    selected = select_recent_turns(
        turns, available_tokens=50, growth_reserve_tokens=100, count_turn_tokens=lambda _: 20
    )
    assert [turn.turn_id for turn in selected.exact_turns] == ["turn-2"]
    assert [turn.turn_id for turn in selected.checkpoint_turns] == ["turn-1", "turn-3"]
    with pytest.raises(MandatoryExactTurnsOverCapacity):
        select_recent_turns(
            turns, available_tokens=19, growth_reserve_tokens=0, count_turn_tokens=lambda _: 20
        )


def test_growth_reserve_still_compacts_eligible_history_before_it_consumes_headroom():
    selected = select_recent_turns(
        (_turn(1), _turn(2), _turn(3)),
        available_tokens=70,
        growth_reserve_tokens=35,
        count_turn_tokens=lambda _: 20,
    )
    assert [turn.turn_id for turn in selected.exact_turns] == ["turn-3"]
