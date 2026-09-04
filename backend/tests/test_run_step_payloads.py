import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import AssistantRun, AssistantRunStep, Base
from app.services.workspace.run_log import start_run_step, step_payload
from app.services.workspace.run_recovery import retry_step
from app.services.workspace.run_step_payloads import (
    UnrecoverableStepRequest,
    deserialize_step_request,
    serialize_step_request,
    serialize_step_result,
)


def test_large_step_request_is_saved_losslessly() -> None:
    request = {"content": "长正文\\\"\n" * 20_000, "chapter_id": "chapter-1"}

    encoded = serialize_step_request(request)

    assert len(encoded) > 80_000
    assert json.loads(encoded) == request


def test_non_json_step_request_is_rejected_instead_of_changed() -> None:
    with pytest.raises(ValueError, match="无法完整序列化"):
        serialize_step_request({"value": object()})


def test_large_step_result_is_saved_losslessly() -> None:
    result = {"content": "\\\"\n" * 40_000}

    encoded = serialize_step_result(result)
    decoded = json.loads(encoded)

    assert len(encoded) > 80_000
    assert decoded == result


def test_legacy_truncated_request_is_explicitly_unrecoverable() -> None:
    raw = '{"content":"' + ("x" * 80_000) + "...[truncated]"

    with pytest.raises(UnrecoverableStepRequest, match="无法安全重试"):
        deserialize_step_request(raw)

    run = AssistantRun(id="run-1", project_id="project-1", status="error")
    # Constructing the ORM row is enough to verify the API contract without a
    # database: clients must not offer retry for a request that cannot replay.
    step = AssistantRunStep(
        id="step-1",
        run_id=run.id,
        project_id=run.project_id,
        step_type="tool",
        tool="chapter_writer",
        status="error",
        request_json=raw,
    )
    payload = step_payload(step)
    assert payload["can_retry"] is False
    assert "无法安全重试" in payload["retry_block_reason"]


def test_retry_replays_complete_large_request() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        run = AssistantRun(project_id="project-1", status="running", phase="tool")
        db.add(run)
        db.commit()

        request = {"query": "线索" * 45_000, "limit": 20}
        original = start_run_step(
            db,
            run,
            step_type="tool",
            tool="search_chapters",
            request=request,
        )
        assert original is not None
        assert len(original.request_json or "") > 80_000
        original.status = "error"
        db.commit()

        execute = AsyncMock(
            return_value={"tool": "search_chapters", "status": "ok", "detail": "完成"}
        )
        with patch(
            "app.services.workspace.run_recovery.execute_workspace_action",
            new=execute,
        ):
            result = asyncio.run(retry_step(db, run.id, original.id))

        assert result["status"] == "ok"
        assert execute.await_args.args[2]["arguments"] == request
    finally:
        db.close()
        engine.dispose()


def test_direct_mcp_step_cannot_escape_lease_through_public_retry() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        run = AssistantRun(project_id="project-1", status="error", phase="tool")
        db.add(run)
        db.commit()
        original = start_run_step(
            db,
            run,
            step_type="write",
            tool="create_character",
            request={"project_id": "project-1", "name": "沈砚"},
            direct_mcp_call_key="direct_mcp:run:1:call",
            emit_operation_signal=False,
        )
        assert original is not None
        original.status = "error"
        db.commit()

        payload = step_payload(original)
        assert payload["can_retry"] is False
        assert "原 lease" in payload["retry_block_reason"]

        execute = AsyncMock()
        with (
            patch(
                "app.services.workspace.run_recovery.execute_workspace_action",
                new=execute,
            ),
            pytest.raises(ValueError, match="原 lease"),
        ):
            asyncio.run(retry_step(db, run.id, original.id))
        execute.assert_not_awaited()
        assert db.query(AssistantRunStep).count() == 1
    finally:
        db.close()
        engine.dispose()
