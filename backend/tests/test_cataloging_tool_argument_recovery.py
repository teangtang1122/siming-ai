import asyncio
import json

import pytest

from app.core.exceptions import LLMToolArgumentsError
from app.database.models import CatalogingCandidate, ChapterSummary
from app.modules.model_runtime.infrastructure.gateway import _ToolStreamAttempt, _complete_tool_stream
from app.modules.model_runtime.infrastructure.tool_argument_errors import completed_tool_argument_error
from app.services.cataloging import orchestrator
from app.services.workspace.registry import registry
from tests.test_cataloging_plan import archive, plan_rows, submit  # noqa: F401

SAVE = "save_external_cataloging_candidates"


def broken_events():
    return [
        {"index": 0, "id": "valid-prefix", "name": "read_cataloging_archive", "arguments_delta": '{"kind":"character"}'},
        {"index": 1, "id": "bad-save", "name": SAVE, "arguments_delta": '{"candidates":[{"type":"chapter_summary"}},"finalize":false}'},
    ]


def test_completed_invalid_json_retains_whole_native_batch_without_publishing():
    attempt = _ToolStreamAttempt(content_events=[{"delta": "unaccepted"}],
        tool_events=broken_events(), reasoning=["reason"],
        done_event={"finish_reason": "tool_calls", "provider_state": [{"type": "reasoning", "encrypted_content": "state"}]})
    published = []
    with pytest.raises(LLMToolArgumentsError) as caught:
        for event in _complete_tool_stream(attempt, None, {}, False, 0, [], "deepseek", "model", None, True):
            published.append(event)
    assert published == []
    message = caught.value.assistant_message
    assert [call["id"] for call in message["tool_calls"]] == ["valid-prefix", "bad-save"]
    assert message["tool_calls"][1]["function"]["arguments"] == broken_events()[1]["arguments_delta"]
    assert message["reasoning_content"] == "reason" and message["content"] == "unaccepted"
    assert message["provider_state"] == attempt.done_event["provider_state"]


@pytest.mark.parametrize("ids", [("", "b"), ("a", "a")])
def test_missing_or_duplicate_identity_cannot_receive_correction_receipts(ids):
    events = broken_events()
    for event, identity in zip(events, ids):
        event["id"] = identity
    assert completed_tool_argument_error(events) is None


def test_responses_can_replay_a_whole_unexecuted_argument_error_without_repair(archive):
    from app.ai.openai_adapter import _responses_input
    from app.services.cataloging.tool_argument_recovery import record_tool_argument_failure
    db, job, run = archive
    error = completed_tool_argument_error(broken_events())
    messages = [{"role": "system", "content": "contract"}, {"role": "user", "content": "task"}]
    record_tool_argument_failure(db, run, messages, error, 1, {SAVE}, 1)
    items = _responses_input(messages)
    function = next(item for item in items if item.get("call_id") == "bad-save" and item["type"] == "function_call")
    assert function["arguments"] == broken_events()[1]["arguments_delta"]
    # A missing receipt cannot exempt a malformed batch from strict validation.
    with pytest.raises(ValueError):
        _responses_input(messages[:-1])


def test_exhaustion_keeps_candidates_and_all_invalid_responses(archive, monkeypatch):
    db, job, run = archive
    submit(archive, [plan_rows()[0]], finalize=False)
    before = [(row.id, row.raw_payload) for row in db.query(CatalogingCandidate)]
    requests = []

    async def model(**kwargs):
        requests.append(json.loads(json.dumps(kwargs["messages"])))
        if False:
            yield {}
        raise completed_tool_argument_error(broken_events())

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    monkeypatch.setattr(registry, "get_handler", lambda name: pytest.fail("invalid batch cannot execute a prefix"))

    async def run_agent():
        return [event async for event in orchestrator._extract_run(db, job, run)]

    asyncio.run(run_agent())
    assert len(requests) == 3
    assert "三次返回无效工具参数 JSON" in run.error
    assert [(row.id, row.raw_payload) for row in db.query(CatalogingCandidate)] == before
    assert db.query(ChapterSummary).count() == 0
    audit = json.loads(run.raw_output)
    receipts = [json.loads(item["content"]) for item in audit if item["role"] == "tool"]
    assert len(receipts) == 6
    assert all(item["data"]["executed"] is False for item in receipts)
    assert [item["data"]["retryable"] for item in receipts] == [True, True, True, True, False, False]
    assert requests[1][-2]["tool_call_id"] == "valid-prefix"
    assert requests[1][-1]["tool_call_id"] == "bad-save"


def test_same_model_correction_can_finalize_existing_plan(archive, monkeypatch):
    db, job, run = archive
    submit(archive, plan_rows(), finalize=False)
    expected_ids = {row.id for row in db.query(CatalogingCandidate)}
    requests = []

    async def model(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise completed_tool_argument_error(broken_events())
        assert kwargs["messages"][-1]["tool_call_id"] == "bad-save"
        yield {"type": "tool_call_delta", "index": 0, "id": "repaired", "name": SAVE,
               "arguments_delta": json.dumps({"job_id": job.id, "chapter_id": run.chapter_id, "candidates": [], "finalize": True})}
        yield {"type": "done"}

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)

    async def run_agent():
        return [event async for event in orchestrator._extract_run(db, job, run)]

    asyncio.run(run_agent())
    assert len(requests) == 2
    assert not run.error
    assert {row.id for row in db.query(CatalogingCandidate)} == expected_ids
    assert db.query(ChapterSummary).count() == 0  # manual review still owns the final write
