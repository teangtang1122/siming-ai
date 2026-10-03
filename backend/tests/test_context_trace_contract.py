"""Cross-platform contract and failure/resource acceptance without model traffic."""

import asyncio
import json
import time
from pathlib import Path

import fastjsonschema
import httpx
import pytest
from openai import AsyncOpenAI

from app.modules.operations.application.trace_capture import (
    Span,
    configure_trace_sink,
    correlate,
    record_business_event,
    record_payload,
    trace_scope,
)
from app.modules.operations.application.trace_decorators import observed
from app.modules.operations.domain.context_trace import TraceScope
from app.modules.operations.infrastructure.http_trace import ObservedHttpClient
from app.modules.operations.infrastructure.trace_payload import encode_bounded, redact
from app.modules.operations.infrastructure.trace_store import TraceStore

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(tmp_path):
    value = TraceStore(tmp_path / "capture.sqlite3")
    configure_trace_sink(value)
    yield value
    configure_trace_sink(None)
    value.close()


def test_shared_fixture_and_real_events_follow_same_schema(store):
    schema = json.loads((ROOT / "contracts/context-trace-v1.schema.json").read_text())
    fixture = json.loads((ROOT / "contracts/fixtures/context-trace-v1-interop.json").read_text())
    for case in fixture["redaction_cases"]:
        assert redact(case["input"], tuple(case["secrets"])) == case["expected"]
    with (
        trace_scope(TraceScope(kind="creation_session", id="session")) as trace,
        Span("tool", "generate_creation_artifact") as span,
    ):
        record_payload("tool_arguments", {"role_type": "invalid"})
        span.finish("error", reason="native_tool_contract_invalid")
    store.flush()
    for event in [*fixture["events"], *store.events("local", trace.id)]:
        fastjsonschema.validate(schema, event)


def test_sdk_retry_and_summary_usage_are_real_physical_exchanges(store):
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429, headers={"retry-after-ms": "1"}, json={"error": {"message": "limited"}}
            )
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "choices": [],
                "model": "synthetic",
                "usage": {"prompt_tokens": 7, "completion_tokens": None},
            },
        )

    async def run():
        async with ObservedHttpClient(transport=httpx.MockTransport(handle)) as client:
            sdk = AsyncOpenAI(
                api_key="fixture-secret",
                base_url="https://provider.invalid/v4",
                http_client=client,
                max_retries=1,
            )
            with trace_scope(TraceScope(kind="operation", id="retry")) as trace:
                result = await sdk.chat.completions.create(
                    model="synthetic", messages=[{"role": "user", "content": "private"}]
                )
                assert result.usage.prompt_tokens == 7
            return trace

    trace = asyncio.run(run())
    store.flush()
    events = store.events("local", trace.id)
    assert len(calls) == 2
    assert {str(item.url) for item in calls} == {"https://provider.invalid/v4/chat/completions"}
    assert all(item.headers["Authorization"] == "Bearer fixture-secret" for item in calls)
    assert len([e for e in events if e["data"].get("attempt_id")]) == 2
    usage = [e["data"]["usage"] for e in events if e["event_type"] == "usage"]
    assert usage == [{"prompt_tokens": 7}]
    assert not any(e["data"].get("content_hash") for e in events)
    assert b"private" not in store.path.read_bytes()


def test_handled_failure_and_cancel_keep_business_status(store):
    @observed(kind="turn", scope_kind="operation", scope_id="id")
    async def handled(id):
        record_business_event({"type": "error", "data": {"reason": "revision_conflict"}})
        return {"status": "error"}

    @observed(kind="turn", scope_kind="operation", scope_id="id")
    async def cancelled(id):
        raise asyncio.CancelledError()

    assert asyncio.run(handled("failed")) == {"status": "error"}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled("cancelled"))
    store.flush()
    statuses = {item["scope_id"]: item["status"] for item in store.list_traces("local")}
    assert statuses == {"failed": "error", "cancelled": "cancelled"}
    assert not any(
        e["data"].get("layer") == "provider_request"
        for trace in store.list_traces("local")
        for e in store.events("local", trace["id"])
    )


@pytest.mark.parametrize("status", ["error", "denied", "blocked", "failed", "conflict"])
def test_returned_tool_failures_are_not_successful_spans(store, status):
    @observed(kind="executor")
    async def execute():
        return {"status": status}

    @observed(kind="tool")
    def preflight():
        return {"status": status}

    with trace_scope(TraceScope(kind="operation", id="rejected")) as trace:
        asyncio.run(execute())
        preflight()
    store.flush()
    finishes = [e["data"] for e in store.events("local", trace.id) if e["event_type"] == "span_finished"]
    assert [item["status"] for item in finishes] == ["error", "error"]


