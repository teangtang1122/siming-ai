import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import AssistantRun, Base
from app.services.conversation_context.execution_ledger import (
    resource_references_from_run_step,
    tool_receipts_from_run_steps,
)
from app.services.workspace.run_log import finish_run_step, start_run_step
from app.services.workspace.tool_output_refs import output_refs_from_tool_result


def _result(status: str, data: object, detail: str = "") -> dict[str, object]:
    return {"status": status, "data": data, "detail": detail}


def test_only_declared_result_paths_become_output_refs() -> None:
    assert output_refs_from_tool_result(
        "unknown_writer",
        _result("ok", {"id": "invented", "chapter_id": "chapter-1"}),
    ) == {}
    assert output_refs_from_tool_result(
        "create_character",
        _result(
            "ok",
            {
                "character_id": "wrong-field",
                "nested": {"id": "wrong-level"},
                "detail": "id=prose-only",
            },
        ),
    ) == {}
    # Relationship handlers currently return no structured relationship ID.
    assert output_refs_from_tool_result(
        "create_relationship",
        _result("ok", {"relationship_id": "not-a-declared-result"}),
    ) == {}

    assert output_refs_from_tool_result(
        "create_character",
        _result("ok", {"id": "character-1", "current_version": 4}),
    ) == {"character": {"id": "character-1", "revision": 4}}


def test_skipped_failed_and_mismatched_steps_do_not_invent_refs() -> None:
    request = {"session_id": "session-from-request", "expected_revision": 7}
    successful_shape = {"artifact": {"revision": 8}}

    assert output_refs_from_tool_result(
        "patch_creation_artifact",
        _result("skipped", successful_shape),
        request=request,
        step_status="skipped",
    ) == {}
    assert output_refs_from_tool_result(
        "patch_creation_artifact",
        _result("error", successful_shape),
        request=request,
        step_status="error",
    ) == {}
    assert output_refs_from_tool_result(
        "patch_creation_artifact",
        _result("ok", successful_shape),
        request=request,
        step_status="error",
    ) == {}


def test_partial_outline_error_preserves_every_committed_node() -> None:
    refs = output_refs_from_tool_result(
        "create_outline_nodes",
        _result(
            "error",
            {
                "nodes": [
                    {"id": "outline-1", "title": "第一章"},
                    {"id": "outline-2", "title": "第二章"},
                    {"id": "outline-1", "title": "重复回执"},
                    {"outline_node_id": "wrong-field"},
                ],
                "skipped": ["第三章失败，文本中的 id 不构成回执"],
            },
        ),
        step_status="error",
    )

    assert refs == {
        "outline": [
            {"id": "outline-1"},
            {"id": "outline-2"},
        ]
    }


def test_creation_request_fallback_is_success_only_and_revision_is_result_only() -> None:
    refs = output_refs_from_tool_result(
        "patch_creation_artifact",
        _result("ok", {"artifact": {"revision": 9}}),
        request={"session_id": "session-1", "expected_revision": 8},
        step_status="ok",
    )
    assert refs == {"creation_session": {"id": "session-1", "revision": 9}}

    # Missing post-write revision is not replaced with expected_revision.
    refs_without_revision = output_refs_from_tool_result(
        "patch_creation_artifact",
        _result("ok", {"artifact": {}}),
        request={"session_id": "session-1", "expected_revision": 41},
        step_status="ok",
    )
    assert refs_without_revision == {"creation_session": {"id": "session-1"}}

    # A non-whitelisted tool cannot turn a request ID into a receipt.
    assert output_refs_from_tool_result(
        "get_creation_snapshot",
        _result("ok", {"revision": 42}),
        request={"session_id": "session-1"},
        step_status="ok",
    ) == {}


def test_verified_mcp_write_accepts_only_structured_integer_revision() -> None:
    result = _result(
        "ok",
        {
            "session_id": "session-1",
            "revision_before": 4,
            "revision_after": 5,
        },
        detail="模型文本声称 revision=999，但不是权威字段",
    )
    assert output_refs_from_tool_result(
        "mcp_verified_write",
        result,
        step_status="ok",
    ) == {"creation_session": {"id": "session-1", "revision": 5}}

    forged_text = _result(
        "ok",
        {"session_id": "session-1", "revision_after": "5"},
        detail="revision_after=5",
    )
    assert output_refs_from_tool_result(
        "mcp_verified_write",
        forged_text,
        step_status="ok",
    ) == {}


