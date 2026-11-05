"""Scene-count drift, explicit retirement, and recovery of an old checkpoint."""
import asyncio
import json

import pytest

from app.database.models import CatalogingCandidate
from app.services.cataloging import orchestrator
from app.services.cataloging.candidate_retry import candidate_coverage_error
from app.services.cataloging.candidate_store import create_candidate_from_raw
from app.services.workspace.tools.external_cataloging import save_external_cataloging_candidates
from tests.test_cataloging_candidate_repair import summary_payload
from tests.test_cataloging_character_targets import archive as archive_fixture

archive = archive_fixture


def section(number, summary=None, **extra):
    return {
        "character_ids": [],
        "type": "outline_create", "node_type": "section", "title": f"场景{number}",
        "scene_number": number, "summary": summary or f"事件{number}", "purpose": "核对档案",
        "location": "档案室", "timeline": "本章", "pov_character": "主角",
        "characters": ["主角"], "entry_state": "开始核对", "exit_state": "完成核对",
        "emotional_residue": "平静", "unresolved_actions": [], **extra,
    }


def checkpoint(archive, *, legacy_extra=False):
    db, chapter, _, job, run = archive
    for i, raw in enumerate([
        {"type": "chapter_summary", **summary_payload(scene_count=4)},
        {"character_ids": [], "type": "outline_create", "node_type": "chapter", "title": chapter.title, "summary": "核对档案"},
        *[section(number) for number in range(1, 5)],
    ]):
        result = create_candidate_from_raw(db, job, run, raw, i)
        assert "candidate" in result, result
    extra = None
    if legacy_extra:
        # Reproduce the persisted checkpoint written before the range guard existed.
        extra = CatalogingCandidate(
            job_id=job.id, chapter_run_id=run.id, chapter_id=chapter.id,
            project_id=chapter.project_id, item_type="outline_create", status="pending",
            raw_payload=json.dumps(section(5, "签字发现线索")), sort_order=9,
        )
        db.add(extra)
    run.status = "extracting"
    db.commit()
    return extra


def snapshot(db):
    return [(row.id, row.status, row.raw_payload, row.edited_payload, row.error)
            for row in db.query(CatalogingCandidate).order_by(CatalogingCandidate.id)]


@pytest.mark.parametrize("external", [False, True], ids=["api", "cli-mcp"])
def test_out_of_range_is_rejected_before_staging_and_returns_source_plan(archive, external):
    db, chapter, _, job, run = archive
    checkpoint(archive)
    before = snapshot(db)
    raw = section(5, "签字发现线索")
    if external:
        result = asyncio.run(save_external_cataloging_candidates(db, chapter.project_id, {
            "job_id": job.id, "chapter_id": chapter.id, "candidates": [raw],
        }))
        assert result["status"] == "skipped"
        issue = result["data"]["candidate_errors"][0]
        context = issue["scene_repair"]
    else:
        result = create_candidate_from_raw(db, job, run, raw, 10)
        assert "scene_number=5" in result["error"]
        context = result["scene_repair"]
    assert context["source_scene_count"] == 4
    assert [row["scene_number"] for row in context["source_scenes"]] == [1, 2, 3, 4]
    assert len(context["section_candidates"]) == 4
    assert context["required_repair_type"] is None
    assert snapshot(db) == before


def replacement(db, run):
    from app.services.cataloging.scene_contract import active_scene_candidates

    return {
        "type": "scene_outline_replace",
        "expected_candidate_ids": [row.id for row in active_scene_candidates(db, run)],
        "sections": [section(i, text) for i, text in enumerate(
            ("接电话并核验", "向科长请示", "副馆长批准", "签字发现线索"), 1,
        )],
    }


@pytest.mark.parametrize("external", [False, True], ids=["api", "cli-mcp"])
def test_complete_scene_replacement_is_atomic_and_idempotent(archive, external):
    db, chapter, _, job, run = archive
    extra = checkpoint(archive, legacy_extra=True)
    assert "out_of_range=5" in candidate_coverage_error(db, run)
    raw = replacement(db, run)
    other_ids = {row.id for row in db.query(CatalogingCandidate)
                 if row.id not in raw["expected_candidate_ids"]}

    def submit(*, repeated=False):
        if external:
            result = asyncio.run(save_external_cataloging_candidates(db, chapter.project_id, {
                "job_id": job.id, "chapter_id": chapter.id, "candidates": [raw],
            }))
            assert result["status"] == "ok", result
            if repeated:
                assert result["data"]["duplicates_skipped"] == 1
        else:
            result = create_candidate_from_raw(db, job, run, raw, 10)
            assert result.get("duplicate") if repeated else len(result["candidates"]) == 4

    submit()
    assert extra.status == "rejected"
    assert extra.error.startswith("scene_plan:")
    assert candidate_coverage_error(db, run) == ""
    from app.services.cataloging.scene_contract import scene_repair_context

    assert scene_repair_context(db, run)["required_repair_type"] is None
    active_ids = {row.id for row in db.query(CatalogingCandidate) if row.status != "rejected"}
    assert other_ids <= active_ids
    assert not (active_ids & set(raw["expected_candidate_ids"]))
    before = snapshot(db)
    submit(repeated=True)
    assert snapshot(db) == before