def test_all_run_correlations_survive_retries_and_store_upgrade(store):
    with trace_scope(TraceScope(kind="creation_session", id="session"), assistant_message_id="reply") as trace:
        correlate(run_id="first-run", operation_id="first-operation")
        correlate(run_id="retry-run", operation_id="retry-operation")
    store.flush()
    links = ("reply", "first-run", "first-operation", "retry-run", "retry-operation")
    for link in links:
        assert store.list_traces("local", correlation_id=link)[0]["id"] == trace.id
    with store.db:
        store.db.execute("DROP TABLE trace_correlations")
        store.db.execute("PRAGMA user_version=1")
        store.db.execute("UPDATE traces SET status='running',capture_status='interrupted' WHERE id=?", (trace.id,))
    reopened = TraceStore(store.path)
    try:
        assert reopened.trace("local", trace.id)["status"] == "interrupted"
        for link in links:
            assert reopened.list_traces("local", correlation_id=link)[0]["id"] == trace.id
    finally:
        reopened.close()


def test_queue_full_content_limit_policy_expiry_and_recovery(store, monkeypatch):
    import app.modules.operations.infrastructure.trace_store as storage

    store.set_policy("local", "full", 60)
    with trace_scope(TraceScope(kind="operation", id="large")) as trace:
        monkeypatch.setattr(storage, "MEMORY_LIMIT", 1)
        record_payload("tool_arguments", {"private": "must not wait"})
        assert trace.dropped > 0
        monkeypatch.setattr(storage, "MEMORY_LIMIT", 32 * 1024 * 1024)
    store.flush()
    assert store.trace("local", trace.id)["capture_status"] == "partial"
    assert encode_bounded({"text": '"' * 20000}, 1000) is None
    assert json.loads(encode_bounded({"text": '中"\n' * 3000})) == {"text": '中"\n' * 3000}
    policy = store.policies["local"]
    store.policies["local"] = policy.model_copy(update={"full_until": time.time() - 1})
    with trace_scope(TraceScope(kind="operation", id="expired")) as expired:
        assert expired.policy.mode == "summary"
    store.flush()
    with store.db:
        store.db.execute("UPDATE traces SET finished=NULL,status='running' WHERE id=?", (trace.id,))
    reopened = TraceStore(store.path)
    try:
        saved = reopened.trace("local", trace.id)
        assert saved["capture_status"] == "interrupted" and saved["status"] == "interrupted"
    finally:
        reopened.close()


def test_long_stream_batches_deltas_and_does_not_delay_delivery(store):
    store.set_policy("local", "full", None)

    @observed(kind="turn", scope_kind="operation", scope_id="id")
    async def stream(id):
        for _ in range(10000):
            yield "x"

    async def run():
        seen = 0
        async for item in stream("stream"):
            assert item == "x"
            seen += 1
        return seen

    assert asyncio.run(run()) == 10000
    store.flush()
    trace = store.list_traces("local")[0]
    outputs = [
        e for e in store.events("local", trace["id"]) if e["data"].get("layer") == "adapter_output"
    ]
    assert len(outputs) == 1
    assert (
        len(json.loads(store.payload("local", trace["id"], outputs[0]["event_id"])["content"]))
        == 10000
    )


def test_capacity_eviction_reuses_space_and_preserves_active_trace(tmp_path):
    bounded = TraceStore(tmp_path / "bounded.sqlite3", max_bytes=512 * 1024)
    configure_trace_sink(bounded)
    bounded.set_policy("local", "full", None)
    try:
        with trace_scope(TraceScope(kind="operation", id="active")) as active:
            for index in range(24):
                with trace_scope(TraceScope(kind="operation", id=str(index))) as newest:
                    record_payload("provider_response", {"content": "x" * 24000})
                bounded.flush()
                assert bounded.trace("local", active.id) is not None
            saved = bounded.list_traces("local")
            assert 1 < len(saved) < 24
            assert bounded.trace("local", newest.id) is not None
            assert bounded.write_errors == 0
            assert bounded.path.stat().st_size <= 256 * 1024
    finally:
        configure_trace_sink(None)
        bounded.close()


def test_observed_provider_clients_keep_sdk_defaults():
    from anthropic import AsyncAnthropic
    from openai import DefaultAsyncHttpxClient

    from app.ai.anthropic_adapter import TracedAnthropicHttpClient
    from app.ai.openai_adapter import TracedOpenAIHttpClient

    async def run():
        original_anthropic = AsyncAnthropic(api_key="fixture")
        pairs = [
            (DefaultAsyncHttpxClient(), TracedOpenAIHttpClient()),
            (original_anthropic._client, TracedAnthropicHttpClient()),
        ]
        for original, observed_client in pairs:
            assert original.timeout == observed_client.timeout
            assert original.follow_redirects == observed_client.follow_redirects
            assert original._trust_env == observed_client._trust_env
            assert (
                original._transport._pool._max_connections
                == observed_client._transport._pool._max_connections
            )
            await original.aclose()
            await observed_client.aclose()

    asyncio.run(run())
