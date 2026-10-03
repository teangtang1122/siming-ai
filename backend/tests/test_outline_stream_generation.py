"""Outline proposals require a complete stream, with no whole-request deadline."""

import asyncio
import json
from importlib import import_module

import pytest

from app.services.workspace.tools.native_structured_output import (
    NativeStructuredOutputError,
    required_tool_arguments,
)
from app.services.workspace.tools.outline_writer import _generate_outline

writer_module = import_module("app.services.workspace.tools.outline_writer")


def test_outline_generator_collects_one_complete_native_call(monkeypatch):
    observed = {}
    proposal = {"nodes": [{"title": "Next", "node_type": "chapter"}], "design_notes": "Continue"}
    arguments = json.dumps(proposal)

    async def stream(**kwargs):
        observed.update(kwargs)
        yield {"type": "tool_call_delta", "index": 0, "id": "call-1",
               "name": "propose_outline_nodes", "arguments_delta": arguments[:15]}
        yield {"type": "tool_call_delta", "index": 0, "arguments_delta": arguments[15:]}
        yield {"type": "done", "finish_reason": "tool_calls"}

    monkeypatch.setattr(writer_module.LLMGateway, "stream_chat_completion_with_tools", stream)
    result = asyncio.run(_generate_outline(messages=[], model="openai:test", max_tokens=4000,
                                         gateway_extra={}, batch_count=1))
    assert required_tool_arguments(result, expected_name="propose_outline_nodes")[0] == proposal
    assert observed["timeout"] == 180
    assert observed["retry"] == observed["resume"] == 0
    assert observed["tool_choice"] == "required"
    schema = observed["tools"][0]["function"]["parameters"]["properties"]["nodes"]
    assert schema["minItems"] == schema["maxItems"] == 1


@pytest.mark.parametrize("break_connection", [False, True])
def test_outline_generator_never_accepts_unfinished_native_arguments(monkeypatch, break_connection):
    async def stream(**_kwargs):
        yield {"type": "tool_call_delta", "index": 0, "id": "call-1",
               "name": "propose_outline_nodes", "arguments_delta": '{"nodes":['}
        if break_connection:
            raise TimeoutError("provider stalled")

    monkeypatch.setattr(writer_module.LLMGateway, "stream_chat_completion_with_tools", stream)
    with pytest.raises(TimeoutError if break_connection else NativeStructuredOutputError):
        asyncio.run(_generate_outline(messages=[], model="openai:test", max_tokens=4000,
                                     gateway_extra={}, batch_count=1))
