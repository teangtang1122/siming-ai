"""Author quote lifecycle and semantic-navigation rollup regression tests."""

import json
from types import SimpleNamespace

import pytest

from app.services.conversation_context import (
    AuthorQuote,
    CapacityAssurance,
    CheckpointNavigationProposal,
    ConversationCheckpoint,
    ConversationContextError,
    ConversationContextErrorCode,
    ConversationKind,
    GenerationModelBinding,
    PriorAuthorQuoteDecision,
    SemanticNavigation,
    SourceRange,
    Utf8ByteTokenCounter,
    build_checkpoint_messages,
    build_checkpoint_repair_messages,
    checkpoint_navigation_json_schema,
    parse_checkpoint_navigation,
    render_checkpoint_reference,
    rollup_author_quotes,
)
from app.services.conversation_context.canonical import text_sha256
from app.services.conversation_context.runtime import (
    _require_prior_quote_rollup_capacity,
)


def _quote(
    message_id: str,
    text: str,
    *,
    superseded: bool = False,
) -> AuthorQuote:
    return AuthorQuote(
        message_id=message_id,
        start_char=0,
        end_char=len(text),
        exact_quote=text,
        quote_sha256=text_sha256(text),
        purpose="active_constraint",
        superseded=superseded,
    )


def _proposal(*decisions: PriorAuthorQuoteDecision) -> CheckpointNavigationProposal:
    return CheckpointNavigationProposal(
        semantic_navigation=SemanticNavigation(
            current_objectives=("按最新要求继续",),
            superseded_directions=("旧约束已被替代",),
        ),
        author_quote_positions=(),
        prior_author_quote_states=tuple(decisions),
    )


def _decision(quote: AuthorQuote, status: str) -> PriorAuthorQuoteDecision:
    return PriorAuthorQuoteDecision(
        message_id=quote.message_id,
        start_char=quote.start_char,
        end_char=quote.end_char,
        quote_sha256=quote.quote_sha256,
        status=status,
    )


def test_model_must_return_exhaustive_structured_prior_quote_states() -> None:
    first = _quote("user-1", "不要改变主角姓名")
    second = _quote("user-2", "采用第三人称")

    with pytest.raises(ConversationContextError) as caught:
        rollup_author_quotes(
            _proposal(_decision(first, "active")),
            previous_author_quotes=(first, second),
            new_author_quotes=(),
        )
    assert caught.value.code is ConversationContextErrorCode.CHECKPOINT_FAILED

    invented = PriorAuthorQuoteDecision(
        message_id="invented",
        start_char=0,
        end_char=2,
        quote_sha256=text_sha256("虚构"),
        status="active",
    )
    with pytest.raises(ConversationContextError):
        rollup_author_quotes(
            _proposal(_decision(first, "active"), _decision(second, "active"), invented),
            previous_author_quotes=(first, second),
            new_author_quotes=(),
        )


def test_server_validates_prior_quote_hash_and_preserves_exact_text() -> None:
    prior = _quote("user-1", "主角仍叫陆糖")
    wrong_hash = PriorAuthorQuoteDecision(
        message_id=prior.message_id,
        start_char=prior.start_char,
        end_char=prior.end_char,
        quote_sha256="0" * 64,
        status="active",
    )

    with pytest.raises(ConversationContextError, match="hash"):
        rollup_author_quotes(
            _proposal(wrong_hash),
            previous_author_quotes=(prior,),
            new_author_quotes=(),
        )

    rolled = rollup_author_quotes(
        _proposal(_decision(prior, "active")),
        previous_author_quotes=(prior,),
        new_author_quotes=(),
    )
    assert rolled == (prior,)


