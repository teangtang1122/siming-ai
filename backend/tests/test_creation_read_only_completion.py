"""Replies reflect write receipts; a chat report cannot complete an artifact."""

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest

from app.services.creation_agent_reply import (
    CREATION_READ_ONLY_COMPLETION_INSTRUCTION,
    CREATION_READ_ONLY_NOTICE,
)
from app.services.novel_creation_agent import run_creation_agent
from tests.test_creation_entity_read_admission import _call
from tests.test_novel_creation_agent import _stream_completion, _test_context_preparer
from tests.test_novel_creation_workspace_v2 import _db, _ready_session
from tests.tool_budget_helpers import request_budget
from app.services.workspace.tools.novel_creation_v2 import patch_creation_artifact_tool


def _pending_review(db):
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    draft["stages"]["final_review"] = {"status": "pending", "data": None}
    session.draft_json = draft
    db.commit()
    return session


@pytest.mark.parametrize("review", [
    {"readiness": "ready", "overall_assessment": "资料完整"},
    {"ready": "true"},
    {"ready": 1},
])
def test_review_patch_rejects_non_boolean_readiness_without_changing_saved_state(review):
    db = _db()
    try:
        session = _pending_review(db)
        before = deepcopy(session.draft_json)
        revision = session.revision
        result = asyncio.run(patch_creation_artifact_tool(db, "", {
            "session_id": session.id, "artifact": "final_review", "expected_revision": revision,
            "changes": [{"path": "/", "action": "set", "value": review}],
        }))
        assert result["status"] == "error"
        assert "ready" in result["detail"]
        assert session.revision == revision
        assert session.draft_json == before
    finally:
        db.close()


def test_read_only_chat_report_has_explicit_receipt_and_does_not_complete_stage():
    db = _db()
    try:
        session = _pending_review(db)
        before = deepcopy(session.draft_json)
        captured = []
        completion = _stream_completion([
            {"tool_calls": [_call("categories", "set_tool_categories", {
                "enabled_categories": ["creation_artifacts"],
            })]},
            {"tool_calls": [_call("review", "get_creation_artifact", {"artifact": "final_review"})]},
            {"content": "已完成对全部立项资料的最终审阅。"},
        ])
        with patch("app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools", new=completion):
            result = asyncio.run(run_creation_agent(
                db, session=session, message="查看审阅状态", model="openai:test",
                provider_request_budget=request_budget,
                prepare_model_messages=_test_context_preparer("查看审阅状态", captured=captured),
            ))
        assert CREATION_READ_ONLY_COMPLETION_INSTRUCTION in captured[-1]["messages"][0]["content"]
        assert result["reply"].startswith(CREATION_READ_ONLY_NOTICE)
        assert result["_turn_trace"]["outcome"]["reply_status"] == "read_only"
        assert result["write_count"] == 0
        assert session.draft_json == before
    finally:
        db.close()


def test_agent_saves_model_selected_review_before_reporting_generation():
    db = _db()
    try:
        session = _pending_review(db)
        revision = session.revision
        upstream = deepcopy({key: value for key, value in session.draft_json["stages"].items() if key != "final_review"})
        review = {"ready": True, "blocking": [], "warnings": [], "counts": {"reviewed_stages": 7}}
        responses = iter([
            {"tool_calls": [_call("categories", "set_tool_categories", {
                "enabled_categories": ["creation_artifacts"],
            })]},
            {"tool_calls": [_call("read", "get_creation_artifact", {"artifact": "final_review"})]},
            {"tool_calls": [_call("save", "patch_creation_artifact", {
                "artifact": "final_review", "expected_revision": revision,
                "changes": [{"path": "/", "action": "set", "value": review}],
            })]},
            {"content": "最终审阅已保存，等待作者确认。"},
        ])
        request_count = 0

        def response(**kwargs):
            nonlocal request_count
            request_count += 1
            if request_count == 3:
                assert CREATION_READ_ONLY_COMPLETION_INSTRUCTION in kwargs["messages"][0]["content"]
            return next(responses)

        completion = _stream_completion(response)
        with patch("app.services.novel_creation_agent.LLMGateway.stream_chat_completion_with_tools", new=completion):
            result = asyncio.run(run_creation_agent(
                db, session=session, message="请完成末轮审查并保留报告", model="openai:test",
                provider_request_budget=request_budget,
                prepare_model_messages=_test_context_preparer("请完成末轮审查并保留报告"),
            ))
        assert result["write_count"] == 1
        assert not result["reply"].startswith(CREATION_READ_ONLY_NOTICE)
        assert session.draft_json["stages"]["final_review"]["status"] == "generated"
        assert session.draft_json["stages"]["final_review"]["data"] == review
        assert {key: value for key, value in session.draft_json["stages"].items() if key != "final_review"} == upstream
    finally:
        db.close()
