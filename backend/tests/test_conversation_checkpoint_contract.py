"""Checkpoint provenance, quote and server-ledger validation tests."""

import pytest

from app.services.conversation_context import (
    AuthorQuote,
    CheckpointSourceMessage,
    ConversationCheckpoint,
    ConversationContextError,
    ConversationContextErrorCode,
    ConversationKind,
    ExecutionLedgerEntry,
    ProjectReference,
    ResourceReference,
    SemanticNavigation,
    SourceRange,
    checkpoint_source_hash,
    render_checkpoint_reference,
    validate_checkpoint,
)
from app.services.conversation_context.canonical import text_sha256
from app.services.conversation_context.contracts import ConversationRole


def _sources() -> tuple[CheckpointSourceMessage, ...]:
    return (
        CheckpointSourceMessage(
            message_id="user-1",
            sequence_no=1,
            role=ConversationRole.USER,
            content="不要改变主角姓名，先使用方案 B。",
        ),
        CheckpointSourceMessage(
            message_id="assistant-1",
            sequence_no=2,
            role=ConversationRole.ASSISTANT,
            content="已记录，方案 A 被替换为方案 B。",
        ),
    )


def _ledger() -> ExecutionLedgerEntry:
    return ExecutionLedgerEntry(
        run_id="run-1",
        step_id="step-1",
        tool="create_outline_nodes",
        status="ok",
        resource_refs=(ResourceReference("outline", "outline-151", 3),),
    )


def _checkpoint() -> ConversationCheckpoint:
    sources = _sources()
    quote = "不要改变主角姓名"
    return ConversationCheckpoint(
        scope=ConversationKind.WORKSPACE,
        conversation_id="conversation-1",
        source_range=SourceRange(
            first_sequence=1,
            last_sequence=2,
            message_count=2,
            source_hash=checkpoint_source_hash(sources),
        ),
        semantic_navigation=SemanticNavigation(
            current_objectives=("继续方案 B",),
            superseded_directions=("方案 A 已被作者替换",),
        ),
        author_quotes=(
            AuthorQuote(
                message_id="user-1",
                start_char=0,
                end_char=len(quote),
                exact_quote=quote,
                quote_sha256=text_sha256(quote),
                purpose="active_constraint",
            ),
        ),
        execution_ledger=(_ledger(),),
        project_refs=(
            ProjectReference(
                type="outline",
                id="outline-151",
                reason="曾在会话中操作；使用前重新读取",
            ),
        ),
    )


def test_checkpoint_validates_exact_quote_and_server_execution_receipt() -> None:
    checkpoint = _checkpoint()
    validate_checkpoint(
        checkpoint,
        source_messages=_sources(),
        expected_scope=ConversationKind.WORKSPACE,
        expected_conversation_id="conversation-1",
        trusted_execution_ledger={"step-1": _ledger()},
    )

    rendered = render_checkpoint_reference(checkpoint)
    assert "authority: mixed_reference_only" in rendered
    assert "non_authoritative_navigation" in rendered
    assert "不要改变主角姓名" in rendered
    assert "create_outline_nodes" in rendered
    assert "arguments" not in rendered
    assert "tool_call_id" not in rendered


def test_checkpoint_rejects_modified_source_hash() -> None:
    changed = list(_sources())
    changed[0] = CheckpointSourceMessage(
        message_id="user-1",
        sequence_no=1,
        role=ConversationRole.USER,
        content="已被事后修改",
    )

    with pytest.raises(ConversationContextError) as caught:
        validate_checkpoint(
            _checkpoint(),
            source_messages=changed,
            expected_scope=ConversationKind.WORKSPACE,
            expected_conversation_id="conversation-1",
            trusted_execution_ledger={"step-1": _ledger()},
        )
    assert caught.value.code is ConversationContextErrorCode.SOURCE_CHANGED


def test_checkpoint_rejects_fabricated_author_quote() -> None:
    checkpoint = _checkpoint()
    bad_quote = AuthorQuote(
        message_id="user-1",
        start_char=0,
        end_char=2,
        exact_quote="虚构",
        quote_sha256=text_sha256("虚构"),
        purpose="active_constraint",
    )
    bad = ConversationCheckpoint(
        scope=checkpoint.scope,
        conversation_id=checkpoint.conversation_id,
        source_range=checkpoint.source_range,
        semantic_navigation=checkpoint.semantic_navigation,
        author_quotes=(bad_quote,),
        execution_ledger=checkpoint.execution_ledger,
        project_refs=checkpoint.project_refs,
    )

    with pytest.raises(ConversationContextError) as caught:
        validate_checkpoint(
            bad,
            source_messages=_sources(),
            expected_scope=ConversationKind.WORKSPACE,
            expected_conversation_id="conversation-1",
            trusted_execution_ledger={"step-1": _ledger()},
        )
    assert caught.value.code is ConversationContextErrorCode.CHECKPOINT_FAILED


def test_checkpoint_rejects_model_fabricated_execution_success() -> None:
    checkpoint = _checkpoint()
    fabricated = ExecutionLedgerEntry(
        run_id="run-1",
        step_id="step-1",
        tool="create_outline_nodes",
        status="ok",
        resource_refs=(ResourceReference("outline", "invented", 1),),
    )
    bad = ConversationCheckpoint(
        scope=checkpoint.scope,
        conversation_id=checkpoint.conversation_id,
        source_range=checkpoint.source_range,
        semantic_navigation=checkpoint.semantic_navigation,
        author_quotes=checkpoint.author_quotes,
        execution_ledger=(fabricated,),
        project_refs=checkpoint.project_refs,
    )

    with pytest.raises(ConversationContextError) as caught:
        validate_checkpoint(
            bad,
            source_messages=_sources(),
            expected_scope=ConversationKind.WORKSPACE,
            expected_conversation_id="conversation-1",
            trusted_execution_ledger={"step-1": _ledger()},
        )
    assert caught.value.code is ConversationContextErrorCode.CHECKPOINT_FAILED