@pytest.mark.parametrize("bad_target", [
    "foreign", "summary", "applied", "edited", "partial", "missing", "omitted", "duplicate",
])
def test_invalid_scene_replacement_never_changes_any_rows(archive, bad_target):
    db, _, _, job, run = archive
    extra = checkpoint(archive, legacy_extra=True)
    raw = replacement(db, run)
    if bad_target == "foreign":
        extra.project_id = "other"
    elif bad_target == "summary":
        raw["expected_candidate_ids"][-1] = db.query(CatalogingCandidate).filter_by(item_type="chapter_summary").one().id
    elif bad_target == "applied":
        extra.status = "applied"
    elif bad_target == "edited":
        extra.edited_payload = extra.raw_payload
    elif bad_target == "partial":
        raw["sections"].pop(2)  # Omitting the approval scene cannot pass as a repair.
    elif bad_target == "omitted":
        raw["expected_candidate_ids"].remove(extra.id)
    elif bad_target == "duplicate":
        raw["sections"][-1]["scene_number"] = 3
    else:
        raw["expected_candidate_ids"][-1] = "missing"
    db.commit()
    before = snapshot(db)
    result = create_candidate_from_raw(db, job, run, raw, 10)
    assert "bad_line" in result, result
    if bad_target == "omitted":
        assert extra.id in result["error"]
        assert extra.id in result["scene_repair"]["expected_candidate_ids"]
    assert snapshot(db) == before


def test_failed_transaction_rolls_back_all_scene_replacements(archive):
    db, _, _, job, run = archive
    checkpoint(archive, legacy_extra=True)
    raw = replacement(db, run)
    before = snapshot(db)
    with pytest.raises(RuntimeError), db.begin_nested():
        result = create_candidate_from_raw(db, job, run, raw, 10)
        assert len(result["candidates"]) == 4
        raise RuntimeError("Simulate a failed surrounding transaction")
    assert snapshot(db) == before


def test_only_renumbering_the_last_scene_cannot_overwrite_the_old_fourth(archive):
    db, _, _, job, run = archive
    checkpoint(archive, legacy_extra=True)
    before = snapshot(db)
    result = create_candidate_from_raw(db, job, run, section(4, "只留下签字，丢掉批准"), 10)
    assert "scene_outline_replace" in result["error"]
    assert snapshot(db) == before






def test_middle_section_failure_rolls_back_the_entire_plan(archive, monkeypatch):
    from app.services.cataloging import candidate_store

    db, _, _, job, run = archive
    checkpoint(archive, legacy_extra=True)
    raw = replacement(db, run)
    before = snapshot(db)
    original = candidate_store.create_candidate_from_raw

    def fail_middle(db, job, run, record, sort_order, **kwargs):
        if record.get("scene_number") == 3:
            return {"bad_line": "test", "error": "simulated later validation failure"}
        return original(db, job, run, record, sort_order, **kwargs)

    monkeypatch.setattr(candidate_store, "create_candidate_from_raw", fail_middle)
    result = original(db, job, run, raw, 10)
    assert "simulated later validation" in result["error"]
    assert snapshot(db) == before


def test_scene_replacement_menu_and_schema_use_only_staged_candidate_ids(archive):
    from app.services.cataloging.agent import _selection_context, _cataloging_step_contract, _execute_cataloging_call
    from app.services.cataloging.scene_contract import active_scene_candidates

    db, chapter, _, job, run = archive
    result = create_candidate_from_raw(db, job, run,
        {"type": "chapter_summary", **summary_payload(scene_count=4)}, 0)
    assert "candidate" in result, result
    assert "scene_outline_replace" not in _selection_context(db, run)[0]
    before = snapshot(db)
    schemas, _ = _cataloging_step_contract(db, run, None)
    offered = {row["function"]["name"]: row for row in schemas}
    rejected = asyncio.run(_execute_cataloging_call(db, job, run, "select_cataloging_candidate_types",
        {"types": ["scene_outline_replace"]}, offered))
    assert rejected["status"] == "error"
    assert rejected["data"]["scene_repair"]["expected_candidate_ids"] == []
    assert snapshot(db) == before
    for number in range(1, 5):
        assert "candidate" in create_candidate_from_raw(db, job, run, section(number), number)
    assert "scene_outline_replace" in _selection_context(db, run)[0]
    schemas, selected = _cataloging_step_contract(db, run, "scene_outline_replace")
    assert selected == "scene_outline_replace"
    save = next(row["function"] for row in schemas
        if row["function"]["name"] == "save_external_cataloging_candidates")
    expected = save["parameters"]["properties"]["candidates"]["items"]["properties"]["expected_candidate_ids"]
    ids = [row.id for row in active_scene_candidates(db, run)]
    assert expected["items"]["enum"] == ids
    assert expected["minItems"] == expected["maxItems"] == len(ids) == 4
    assert expected["uniqueItems"] is True
    assert chapter.outline_node_id not in ids
    before = snapshot(db)
    raw = replacement(db, run)
    raw["expected_candidate_ids"] = ["formal-outline-id"] * 4
    offered = {row["function"]["name"]: row for row in schemas}
    rejected = asyncio.run(_execute_cataloging_call(db, job, run, "save_external_cataloging_candidates",
        {"job_id": job.id, "chapter_id": chapter.id, "candidates": [raw]}, offered))
    assert rejected["status"] == "error"
    assert rejected["data"]["scene_repair"]["expected_candidate_ids"] == ids
    assert snapshot(db) == before
