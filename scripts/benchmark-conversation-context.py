#!/usr/bin/env python3
"""Repeatable short-conversation ContextFrame preflight microbenchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.conversation_context import (
    CapacityAssurance,
    ConversationIdentity,
    ConversationKind,
    ConversationMessage,
    ConversationTurn,
    GenerationModelBinding,
    ModelToolCapability,
    Utf8ByteTokenCounter,
    assemble_context_step,
    prepare_conversation_context,
)
from app.services.conversation_context.canonical import (
    canonical_sha256,
    text_sha256,
)
from app.services.conversation_context.contracts import (
    ConversationRole,
    TurnStatus,
)

SYSTEM_PROMPT = "You are the workspace assistant. Use only native tools."
TOOLS = (
    {
        "type": "function",
        "function": {
            "name": "set_tool_categories",
            "description": "Select the next tool categories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled_categories": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["enabled_categories"],
            },
        },
    },
)


def _turn(index: int) -> ConversationTurn:
    first = index * 2 - 1
    return ConversationTurn(
        turn_id=f"turn-{index}",
        status=TurnStatus.COMPLETED,
        messages=(
            ConversationMessage(
                message_id=f"user-{index}",
                sequence_no=first,
                role=ConversationRole.USER,
                content=f"User request {index}: continue the current task.",
            ),
            ConversationMessage(
                message_id=f"assistant-{index}",
                sequence_no=first + 1,
                role=ConversationRole.ASSISTANT,
                content=f"Assistant result {index}: completed.",
            ),
        ),
    )


TURNS = tuple(_turn(index) for index in range(1, 5))
CURRENT = ConversationMessage(
    message_id="current-user",
    sequence_no=9,
    role=ConversationRole.USER,
    content="Continue with the latest author request.",
)
CONVERSATION = ConversationIdentity(
    kind=ConversationKind.WORKSPACE,
    id="benchmark-conversation",
    revision=CURRENT.sequence_no,
    project_id="benchmark-project",
)
BINDING = GenerationModelBinding(
    task_type="assistant",
    provider="openai",
    model_name="benchmark-model",
    normalized_model="openai:benchmark-model",
    protocol="chat_completions",
    context_window_tokens=200_000,
    max_output_tokens=4_096,
    token_counter_id="conservative.utf8_bytes.v1",
    capacity_assurance=CapacityAssurance.CONSERVATIVE,
    prompt_contract_hash=text_sha256(SYSTEM_PROMPT),
    tool_schema_hash=canonical_sha256(list(TOOLS)),
    config_fingerprint="benchmark-profile-v1",
)


class _Orchestrator:
    @staticmethod
    def resolve_model_profile(model: str | None, task_type: str) -> SimpleNamespace:
        del model, task_type
        return SimpleNamespace(
            provider="openai",
            model_name="benchmark-model",
            context_window_tokens=200_000,
            max_output_tokens=4_096,
            safety_margin_tokens=1_024,
            known=True,
        )


class _Store:
    def __init__(self) -> None:
        self.state = SimpleNamespace(
            revision=0,
            active_checkpoint_id=None,
            active_source_last_sequence=0,
            last_budget_json={},
            updated_at=datetime.now(UTC),
        )

    def context_state(self, *_args, **_kwargs):
        return self.state

    ensure_context_state = context_state

    @staticmethod
    def context_checkpoints(*_args, **_kwargs):
        return []

    @staticmethod
    def context_checkpoint(*_args, **_kwargs):
        return None

    @staticmethod
    def context_checkpoint_sources(*_args, **_kwargs):
        return []


def _assemble_once() -> None:
    step = assemble_context_step(
        conversation=CONVERSATION,
        turns=TURNS,
        current_user_message=CURRENT,
        model_binding=BINDING,
        token_counter=Utf8ByteTokenCounter(),
        system_prompt=SYSTEM_PROMPT,
        current_tools=TOOLS,
        safety_margin_tokens=1_024,
        model_capability=ModelToolCapability(supports_native_tool_calling=True),
    )
    if step.checkpoint_turns or step.frame.checkpoint is not None:
        raise AssertionError("short conversation unexpectedly required a checkpoint")


async def _verify_short_path() -> int:
    checkpoint_calls = 0

    async def checkpoint_completion(**_kwargs):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        raise AssertionError("short conversation must not call the checkpoint model")

    prepared = await prepare_conversation_context(
        store=_Store(),
        orchestrator=_Orchestrator(),
        conversation=CONVERSATION,
        owner_id="benchmark-project",
        turns=TURNS,
        current_user_message=CURRENT,
        model="openai:benchmark-model",
        task_type="assistant",
        protocol="chat_completions",
        system_prompt=SYSTEM_PROMPT,
        current_tools=TOOLS,
        reload_turns=lambda: TURNS,
        model_capability=ModelToolCapability(supports_native_tool_calling=True),
        checkpoint_completion=checkpoint_completion,
    )
    if prepared.checkpoint is not None or prepared.trigger != "within_capacity":
        raise AssertionError("short conversation did not remain on the exact-history path")
    return checkpoint_calls


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=2_000)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--fail-over-ms", type=float, default=50.0)
    args = parser.parse_args()
    if args.iterations <= 0 or args.warmup < 0:
        parser.error("iterations must be positive and warmup must not be negative")

    checkpoint_calls = asyncio.run(_verify_short_path())
    for _ in range(args.warmup):
        _assemble_once()
    elapsed_ms: list[float] = []
    for _ in range(args.iterations):
        started = perf_counter_ns()
        _assemble_once()
        elapsed_ms.append((perf_counter_ns() - started) / 1_000_000)

    result = {
        "schema": "conversation_context_microbenchmark.v1",
        "iterations": args.iterations,
        "warmup": args.warmup,
        "checkpoint_model_calls": checkpoint_calls,
        "median_ms": round(median(elapsed_ms), 4),
        "p95_ms": round(_percentile(elapsed_ms, 0.95), 4),
        "max_ms": round(max(elapsed_ms), 4),
        "threshold_ms": args.fail_over_ms,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(
        checkpoint_calls != 0
        or result["p95_ms"] >= args.fail_over_ms
        or result["max_ms"] >= args.fail_over_ms
    )


if __name__ == "__main__":
    raise SystemExit(main())
