"""Final review reads admit real entity results without rewriting saved data."""

import asyncio
import json
from collections import Counter
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from sqlalchemy import event

from app.services.novel_creation_agent import run_creation_agent
from app.services.novel_creation_entities import list_creation_entities
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.tools.novel_creation_v2 import (
    get_creation_entity_tool,
    list_creation_entities_tool,
)
from tests.test_novel_creation_agent import _stream_completion, _test_context_preparer
from tests.test_novel_creation_workspace_v2 import _db, _ready_session
from tests.tool_budget_helpers import request_budget


def _call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments, ensure_ascii=False),
    }}


def test_entity_reads_do_not_sync_flush_or_commit_saved_artifacts():
    db = _db()
    try:
        session = _ready_session(db)
        before = deepcopy(list_creation_entities(session))
        before_draft = deepcopy(session.draft_json)
        before_revision = session.revision
        writes = []
        commits = []

        def record_sql(_conn, _cursor, statement, *_args):
            if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                writes.append(statement)

        event.listen(db.get_bind(), "before_cursor_execute", record_sql)
        event.listen(db, "after_commit", lambda *_: commits.append(True))
        first = asyncio.run(list_creation_entities_tool(db, "", {
            "session_id": session.id, "artifact": "characters", "limit": 1,
        }))
        entity_id = first["data"]["entities"][0]["id"]
        second = asyncio.run(get_creation_entity_tool(db, "", {"entity_id": entity_id}))
        assert first["status"] == second["status"] == "ok"
        assert len(first["data"]["entities"]) == 1
        assert first["data"]["has_more"] is True
        assert not writes
        assert not commits
        assert not db.new and not db.dirty and not db.deleted
        assert session.revision == before_revision
        assert session.draft_json == before_draft
        assert list_creation_entities(session) == before
    finally:
        db.close()


def test_final_review_entity_batch_uses_actual_results_at_the_failed_run_budget():
    db = _db()
    try:
        session = _ready_session(db)
        before = deepcopy(list_creation_entities(session))
        before_draft = deepcopy(session.draft_json)
        before_revision = session.revision
        character_id = next(row["id"] for row in before if row["entity_type"] == "character")
        entity_calls = [
            _call("chapters", "list_creation_entities", {
                "session_id": session.id, "artifact": "opening_outline", "limit": 10,
            }),
            _call("characters", "list_creation_entities", {
                "session_id": session.id, "artifact": "characters", "limit": 10,
            }),
        ]
        completion = _stream_completion([
            {"tool_calls": [_call("categories", "set_tool_categories", {
                "enabled_categories": ["creation_entities"],
            })]},
            {"tool_calls": entity_calls},
            {"tool_calls": [_call("character", "get_creation_entity", {"entity_id": character_id})]},
            {"content": "已读取前3章和角色资料，可以继续最终审阅。"},
        ])
        executor = AsyncMock(wraps=execute_workspace_action)
        with patch(
            "app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools",
            new=completion,
        ), patch("app.services.creation_agent_execution.execute_workspace_action", new=executor):
            outcome = asyncio.run(run_creation_agent(
                db, session=session, message="最终审阅", model="openai:test",
                provider_request_budget=lambda: request_budget(26_843),
                prepare_model_messages=_test_context_preparer("最终审阅"),
            ))
        assert completion.call_count == 4
        assert all(result["status"] == "ok" for result in outcome["tool_results"])
        assert Counter(call.args[2]["tool"] for call in executor.await_args_list) == {
            "list_creation_entities": 2, "get_creation_entity": 1,
        }
        assert session.revision == before_revision
        assert session.draft_json == before_draft
        assert list_creation_entities(session) == before
    finally:
        db.close()
