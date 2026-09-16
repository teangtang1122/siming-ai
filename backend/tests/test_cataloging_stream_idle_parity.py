"""The PC gateway and Android consume the same synthetic provider stream cases."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.deepseek_adapter import DeepSeekAdapter
from app.core.exceptions import LLMError
from app.modules.model_runtime.infrastructure.gateway import LLMGateway

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "contracts/fixtures/agent-stream-idle-v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_pc_stream_event_idle_boundary_matches_android(monkeypatch, case):
    async def provider_stream():
        for index, event in enumerate(case["events"]):
            if index:
                await asyncio.sleep(case["interval_ms"] / 1000)
            # SSE comments never reach the SDK's parsed chunk iterator.
            if event is not None:
                yield json.loads(json.dumps(event), object_hook=lambda value: SimpleNamespace(**value))

    async def create(**_kwargs):
        await asyncio.sleep(case["headers_delay_ms"] / 1000)
        return provider_stream()

    create_mock = AsyncMock(side_effect=create)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock)))
    config = SimpleNamespace(api_key="fixture-key", base_url="http://127.0.0.1:1", provider="deepseek", api_protocol="chat_completions")
    monkeypatch.setattr(LLMGateway, "_parse_model", staticmethod(lambda _model: ("deepseek", "deepseek-flash")))
    monkeypatch.setattr(LLMGateway, "_load_config", staticmethod(lambda _provider: config))
    monkeypatch.setattr(LLMGateway, "_get_adapter", staticmethod(lambda _provider: DeepSeekAdapter))
    monkeypatch.setattr(DeepSeekAdapter, "_get_client", lambda _self: client)

    async def collect():
        started = asyncio.get_running_loop().time()
        received = []
        error = None
        try:
            async for event in LLMGateway.stream_chat_completion_with_tools(
                messages=[{"role": "user", "content": "read"}], model="deepseek:deepseek-flash",
                tools=[{"type": "function", "function": {"name": "read_archive", "parameters": {"type": "object"}}}],
                timeout=FIXTURE["idle_timeout_ms"] / 1000, retry=0, resume=0,
            ):
                received.append(event)
        except LLMError as exc:
            error = exc
        if case["expected"] == "completed":
            assert error is None
            assert asyncio.get_running_loop().time() - started > FIXTURE["idle_timeout_ms"] / 1000
            arguments = "".join(event.get("arguments_delta", "") for event in received if event["type"] == "tool_call_delta")
            assert json.loads(arguments) == {"id": "chapter-1"}
            assert received[-1]["type"] == "done"
        else:
            assert error is not None and "超时" in str(error)
            assert not any(event["type"] == "tool_call_delta" for event in received)
        assert create_mock.await_count == 1

    asyncio.run(collect())
