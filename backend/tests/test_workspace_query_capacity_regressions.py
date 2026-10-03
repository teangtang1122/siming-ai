"""Real paged handlers and public recovery receipts for the September incident."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, Chapter, OutlineNode, Project
from app.mcp.adapter import _project_tool_execution
from app.services.conversation_context import ConversationContextError, ConversationContextErrorCode
from app.services.creation_agent_turn_runtime import safe_creation_agent_error
from app.services.workspace.assistant_public_errors import public_context_failure
from app.services.workspace.assistant_public_projection import (
    public_run_mapping,
    public_step_payload,
    public_tool_log,
)
from app.services.workspace.registry import registry
from app.services.workspace.tool_result_projection import model_tool_result_projector
from app.services.workspace.tools.search import list_chapters, search_outline, search_outline_tree


@pytest.mark.parametrize("character", ["汉", "𠮷", "\x01"])
def test_actual_41_chapter_pages_fit_declared_bounds_without_losing_fields(character):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        title = character * 200
        project = Project(id=str(uuid4()), title="容量边界测试")
        root = OutlineNode(id=str(uuid4()), project_id=project.id, node_type="volume",
                           title=title, summary=character * 200,
                           actual_summary=character * 200, planned_summary=character * 200)
        db.add_all([project, root])
        nodes = [OutlineNode(id=str(uuid4()), project_id=project.id, parent_id=root.id,
                             node_type="chapter", title=title, summary=character * 200,
                             actual_summary=character * 200, planned_summary=character * 200,
                             sort_order=i) for i in range(41)]
        chapters = [Chapter(id=str(uuid4()), project_id=project.id, outline_node_id=node.id,
                            title=title, content="测试正文") for node in nodes]
        db.add_all([*nodes, *chapters])
        db.commit()
        for handler in (list_chapters, search_outline_tree, search_outline):
            tool = registry.get(handler.__name__)
            for limit in (1, tool.model_result_contract.page_budget.max_items):
                args = {"limit": limit}
                if handler is search_outline:
                    args["node_id"] = root.id
                elif handler is search_outline_tree:
                    args["root_id"] = root.id
                seen = []
                while True:
                    raw = asyncio.run(handler(db, project.id, args))
                    projected = model_tool_result_projector.project(tool, raw, arguments=args)
                    value = json.loads(projected.content)
                    mcp_result, _ = _project_tool_execution(tool, raw, args)
                    assert not mcp_result.is_error
                    assert json.loads(mcp_result.content[0]["text"]) == value
                    assert value["page"] == raw["page"]
                    rows = value["data"][0]["children"] if handler is search_outline else value["data"]
                    assert all(row["title"] == title for row in rows)
                    seen.extend(row["id"] for row in rows)
                    if not value["page"]["has_more"]:
                        break
                    assert value["page"]["next_cursor"] > args.get("cursor", 0)
                    args = value.get("next_arguments") or {**args, "cursor": value["page"]["next_cursor"]}
                expected = chapters if handler is list_chapters else nodes
                assert len(seen) == 41
                assert set(seen) == {item.id for item in expected}
        assert db.query(Chapter).count() == 41
        assert db.query(OutlineNode).count() == 42
    engine.dispose()


def test_capacity_receipts_are_specific_idempotent_and_do_not_leak_provider_state():
    private = "private reasoning / sensitive provider diagnostic"
    raw = {"tool": "search_outline", "status": "error", "detail": private, "data": {
        "reason": "tool_result_batch_over_capacity", "required_tokens": 99_000,
        "available_tokens": 20_000, "retryable": True, "reasoning_content": private,
        "assistant_json_bytes": 321, "declared_result_json_bytes": 49_152,
    }}
    public = public_tool_log(raw)
    assert "99000" in public["detail"] and "20000" in public["detail"]
    assert public_tool_log(public) == public
    step = SimpleNamespace(
        id="step-capacity", run_id="run-1", step_type="search", tool="search_outline",
        status="error", iteration=1, attempt_no=1, retry_of_step_id=None, resolved_step_id=None,
        request_json=json.dumps({"native_assistant_transaction": private}),
        result_json=json.dumps(raw), output_refs=None, started_at=None, completed_at=None,
    )
    public_step = public_step_payload(step, can_retry=False, retry_block_reason=None)
    assert public_step["detail"] == public["detail"]
    assert public_step["remediation"] == public["remediation"]
    error = ConversationContextError(ConversationContextErrorCode.TOOL_TRANSACTION_OVER_CAPACITY,
                                     private, details={**raw["data"], "consecutive_rejections": 3})
    workspace = public_context_failure(error)
    message, creation = safe_creation_agent_error(error)
    assert message == workspace.message
    assert creation["details"]["required_tokens"] == 99_000
    assert creation["details"]["retryable"] is False
    run = public_run_mapping({"status": "error", "error": workspace.persisted_error})
    assert run["error_code"] == "tool_transaction_over_capacity"
    assert "剩余容量" in run["error"]
    assert private not in json.dumps([public_step, workspace.to_dict(), creation, run])


def test_scheduled_request_uses_explicit_profile_before_unknown_model_fallback():
    from app.database.models import ModelContextProfile
    from app.services.workspace.scheduled_task_runner import (
        _scheduled_request_budget,
        _tool_schemas,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(ModelContextProfile(provider="openai", model_name="query-capacity-test",
                                   context_window_tokens=1_000_000, max_output_tokens=2048,
                                   safety_margin_tokens=512))
        db.commit()
        messages = [{"role": "system", "content": "只读测试"}, {"role": "user", "content": "查询章节"}]
        schemas = _tool_schemas({"list_chapters"}, ("chapters",))
        model, budget = _scheduled_request_budget(db, messages, schemas, "openai:query-capacity-test")
        assert model == "openai:query-capacity-test"
        assert budget.context_window_tokens == 1_000_000
        assert budget.output_reserve_tokens == 2048
        assert budget.tool_transaction_budget_tokens == budget.request_input_limit - budget.current_input_tokens
        _, fallback = _scheduled_request_budget(db, messages, schemas, "openai:unlisted-capacity-test")
        assert fallback.context_window_tokens == 256_000
    engine.dispose()
