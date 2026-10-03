"""Native correction, explicit identities, archive paging and upgrade boundaries."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database.models import (
    Character, CatalogingCandidate, CatalogingApplyLog, WorldbuildingEntry, Project,
    WorldbuildingTimeline, OutlineNode, Chapter,
)
from app.services.cataloging import applier, orchestrator
from app.services.cataloging.archive_reader import read_cataloging_archive
from app.services.workspace.tools.external_cataloging import save_external_cataloging_candidates
from tests.test_cataloging_plan import archive, plan_rows, submit  # noqa: F401


def test_native_agent_repairs_only_invalid_fields_in_same_conversation(archive, monkeypatch):
    db, job, run = archive
    rows = plan_rows()
    calls = [
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
            "candidates": [rows[0]], "finalize": False}),
        ("select_cataloging_candidate_types", {"types": ["outline_create_chapter"]}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
            "candidates": [rows[1]], "finalize": False}),
        ("select_cataloging_candidate_types", {"types": ["chapter_link"]}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
            "candidates": [{"type": "chapter_link", "characters": [], "worldbuilding_titles": [],
                            "importance": "high"}], "finalize": False}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
            "candidates": [{"type": "chapter_link", "characters": [], "worldbuilding_titles": [],
                            "importance": "major"}], "finalize": True}),
    ]
    seen, accepted_ids = [], []
    async def model(**kwargs):
        import json
        step = len(seen)
        seen.append(deepcopy(kwargs["messages"]))
        if step >= 2:
            accepted_ids.append(db.query(CatalogingCandidate).filter_by(item_type="chapter_summary").one().id)
        name, args = calls[step]
        yield {"type": "tool_call_delta", "index": 0, "id": f"call-{step}", "name": name,
               "arguments_delta": json.dumps(args)}
        yield {"type": "done"}
    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    async def execute():
        return [event async for event in orchestrator._extract_run(db, job, run)]
    asyncio.run(execute())
    assert run.status == "awaiting_confirmation", (run.error, run.raw_output)
    assert len(calls) == len(seen)
    assert len(set(accepted_ids)) == 1
    assert "importance" in seen[-1][-1]["content"]
    assert "enum" in seen[-1][-1]["content"]
    assert seen[-1][:len(seen[-2])] == seen[-2]
    assert db.query(CatalogingApplyLog).count() == 0


def test_new_character_state_uses_the_plan_client_id(archive):
    db, job, run = archive
    rows = plan_rows()
    identity = str(uuid4())
    rows[0]["character_bindings"] = [{"name": "新人", "id": identity, "decision": "new", "reason": "正文确认姓名"}]
    rows[0]["coverage_manifest"].update(characters=["新人"], character_profiles=["新人"])
    rows += [{"type": "character_create", "client_id": identity, "name": "新人", "background": "新到的访客"},
             {"type": "character_state_update", "id": identity, "name": "新人", "mental_state": "平静"},
             {"type": "chapter_link", "characters": [{"name": "新人", "appearance_type": "出场"}]}]
    result = submit(archive, rows)
    assert result["data"]["candidate_set_complete"], result["data"]["missing_required_items"]
    events = applier.apply_candidates_for_run(db, job, run)
    assert all(event["type"] == "candidate_applied" for event in events), events
    assert db.get(Character, identity).mental_state == "平静"


def test_plan_id_wins_when_two_archive_records_have_same_name(archive):
    db, job, run = archive
    a, b = Character(project_id=job.project_id, name="同名"), Character(project_id=job.project_id, name="同名")
    db.add_all([a, b]); db.commit()
    result = submit(archive, plan_rows(b))
    assert result["data"]["candidate_set_complete"], result
    events = applier.apply_candidates_for_run(db, job, run)
    assert all(event["type"] == "candidate_applied" for event in events), events
    assert a.mental_state is None and b.mental_state == "愿意交谈"


@pytest.mark.parametrize("extra", [{"character_names": ["unbound"]}, {"_cataloging_target_id": "other"}])
def test_edited_candidate_cannot_bypass_binding_schema(archive, extra):
    import json
    db, job, run = archive
    assert submit(archive, plan_rows())["data"]["candidate_set_complete"]
    db.add(CatalogingCandidate(job_id=job.id, chapter_run_id=run.id, project_id=job.project_id,
        chapter_id=run.chapter_id, item_type="chapter_link", raw_payload="{}", edited_payload=json.dumps(extra)))
    db.commit()
    events = applier.apply_candidates_for_run(db, job, run)
    assert events[0]["chapter_rolled_back"]
    assert db.query(CatalogingApplyLog).count() == 0


def test_explicit_retraction_cannot_touch_author_edits_or_other_runs(archive):
    db, job, run = archive
    submit(archive, plan_rows(), finalize=False)
    summary = db.query(CatalogingCandidate).filter_by(item_type="chapter_summary").one()
    summary.edited_payload = summary.raw_payload
    db.commit()
    result = asyncio.run(save_external_cataloging_candidates(db, job.project_id, {
        "job_id": job.id, "chapter_id": run.chapter_id, "candidates": [], "reject_candidate_ids": [summary.id]}))
    assert result["status"] != "ok"
    assert summary.status == "pending"


def test_archive_reader_pages_and_preserves_exact_fields(archive):
    db, job, _ = archive
    people = [Character(project_id=job.project_id, name=f"人{i}", background="  精确文本。\n" + "长文" * 5000) for i in range(5)]
    db.add_all(people)
    db.add(Project(id="foreign", title="其他作品"))
    outsider = Character(project_id="foreign", name="外部")
    retired = WorldbuildingEntry(project_id=job.project_id, title="旧设定", dimension="culture", content="旧内容", status="superseded")
    db.add_all([outsider, retired]); db.commit()
    args = {"kind": "character", "limit": 2}
    found = []
    while args:
        result = asyncio.run(read_cataloging_archive(db, job.project_id, args))["data"]
        found += [item["id"] for item in result["items"]]
        args = result["next_arguments"]
    assert len(found) == 5 and len(set(found)) == 5
    result = asyncio.run(read_cataloging_archive(db, job.project_id, {"kind": "character", "ids": [people[0].id]}))
    assert result["data"]["items"][0]["background"] == people[0].background
    for kind, identity in (("character", outsider.id), ("worldbuilding", retired.id)):
        with pytest.raises(ValueError):
            asyncio.run(read_cataloging_archive(db, job.project_id, {"kind": kind, "ids": [identity]}))


def test_fts_detection_never_commits_its_callers_writes(archive, monkeypatch):
    from app.services.rag import indexer
    db, job, _ = archive
    monkeypatch.setattr(indexer, "_fts5_available", None)
    with db.begin_nested() as chapter_transaction:
        db.execute(text("UPDATE projects SET title='uncommitted' WHERE id=:id"), {"id": job.project_id})
        indexer.detect_fts5_available(db)
        chapter_transaction.rollback()
    db.expire_all()
    assert db.get(Project, job.project_id).title == "回归作品"


def test_new_world_timeline_is_bound_to_the_same_staged_create(archive):
    db, job, run = archive
    identity = str(uuid4())
    rows = plan_rows()
    rows[0]["worldbuilding_bindings"] = [
        {"name": "旧炉", "id": identity, "decision": "new", "reason": "本章首次明确其设定"}]
    rows[0]["coverage_manifest"]["worldbuilding"] = ["旧炉"]
    rows.extend([
        {"type": "worldbuilding_create", "client_id": identity, "title": "旧炉",
         "dimension": "culture", "content": "炉中能够居住灵体。"},
        {"type": "worldbuilding_timeline", "id": identity, "title": "旧炉",
         "event_description": "炉中的灵体开始与来访者交谈。"},
        {"type": "chapter_link", "worldbuilding_titles": ["旧炉"]},
    ])
    result = submit(archive, rows)
    assert result["data"]["candidate_set_complete"], result
    events = applier.apply_candidates_for_run(db, job, run)
    assert all(event["type"] == "candidate_applied" for event in events), events
    assert db.get(WorldbuildingEntry, identity).title == "旧炉"
    assert db.query(WorldbuildingTimeline).one().entry_id == identity


@pytest.mark.parametrize("error_case", ["foreign", "other_chapter", "wrong_type", "title_as_parent"])
def test_outline_ids_are_validated_before_staging(archive, error_case):
    db, job, run = archive
    db.add(Project(id="foreign", title="另一作品"))
    owned = OutlineNode(project_id=job.project_id, node_type="chapter", title="本章计划")
    foreign = OutlineNode(project_id="foreign", node_type="chapter", title="外部计划")
    other = OutlineNode(project_id=job.project_id, node_type="chapter", title="其他章计划")
    volume = OutlineNode(project_id=job.project_id, node_type="volume", title="分卷标题")
    db.add_all([owned, foreign, other, volume]); db.flush()
    run.chapter.outline_node_id = owned.id
    db.add(Chapter(project_id=job.project_id, title="另一章", outline_node_id=other.id))
    db.commit()
    rows = plan_rows()
    rows[1].update(type="outline_update", id=owned.id)
    if error_case == "title_as_parent":
        rows[1]["parent_id"] = volume.title
    else:
        rows[1]["id"] = {"foreign": foreign.id, "other_chapter": other.id, "wrong_type": volume.id}[error_case]
    result = submit(archive, rows)
    assert not result["data"]["candidate_set_complete"], result
    assert result["data"]["candidate_errors"], result
    assert db.query(CatalogingCandidate).filter(CatalogingCandidate.item_type.like("outline_%")).count() == 0
    assert run.chapter.outline_node_id == owned.id
    assert other.title == "其他章计划" and owned.title == "本章计划"


def test_plan_can_explicitly_revise_scene_count_without_frozen_fact_stage(archive):
    import json
    db, _, _ = archive
    summary = plan_rows()[0]
    summary["scenes"] = ["第一次交谈", "第二次交谈"]
    summary["coverage_manifest"]["scene_count"] = 2
    submit(archive, [summary], finalize=False)
    row = db.query(CatalogingCandidate).one()
    prior = row.raw_payload
    correction = plan_rows()[0]
    result = submit(archive, [correction], finalize=False)
    assert result["data"]["candidate_errors"], result
    assert row.raw_payload == prior
    correction["coverage_manifest_mode"] = "replace"
    result = submit(archive, [correction, plan_rows()[1]])
    assert result["data"]["candidate_set_complete"], result
    assert json.loads(row.raw_payload)["coverage_manifest"]["scene_count"] == 1
    assert db.query(CatalogingCandidate).filter_by(item_type="chapter_summary").one().id == row.id


def test_incompatible_old_candidate_has_an_explicit_recovery_receipt(archive):
    db, job, run = archive
    submit(archive, [plan_rows()[0]], finalize=False)
    legacy = CatalogingCandidate(job_id=job.id, chapter_run_id=run.id, project_id=job.project_id,
        chapter_id=run.chapter_id, item_type="outline_create", raw_payload='{"title":"旧计划"}', status="pending")
    db.add(legacy); db.commit()
    result = submit(archive, [plan_rows()[1]])
    issue = result["data"]["candidate_errors"][0]
    assert legacy.id in issue["message"] and "reject_candidate_ids" in issue["message"]
    result = asyncio.run(save_external_cataloging_candidates(db, job.project_id, {
        "job_id": job.id, "chapter_id": run.chapter_id, "candidates": [plan_rows()[1]],
        "reject_candidate_ids": [legacy.id], "finalize": True,
    }))
    assert result["data"]["candidate_set_complete"], result
    assert legacy.status == "rejected"