def test_superseded_quote_is_audited_once_but_not_rendered_or_accumulated() -> None:
    prior = _quote("user-1", "使用方案 A")
    newest = _quote("user-3", "改用方案 B")
    first_rollup = rollup_author_quotes(
        _proposal(_decision(prior, "superseded")),
        previous_author_quotes=(prior,),
        new_author_quotes=(newest,),
    )
    assert [quote.superseded for quote in first_rollup] == [True, False]

    checkpoint = ConversationCheckpoint(
        scope=ConversationKind.WORKSPACE,
        conversation_id="conversation-1",
        source_range=SourceRange(3, 4, 2, "source-hash"),
        semantic_navigation=SemanticNavigation(),
        author_quotes=first_rollup,
    )
    rendered = render_checkpoint_reference(checkpoint)
    assert "使用方案 A" not in rendered
    assert "改用方案 B" in rendered

    second_rollup = rollup_author_quotes(
        _proposal(_decision(newest, "active")),
        previous_author_quotes=first_rollup,
        new_author_quotes=(),
    )
    assert second_rollup == (newest,)


def test_checkpoint_prompt_exposes_only_active_prior_quotes_and_requires_decisions() -> None:
    active = _quote("user-1", "继续保留")
    superseded = _quote("user-2", "已经替代", superseded=True)
    messages = build_checkpoint_messages(
        scope="workspace",
        conversation_id="conversation-1",
        source_messages=(),
        previous_navigation=SemanticNavigation(),
        previous_author_quotes=(active, superseded),
    )
    request = json.loads(messages[-1]["content"])

    assert request["previous_active_author_quotes"] == [
        {
            "message_id": active.message_id,
            "start_char": active.start_char,
            "end_char": active.end_char,
            "exact_quote": active.exact_quote,
            "quote_sha256": active.quote_sha256,
            "purpose": active.purpose,
        }
    ]
    assert "prior_author_quote_states" in checkpoint_navigation_json_schema()["schema"][
        "required"
    ]


def test_navigation_parser_accepts_only_structured_prior_identity_and_status() -> None:
    quote = _quote("user-1", "不要改名")
    payload = {
        "schema": "conversation_checkpoint_navigation.v1",
        "semantic_navigation": {
            "authority": "non_authoritative_navigation",
            "current_objectives": ["继续"],
            "resolved_decisions": [],
            "superseded_directions": [],
            "unresolved_questions": [],
            "next_context_needed": [],
        },
        "author_quote_positions": [],
        "prior_author_quote_states": [
            {
                "message_id": quote.message_id,
                "start_char": quote.start_char,
                "end_char": quote.end_char,
                "quote_sha256": quote.quote_sha256,
                "status": "active",
            }
        ],
    }
    proposal = parse_checkpoint_navigation(json.dumps(payload))
    assert proposal.prior_author_quote_states == (_decision(quote, "active"),)


def test_repair_request_does_not_silently_character_truncate_required_state() -> None:
    invalid = "x" * 20_000
    error = "e" * 2_000
    messages = build_checkpoint_repair_messages(
        original_messages=({"role": "system", "content": "contract"},),
        invalid_output=invalid,
        validation_error=error,
    )
    request = json.loads(messages[-1]["content"].split("\n", 1)[1])
    assert request["invalid_output"] == invalid
    assert request["validation_error"] == error


def test_required_prior_quote_state_over_output_budget_fails_explicitly() -> None:
    quotes = tuple(
        _quote(f"author-message-with-a-long-identity-{index}", f"约束 {index}")
        for index in range(4)
    )
    active = SimpleNamespace(
        checkpoint=SimpleNamespace(author_quotes=quotes),
    )
    binding = GenerationModelBinding(
        task_type="assistant",
        provider="openai",
        model_name="test",
        normalized_model="openai:test",
        protocol="chat_completions",
        context_window_tokens=4_096,
        max_output_tokens=32,
        token_counter_id="conservative.utf8_bytes.v1",
        capacity_assurance=CapacityAssurance.CONSERVATIVE,
        prompt_contract_hash="prompt",
        tool_schema_hash="tools",
        config_fingerprint="config",
    )

    with pytest.raises(ConversationContextError) as caught:
        _require_prior_quote_rollup_capacity(
            active=active,
            binding=binding,
            counter=Utf8ByteTokenCounter(),
        )
    assert caught.value.code is ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY
