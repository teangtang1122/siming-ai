"""Retained legacy records must produce actionable, consistent recovery receipts."""
import asyncio
import json
from copy import deepcopy

from app.database.models import CatalogingApplyLog, CatalogingCandidate
from app.services.cataloging.candidate_retry import candidate_recovery_context
from tests.test_cataloging_plan import archive as archive
from tests.test_cataloging_plan import plan_rows, submit


def migrate_envelopes(db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic/versions/300a38_candidate_envelope.py"
    spec = importlib.util.spec_from_file_location("cataloging_envelope_upgrade", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db.connection())):
        module.upgrade()
    db.commit()
    db.expire_all()
    return module


def legacy_outline(archive):
    db, _, _ = archive
    assert not submit(archive, plan_rows(), finalize=False)["data"]["candidate_set_complete"]
    row = db.query(CatalogingCandidate).filter_by(item_type="outline_create").one()
    payload = json.loads(row.raw_payload)
    payload.update(item_type=row.item_type, operation=row.operation, action=row.operation)
    row.raw_payload = json.dumps(payload, ensure_ascii=False)
    db.commit()
    return row


def test_failed_finalization_identifies_the_retained_record_and_is_not_success(archive):
    db, _, run = archive
    row = legacy_outline(archive)
    result = submit(archive, [], finalize=True)
    assert result["status"] == "error"
    data = result["data"]
    issue = next(issue for issue in data["candidate_errors"] if issue.get("candidate_id") == row.id)
    assert issue["rule"] == "additionalProperties"
    assert "action" in issue["message"] and issue["item_type"] == "outline_create"
    assert data["missing_required_items"] == data["recovery_context"]["missing_required_items"]
    assert candidate_recovery_context(db, run, include_payloads=False)["candidate_errors"] == data["candidate_errors"]
    assert run.status == "extracting" and db.query(CatalogingApplyLog).count() == 0


def test_all_invalid_retained_candidates_are_identified_in_one_receipt(archive):
    db, _, _ = archive
    outline = legacy_outline(archive)
    summary = db.query(CatalogingCandidate).filter_by(item_type="chapter_summary").one()
    payload = json.loads(summary.raw_payload)
    payload["unrecognized_field"] = "author content"
    summary.edited_payload = json.dumps(payload, ensure_ascii=False)
    summary.status = "edited"
    db.commit()
    result = submit(archive, [])
    issues = {issue["candidate_id"]: issue for issue in result["data"]["candidate_errors"]}
    assert {outline.id, summary.id} <= issues.keys()
    assert issues[summary.id]["requires_author_action"] is True
    assert issues[outline.id]["requires_author_action"] is False
    assert json.loads(summary.edited_payload)["unrecognized_field"] == "author content"


def test_text_only_completion_is_corrected_in_the_same_agent_conversation(archive, monkeypatch):
    from app.services.cataloging import orchestrator
    db, job, run = archive
    submit(archive, plan_rows(), finalize=False)
    retained_ids = {row.id for row in db.query(CatalogingCandidate).all()}
    seen = []
    async def model(**kwargs):
        index = len(seen)
        seen.append(deepcopy(kwargs))
        if index == 0:
            yield {"type": "content_delta", "delta": "候选齐了，建档完成。"}
        else:
            name, args = ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                                   "candidates": [], "finalize": True})
            yield {"type": "tool_call_delta", "index": 0, "id": f"call-{index}", "name": name,
                   "arguments_delta": json.dumps(args)}
        yield {"type": "done"}
    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    async def execute():
        return [event async for event in orchestrator._extract_run(db, job, run)]
    asyncio.run(execute())
    assert run.status == "awaiting_confirmation" and len(seen) == 2
    feedback = json.loads(seen[-1]["messages"][-1]["content"])
    assert feedback["candidate_set_complete"] is False
    assert feedback["requires_explicit_finalization"] is True
    assert any(message.get("content") == "候选齐了，建档完成。" for message in seen[-1]["messages"])
    assert {row.id for row in db.query(CatalogingCandidate).all()} == retained_ids
    assert db.query(CatalogingApplyLog).count() == 0


def test_text_only_recovery_is_bounded_and_persists_the_actual_blocker(archive, monkeypatch):
    from app.services.cataloging import orchestrator
    db, job, run = archive
    outline = legacy_outline(archive)
    calls = []
    async def model(**kwargs):
        calls.append(deepcopy(kwargs))
        yield {"type": "content_delta", "delta": "只是旧字段警告，已经完成。"}
        yield {"type": "done"}
    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    async def execute():
        return [event async for event in orchestrator._extract_run(db, job, run)]
    asyncio.run(execute())
    assert len(calls) == 3
    assert run.status == "failed" and "additionalProperties" in run.error and outline.id in run.error
    assert sum(message.get("role") == "assistant" for message in json.loads(run.raw_output)) == 3
    assert db.query(CatalogingCandidate).count() == 2 and db.query(CatalogingApplyLog).count() == 0


def test_upgrade_removes_only_redundant_metadata_and_preserves_author_content(archive):
    db, _, run = archive
    outline = legacy_outline(archive)
    raw_before = json.loads(outline.raw_payload)
    edited = {**raw_before, "summary": "作者手工确认的场景说明"}
    outline.edited_payload = json.dumps(edited, ensure_ascii=False)
    outline.status = "approved"
    db.commit()
    identity = outline.id
    module = migrate_envelopes(db)
    outline = db.get(CatalogingCandidate, identity)
    assert outline.status == "approved"
    assert json.loads(outline.edited_payload) == {k: v for k, v in edited.items() if k not in {"action", "item_type", "operation"}}
    first_payload = outline.raw_payload
    migrate_envelopes(db)
    assert outline.raw_payload == first_payload
    result = submit(archive, [], finalize=True)
    assert result["data"]["candidate_set_complete"], result
    assert run.status == "awaiting_confirmation" and db.query(CatalogingApplyLog).count() == 0
    conflicting = json.dumps({"item_type": "character_update", "action": "delete", "target_id": "other", "id": "actual"})
    assert module._migrate_payload(conflicting, item_type="outline_create", operation="create", target_id="actual") == conflicting
    duplicated = json.dumps({"id": "actual", "target_id": "actual", "profile": {"reveal_chapter": "第一章"}})
    migrated = json.loads(module._migrate_payload(duplicated, item_type="character_update", operation="update", target_id="actual"))
    assert migrated == {"id": "actual", "profile": {"reveal_chapter": "第一章"}}


def test_correcting_one_retained_record_keeps_the_other_candidate_ids(archive):
    db, _, _ = archive
    outline = legacy_outline(archive)
    migrate_envelopes(db)
    before = {row.id for row in db.query(CatalogingCandidate).all()}
    invalid = json.loads(outline.raw_payload)
    invalid["character_ids"] = "[]"
    outline.raw_payload = json.dumps(invalid)
    db.commit()
    result = submit(archive, [])
    assert result["status"] == "error" and result["data"]["candidate_errors"][0]["candidate_id"] == outline.id
    result = submit(archive, [plan_rows()[1]])
    assert result["data"]["candidate_set_complete"], result
    assert {row.id for row in db.query(CatalogingCandidate).all()} == before