def test_creation_entity_and_finalize_results_emit_multiple_exact_resources() -> None:
    entity_refs = output_refs_from_tool_result(
        "patch_creation_entity",
        _result(
            "ok",
            {
                "entity": {"id": "entity-1", "session_id": "session-1", "revision": 5},
                "artifact": {"revision": 11},
            },
        ),
        request={"entity_id": "stale-request-id", "expected_revision": 10},
        step_status="ok",
    )
    assert entity_refs == {
        "creation_entity": {"id": "entity-1", "revision": 5},
        "creation_session": {"id": "session-1", "revision": 11},
    }

    finalize_refs = output_refs_from_tool_result(
        "finalize_creation_session",
        _result("ok", {"project_id": "project-1"}),
        request={"session_id": "session-1", "revision": 999},
        step_status="ok",
    )
    assert finalize_refs == {
        "project": {"id": "project-1"},
        "creation_session": {"id": "session-1"},
    }


def test_finish_run_step_uses_the_persisted_request_for_creation_refs() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        run = AssistantRun(project_id="project-1", status="running", phase="tool")
        db.add(run)
        db.commit()
        step = start_run_step(
            db,
            run,
            step_type="write",
            tool="patch_creation_artifact",
            request={"session_id": "session-1", "expected_revision": 3},
        )
        assert step is not None

        finish_run_step(
            db,
            step,
            status="ok",
            result=_result("ok", {"artifact": {"revision": 4}}),
        )

        assert json.loads(step.output_refs or "{}") == {
            "creation_session": {"id": "session-1", "revision": 4}
        }

        partial_step = start_run_step(
            db,
            run,
            step_type="write",
            tool="create_outline_nodes",
            request={"nodes": [{"title": "第一章"}, {"title": "第二章"}]},
        )
        assert partial_step is not None
        finish_run_step(
            db,
            partial_step,
            status="error",
            result=_result(
                "error",
                {"nodes": [{"id": "outline-1"}, {"id": "outline-2"}]},
            ),
            error="第三个节点失败",
        )
        assert json.loads(partial_step.output_refs or "{}") == {
            "outline": [{"id": "outline-1"}, {"id": "outline-2"}]
        }
    finally:
        db.close()
        engine.dispose()


def test_execution_ledger_rejects_noncommitting_status_and_never_resolves_revision() -> None:
    skipped = SimpleNamespace(
        id="step-skipped",
        run_id="run-1",
        tool="update_character",
        status="skipped",
        output_refs='{"character":{"id":"character-1","revision":2}}',
    )
    assert resource_references_from_run_step(skipped) == ()

    successful = SimpleNamespace(
        id="step-ok",
        run_id="run-1",
        tool="update_character",
        status="ok",
        output_refs='{"character":{"id":"character-1"}}',
    )
    references = resource_references_from_run_step(
        successful,
        revision_resolver=lambda _resource_type, _resource_id: 999,
    )
    assert len(references) == 1
    assert references[0].revision is None


def test_partial_outline_receipt_is_marked_as_committed_but_remains_an_error() -> None:
    partial = SimpleNamespace(
        id="step-partial",
        run_id="run-1",
        tool="create_outline_nodes",
        status="error",
        detail="第二个节点失败",
        output_refs='{"outline":[{"id":"outline-1"},{"id":"outline-2"}]}',
    )

    receipts = tool_receipts_from_run_steps(
        [partial],
        write_tools={"create_outline_nodes"},
    )

    assert len(receipts) == 1
    assert receipts[0].status == "error"
    assert receipts[0].resource_ids == ("outline-1", "outline-2")
    assert receipts[0].write_committed is True


def test_compact_receipt_never_replays_raw_run_step_diagnostics() -> None:
    secret = "sk-live-secret tool_call:{dangerous_arguments:true} provider reasoning"
    failed = SimpleNamespace(
        id="step-secret-error",
        run_id="run-1",
        tool="search_chapters",
        status="error",
        detail=secret,
        error=secret,
        output_refs=None,
    )

    receipt = tool_receipts_from_run_steps([failed])[0]

    assert receipt.summary == "search_chapters 执行失败"
    assert secret not in repr(receipt.to_dict())
    assert "dangerous_arguments" not in repr(receipt.to_dict())
