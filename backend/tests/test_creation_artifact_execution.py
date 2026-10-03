"""Issue 99: real tool receipts must agree with persisted artifacts and diagnostics."""
import asyncio
import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from app.database.models import NovelCreationStageRun, OperationRun
from app.modules.operations.application.trace_capture import configure_trace_sink, trace_scope
from app.modules.operations.domain.context_trace import TraceScope
from app.modules.operations.infrastructure.trace_store import TraceStore
from app.services.novel_creation_runs import create_run
from app.services.workspace.tools.novel_creation_v2 import run_creation_artifact_generation
from app.services.workspace.executor import execute_workspace_action
from tests.test_novel_creation_workspace_v2 import _db, _ready_session


@pytest.fixture
def diagnostics(tmp_path):
    store = TraceStore(tmp_path / "diagnostics.sqlite3")
    configure_trace_sink(store)
    yield store
    configure_trace_sink(None)
    store.close()


@pytest.mark.parametrize("tool", [
    "generate_creation_artifact", "refine_creation_artifact", "regenerate_creation_artifact",
])
@pytest.mark.parametrize("locked", [False, True])
def test_whole_artifact_tool_persists_new_collections_or_rejects_locked_changes(tool, locked, diagnostics):
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    if locked:
        draft["artifact_locks"] = {"characters": ["/characters/0/goal"]}
        session.draft_json = draft
        db.commit()
    baseline = deepcopy(draft["stages"]["characters"]["data"])
    generated = deepcopy(baseline)
    generated["characters"][0]["goal"] = "查明隔离站旧案并公开真相"
    generated["relationships"][0]["description"] = "因调查旧案结成同盟"
    revision = session.revision
    prompts = []

    def stream(**kwargs):
        prompts.append(json.dumps(kwargs["messages"], ensure_ascii=False))

        async def values():
            yield json.dumps({"data": generated}, ensure_ascii=False)
        return values()

    try:
        with trace_scope(TraceScope(kind="creation_session", id=session.id), assistant_message_id="reply") as trace, patch(
            "app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion",
            new=MagicMock(side_effect=stream),
        ):
            result = asyncio.run(execute_workspace_action(db, "", {
                "tool": tool,
                "arguments": {
                    "session_id": session.id, "artifact": "characters", "model": "openai:test",
                    "instruction": "更新人物目标和关系", "expected_revision": revision,
                },
            }))
        db.refresh(session)
        assert baseline["characters"][0]["goal"] in prompts[0]
        current = session.draft_json["stages"]["characters"]["data"]
        run = db.query(NovelCreationStageRun).filter_by(session_id=session.id).one()
        operation = db.get(OperationRun, run.operation_id)
        if locked:
            assert result["status"] == "error"
            assert result["data"]["reason"] == "creation_artifact_locked_changed"
            assert current == baseline and session.revision == revision
            assert run.status == operation.status == "failed"
        else:
            assert result["status"] == "ok" and result["data"]["saved"] is True
            assert current["relationships"] == generated["relationships"]
            for actual, expected in zip(current["characters"], generated["characters"], strict=True):
                assert all(actual[key] == value for key, value in expected.items())
            assert session.revision == revision + 1
            assert run.status == "waiting_user"
        diagnostics.flush()
        for correlation in ("reply", run.id, operation.id):
            assert [item["id"] for item in diagnostics.list_traces("local", correlation_id=correlation)] == [trace.id]
        finished = [event["data"] for event in diagnostics.events("local", trace.id)
                    if event["event_type"] == "span_finished"]
        assert any(item["status"] == ("error" if locked else "completed") for item in finished)
    finally:
        db.close()


def test_background_preflight_failure_is_queryable_without_a_chat_turn(diagnostics):
    db = _db()
    session = _ready_session(db)
    request = {"stage": "characters", "entity_id": "missing-entity", "model": "openai:test"}
    run = create_run(db, session, "characters", request)
    db.commit()
    try:
        with patch("app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion") as model:
            result = asyncio.run(run_creation_artifact_generation(db, "", {
                **request, "session_id": session.id, "_run_id": run.id,
            }))
        model.assert_not_called()
        assert result["status"] == "error"
        diagnostics.flush()
        by_run = diagnostics.list_traces("local", correlation_id=run.id)
        by_operation = diagnostics.list_traces("local", correlation_id=run.operation_id)
        assert len(by_run) == 1 and by_run == by_operation
        assert by_run[0]["status"] == "error" and by_run[0]["finished"] is not None
    finally:
        db.close()
