"""Generic creation dependency and entity-target generation contracts."""
from __future__ import annotations

import asyncio
import json
import pytest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.novel_creation_entities import ensure_creation_entities, list_creation_entities
from app.services.novel_creation_consistency import (
    creation_dependency_graph,
    validate_creation_consistency,
)
from app.services.novel_creation_stage_execution import _merge_entity_generation
from app.services.novel_creation_runs import create_run
from app.services.novel_creation_claims import creation_idempotency_key
from app.database.models import OperationRun
from app.services.workspace.tools.novel_creation_v2 import run_creation_artifact_generation
from tests.test_novel_creation_workspace_v2 import _db, _ready_session


def test_dependency_graph_covers_artifacts_entities_and_references():
    db = _db()
    session = _ready_session(db)
    graph = creation_dependency_graph(session)
    assert graph["summary"]["artifact_count"] == 8
    assert graph["summary"]["entity_count"] > 0
    assert any(edge["relation"] == "soft" for edge in graph["edges"])
    assert any(edge["relation"] == "impact" for edge in graph["edges"])
    assert any(edge["relation"] == "contains" for edge in graph["edges"])


def test_consistency_report_uses_stable_issue_codes_and_does_not_mutate():
    db = _db()
    session = _ready_session(db)
    before_revision = int(session.revision or 0)
    session.draft_json["stages"]["opening_outline"]["status"] = "stale"
    report = validate_creation_consistency(session)
    assert report["revision"] == before_revision
    assert any(issue["code"] == "stale_artifact" for issue in report["issues"])
    assert int(session.revision or 0) == before_revision


def test_entity_generation_replaces_only_the_selected_row():
    baseline = {
        "characters": [
            {"name": "主角", "goal": "守城"},
            {"name": "反派", "goal": "夺城"},
        ],
        "relationships": [],
    }
    generated = {
        "characters": [{"name": "反派", "goal": "揭开旧案", "secret": "旧案证人"}],
        "relationships": [{"source": "主角", "target": "反派"}],
    }
    context = SimpleNamespace(
        operation="refine",
        working_draft={"artifact_locks": {"characters": ["/characters/1/goal"]}},
        entity_target={
            "id": "entity-2",
            "entity_type": "character",
            "entity_key": "反派",
            "mode": "existing",
        },
    )
    merged, summary = _merge_entity_generation(context, "characters", baseline, generated)
    assert merged["characters"] == [
        {"name": "主角", "goal": "守城"},
        {"name": "反派", "goal": "夺城", "secret": "旧案证人"},
    ]
    assert merged["relationships"] == []
    assert summary["preserved_entity_count"] == 1


def test_entity_generation_can_append_several_rows_without_rewriting_existing_data():
    baseline = {"characters": [{"name": "主角", "goal": "守城"}], "relationships": []}
    generated = {
        "characters": [
            {"name": "主角", "goal": "被模型改写但不应采用"},
            {"name": "谋士", "goal": "寻找旧主"},
            {"name": "刺客", "goal": "偿还人情"},
        ],
        "relationships": [],
    }
    context = SimpleNamespace(
        operation="generate",
        working_draft={},
        entity_target={"entity_type": "character", "mode": "new", "count": 2},
    )

    merged, summary = _merge_entity_generation(context, "characters", baseline, generated)

    assert merged["characters"] == [
        {"name": "主角", "goal": "守城"},
        {"name": "谋士", "goal": "寻找旧主"},
        {"name": "刺客", "goal": "偿还人情"},
    ]
    assert summary["created_entity_count"] == 2
    assert summary["preserved_entity_count"] == 1


def test_entity_generation_uses_all_model_selected_additions_when_count_is_unspecified():
    baseline = {"characters": [{"name": "主角"}], "relationships": []}
    generated = {"characters": [{"name": "主角"}, {"name": "同伴甲"}, {"name": "同伴乙"}], "relationships": []}
    context = SimpleNamespace(
        operation="generate",
        working_draft={},
        entity_target={"entity_type": "character", "mode": "new", "count": None},
    )

    merged, summary = _merge_entity_generation(context, "characters", baseline, generated)

    assert [item["name"] for item in merged["characters"]] == ["主角", "同伴甲", "同伴乙"]
    assert summary["created_entity_count"] == 2


def test_entity_target_generation_runs_end_to_end_without_rewriting_siblings():
    db = _db()
    session = _ready_session(db)
    ensure_creation_entities(session)
    db.commit()
    baseline = deepcopy(session.draft_json["stages"]["characters"]["data"])
    antagonist = next(
        item
        for item in list_creation_entities(session, artifact="characters")
        if item["entity_type"] == "character" and item["data"]["name"] == "周渡"
    )
    generated = {"characters": [deepcopy(baseline["characters"][1])], "relationships": []}
    generated["characters"][0]["goal"] = "揭开旧案并迫使主角公开真相"

    def stream(**_kwargs):
        async def generate():
            yield json.dumps({"data": generated}, ensure_ascii=False)

        return generate()

    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion",
        new=MagicMock(side_effect=stream),
    ):
        result = asyncio.run(run_creation_artifact_generation(db, "", {
            "session_id": session.id,
            "stage": "characters",
            "model": "openai:test",
            "use_model": True,
            "operation": "refine",
            "instruction": "只加强反派目标",
            "entity_id": antagonist["id"],
            "expected_revision": int(session.revision or 0),
        }))

    assert result["status"] == "ok"
    current = session.draft_json["stages"]["characters"]["data"]
    assert current["characters"][0] == baseline["characters"][0]
    assert current["characters"][1]["goal"] == "揭开旧案并迫使主角公开真相"
    assert current["relationships"] == baseline["relationships"]


@pytest.mark.parametrize("target", [{"context_entity_ids": ["not-a-real-entity"]}, {"entity_id": "not-a-real-entity"}])
def test_stage_preparation_failure_finishes_the_durable_run(target):
    db = _db()
    session = _ready_session(db)
    request = {"stage": "opening_outline", "model": "openai:test", "use_model": True, **target}
    run = create_run(db, session, "opening_outline", request)
    db.commit()
    before_revision = session.revision
    with patch("app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion") as model:
        result = asyncio.run(run_creation_artifact_generation(db, "", {
            **request, "session_id": session.id, "_run_id": run.id,
        }))
    assert result["status"] == "error"
    model.assert_not_called()
    db.refresh(run)
    assert run.status == "failed"
    assert run.completed_at is not None
    assert "不存在或已删除" in run.current_message
    assert db.get(OperationRun, run.operation_id).status == "failed"
    assert session.revision == before_revision


@pytest.mark.parametrize("change", [
    {"entity_id": "another-character"},
    {"entity_type": "relationship"},
    {"entity_count": 2},
    {"context_entity_ids": ["corrected-reference"]},
    {"context_artifacts": ["world_style"]},
    {"auto_confirm": True},
])
def test_stage_request_identity_includes_selected_entities_and_context(change):
    request = {"model": "openai:test", "instruction": "完善资料", "use_model": True, "auto_confirm": False}
    common = dict(session_id="session", stage="characters", operation="refine", input_revision=1, input_snapshot_hash="snapshot")
    original = creation_idempotency_key(**common, request=request)
    assert creation_idempotency_key(**common, request={**request, **change}) != original
    assert creation_idempotency_key(**common, request=dict(request)) == original
