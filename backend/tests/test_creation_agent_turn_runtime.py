from __future__ import annotations

import asyncio
import json
import time
from uuid import uuid4
from unittest.mock import AsyncMock, Mock

from app.services.creation_agent_turn_runtime import creation_agent_turn_stream


def test_creation_failure_has_correlated_redacted_diagnostic(monkeypatch, caplog):
    import app.services.creation_agent_turn_runtime as runtime

    request = runtime.CreationAgentTurnInput(
        session_id="session", message="create", client_turn_id="turn", model=None,
        conversation_id="conversation", assistant_message_id="assistant",
        local_cli_read_paths=(),
    )
    conversations = Mock()
    context = runtime._TurnContext(
        request=request, db=Mock(), conversations=conversations,
        conversation_id="conversation", assistant_message_id="assistant",
    )
    publish = AsyncMock()
    monkeypatch.setattr(runtime, "commit_session", lambda _db: None)
    secret = "sk-private-credential-must-not-leak"
    asyncio.run(runtime._persist_turn_error(
        context, publish, RuntimeError(f"CLI contract failed; api_key={secret}"),
    ))
    event = publish.await_args.args[0]
    error_id = event["data"]["error_id"]
    persisted = conversations.finish_turn.call_args.args[2]
    assert error_id in event["message"]
    assert error_id in caplog.text
    assert persisted["payload"]["creation_agent_error"]["error_id"] == error_id
    assert persisted["status"] == "error"
    assert secret not in caplog.text
    assert secret not in json.dumps(event)
    assert secret not in json.dumps(persisted)


async def _collect(stream) -> list[dict]:
    events: list[dict] = []
    async for frame in stream:
        data_line = next(line for line in frame.splitlines() if line.startswith("data:"))
        events.append(json.loads(data_line.removeprefix("data:").strip()))
    return events


def test_sse_runtime_sequences_events_and_reconnects_without_reexecution():
    async def scenario():
        executions = 0

        async def producer(publish):
            nonlocal executions
            executions += 1
            await publish({"type": "turn_started", "message": "accepted", "data": {}})
            await asyncio.sleep(0.02)
            await publish({"type": "complete", "message": "done", "data": {"reply": "ok"}})

        client_turn_id = str(uuid4())
        stream = creation_agent_turn_stream(
            client_turn_id=client_turn_id,
            request_fingerprint="same",
            after_sequence=0,
            producer=producer,
        )
        first = []
        async for frame in stream:
            first.append(json.loads(next(
                line.removeprefix("data:").strip()
                for line in frame.splitlines()
                if line.startswith("data:")
            )))
            break
        await stream.aclose()
        await asyncio.sleep(0.04)
        resumed = await _collect(creation_agent_turn_stream(
            client_turn_id=client_turn_id,
            request_fingerprint="same",
            after_sequence=first[-1]["sequence"],
            producer=producer,
        ))
        return executions, first, resumed

    started_at = time.monotonic()
    executions, first, resumed = asyncio.run(scenario())
    assert time.monotonic() - started_at < 0.5
    assert executions == 1
    assert [event["sequence"] for event in first + resumed] == [1, 2]
    assert resumed[-1]["type"] == "complete"
    assert resumed[-1]["data"]["reply"] == "ok"


def test_sse_runtime_rejects_reusing_an_id_for_different_input():
    async def scenario():
        async def producer(publish):
            await publish({"type": "complete", "message": "done", "data": {"reply": "ok"}})

        client_turn_id = str(uuid4())
        await _collect(creation_agent_turn_stream(
            client_turn_id=client_turn_id,
            request_fingerprint="first",
            after_sequence=0,
            producer=producer,
        ))
        conflict = await _collect(creation_agent_turn_stream(
            client_turn_id=client_turn_id,
            request_fingerprint="second",
            after_sequence=0,
            producer=producer,
        ))
        assert conflict[-1]["type"] == "error"
        assert conflict[-1]["data"]["error_type"] == "client_turn_conflict"

    asyncio.run(scenario())


def test_sse_runtime_emits_real_wait_heartbeat(monkeypatch):
    import app.services.creation_agent_turn_runtime as runtime

    monkeypatch.setattr(runtime, "_HEARTBEAT_SECONDS", 0.01)

    async def producer(publish):
        await publish({"type": "turn_started", "message": "accepted", "data": {}})
        await asyncio.sleep(0.035)
        await publish({"type": "complete", "message": "done", "data": {"reply": "ok"}})

    events = asyncio.run(_collect(creation_agent_turn_stream(
        client_turn_id=str(uuid4()),
        request_fingerprint="heartbeat",
        after_sequence=0,
        producer=producer,
    )))
    heartbeats = [event for event in events if event["type"] == "heartbeat"]
    assert heartbeats
    assert all("等待" in event["message"] for event in heartbeats)


def test_sse_runtime_continues_sequence_after_process_local_state_was_lost():
    async def producer(publish):
        await publish({"type": "complete", "message": "restored", "data": {"reply": "durable"}})

    events = asyncio.run(_collect(creation_agent_turn_stream(
        client_turn_id=str(uuid4()),
        request_fingerprint="restored",
        after_sequence=7,
        producer=producer,
    )))
    assert [event["sequence"] for event in events] == [8]
    assert events[0]["data"]["reply"] == "durable"
