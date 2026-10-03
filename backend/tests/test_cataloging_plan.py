"""Single Agent decisions, explicit bindings and atomic chapter application."""
import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (Base, Project, Chapter, Character, CatalogingFact,
                                 CatalogingCandidate, CatalogingApplyLog, ChapterSummary, OutlineNode)
from app.services.cataloging.orchestrator import create_cataloging_job
from app.services.cataloging import applier
from app.services.cataloging.plan_contract import validate_plan_references
from app.services.workspace.tools.external_cataloging import save_external_cataloging_candidates
from app.modules.continuity.domain.candidate_contract import validate_candidate_fields
from app.services.workspace.registry import registry
from app.architecture.tool_spec import _validate_exported_schema


@pytest.fixture
def archive(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'plan.db').as_posix()}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(Project(id="p", title="回归作品"))
        db.add(Chapter(id="c", project_id="p", title="旧炉", content="少年走近旧炉。一团红色残影回答他的问题，承认自己正是炉灵。"))
        db.commit()
        job = create_cataloging_job(db, "p", "manual", "test:model", ["c"])
        job.status = "running"
        run = job.chapter_runs[0]
        run.status = "extracting"
        db.commit()
        yield db, job, run
    engine.dispose()


def plan_rows(character=None):
    names = [character.name] if character else []
    ids = [character.id] if character else []
    summary = {"type": "chapter_summary", "summary_text": "少年走近旧炉后遇到一团红色残影，对方回答了少年的提问并确认自己就是此前已有档案的炉灵。本章记录了身份确认过程以及炉灵当前的状态，尚未确认的其他信息保留待后文核实。",
               "scenes": ["旧炉旁的交谈"], "character_bindings": [{"name": character.name, "id": character.id,
                    "decision": "existing", "source_labels": ["红色残影"], "reason": "本章对话确认身份"}] if character else [],
               "worldbuilding_bindings": [],
               "coverage_manifest": {"scene_count": 1, "characters": names, "worldbuilding": [],
                                     "relationships": [], "character_profiles": []},
               "narrative_state": {}, "narrative_review": {"source": "provided", "outcome": "assessed"}}
    rows = [summary, {"type": "outline_create", "title": "旧炉", "node_type": "chapter", "summary": "单场景：旧炉旁的交谈确认身份。", "character_ids": ids}]
    if character:
        rows.extend([{"type": "character_state_update", "id": character.id, "name": character.name, "mental_state": "愿意交谈"},
                     {"type": "chapter_link", "characters": [{"name": character.name, "appearance_type": "出场"}],
                      "worldbuilding_titles": [], "locations": [], "items": [], "events": []}])
    return rows


def submit(archive, rows, finalize=True):
    db, job, run = archive
    return asyncio.run(save_external_cataloging_candidates(db, job.project_id,
                       {"job_id": job.id, "chapter_id": run.chapter_id, "candidates": rows, "finalize": finalize}))


def test_old_anonymous_fact_cannot_veto_current_model_binding(archive):
    db, job, run = archive
    character = Character(project_id="p", name="炉灵")
    db.add(character)
    db.flush()
    db.add(CatalogingFact(project_id="p", job_id=job.id, chapter_run_id=run.id, chapter_id="c",
        fact_type="character_fact", raw_payload=json.dumps({"primary_name": "红色残影", "archive_identity": "anonymous_role"})))
    db.commit()
    result = submit(archive, plan_rows(character))
    assert result["data"]["candidate_set_complete"], result["data"]
    assert db.query(CatalogingApplyLog).count() == 0, "manual confirmation is required"


def test_unbound_narrative_label_is_not_a_character_reference(archive):
    db, _, run = archive
    summary = plan_rows()[0]
    summary["coverage_manifest"]["characters"] = ["红色残影"]
    with pytest.raises(ValueError, match="未绑定"):
        validate_plan_references(db, "p", run, {"item_type": "chapter_summary", "payload": summary})
    summary["coverage_manifest"]["characters"] = []
    validate_plan_references(db, "p", run, {"item_type": "chapter_summary", "payload": summary})


@pytest.mark.parametrize("kind,payload,error", [
    ("character_update", {"id": "test", "profile": {"reveal_chapter": "旧炉"}}, "reveal_chapter"),
    ("character_state_update", {"id": "test", "items_or_assets": []}, "items_or_assets"),
    ("chapter_link", {"importance": "high"}, "importance"),
])
def test_model_field_errors_use_the_same_exported_contract(kind, payload, error):
    with pytest.raises(ValueError, match=error):
        validate_candidate_fields(kind, payload)
    with pytest.raises(ValueError):
        registry.get_spec("save_external_cataloging_candidates").validate_input({
            "job_id": "job", "chapter_id": "chapter", "candidates": [{"type": kind, **payload}]})


def test_failed_candidate_preserves_accepted_plan_for_targeted_correction(archive):
    db, _, _ = archive
    rows = plan_rows()
    bad = {**rows[1], "character_ids": "[]"}
    result = submit(archive, [rows[0], bad])
    assert not result["data"]["candidate_set_complete"]
    assert len(result["data"]["candidate_errors"]) == 1
    summary_id = db.query(CatalogingCandidate).one().id
    result = submit(archive, [rows[1]])
    assert result["data"]["candidate_set_complete"], result
    assert db.query(CatalogingCandidate).filter_by(item_type="chapter_summary").one().id == summary_id


def test_finalization_is_explicit(archive):
    db, _, run = archive
    result = submit(archive, plan_rows(), finalize=False)
    assert not result["data"]["candidate_set_complete"]
    assert run.status == "extracting"
    assert submit(archive, [], finalize=True)["data"]["candidate_set_complete"]


def test_duplicate_sections_in_one_batch_are_rejected_before_staging(archive):
    db, _, _ = archive
    summary = plan_rows()[0]
    summary["scenes"] = ["旧炉旁", "山路上"]
    summary["coverage_manifest"]["scene_count"] = 2
    submit(archive, [summary], finalize=False)
    section = {"type": "outline_create", "node_type": "section", "scene_number": 1,
               "title": "旧炉旁", "summary": "少年与炉灵对话", "character_ids": []}
    result = submit(archive, [section, {**section, "title": "山路上"}], finalize=False)
    assert result["status"] != "ok"
    assert db.query(CatalogingCandidate).filter_by(item_type="outline_create").count() == 0
    assert db.query(CatalogingCandidate).filter_by(item_type="chapter_summary").count() == 1


def test_chapter_application_rolls_back_all_candidates_and_logs(archive, monkeypatch):
    db, job, run = archive
    assert submit(archive, plan_rows())["data"]["candidate_set_complete"]
    db.commit()
    original = applier.apply_candidate
    def fail_second(db, candidate):
        if candidate.item_type == "outline_create":
            raise ValueError("模拟第二项写入失败")
        return original(db, candidate)
    monkeypatch.setattr(applier, "apply_candidate", fail_second)
    events = applier.apply_candidates_for_run(db, job, run)
    db.commit()
    assert len(events) == 1 and events[0]["chapter_rolled_back"]
    assert db.query(ChapterSummary).count() == 0
    assert db.query(OutlineNode).count() == 0
    assert db.query(CatalogingApplyLog).count() == 0
    assert db.query(CatalogingCandidate).filter_by(status="applied").count() == 0
    monkeypatch.setattr(applier, "apply_candidate", original)
    assert all(e["type"] == "candidate_applied" for e in applier.apply_candidates_for_run(db, job, run))
    db.commit()
    assert db.query(ChapterSummary).count() == 1
    assert db.query(OutlineNode).filter_by(node_type="chapter").count() == 1
    assert applier.apply_candidates_for_run(db, job, run) == []
    assert db.query(CatalogingApplyLog).count() == 2


def test_stale_chapter_and_pause_prevent_submission(archive):
    db, job, run = archive
    job.status = "paused"
    db.commit()
    assert submit(archive, plan_rows())["status"] != "ok"
    job.status = "running"
    run.chapter.current_version += 1
    db.commit()
    assert submit(archive, plan_rows())["status"] != "ok"
    assert db.query(CatalogingCandidate).count() == 0


def test_cross_project_binding_is_rejected(archive):
    db, _, run = archive
    db.add(Project(id="other", title="其他作品"))
    person = Character(project_id="other", name="炉灵")
    db.add(person)
    db.commit()
    result = submit(archive, plan_rows(person))
    assert not result["data"]["candidate_set_complete"]
    assert "不属于当前作品" in str(result)


def test_api_native_agent_uses_shared_tools_and_finishes_one_plan(archive, monkeypatch, tmp_path):
    from app.services.cataloging import orchestrator
    from app.services.workspace.tools import external_cataloging
    db, job, run = archive
    chapter_file = tmp_path / "chapter.txt"
    chapter_file.write_text(run.chapter.content, encoding="utf-8")
    monkeypatch.setattr(external_cataloging, "ensure_chapter_mirror", lambda *a, **kw: (tmp_path, chapter_file))
    summary, outline = plan_rows()
    script = [("get_next_external_cataloging_chapter", {"job_id": job.id, "include_prompt_pack": False}),
              ("read_cataloging_archive", {"kind": "character"}),
              ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": "c", "candidates": [summary]}),
              ("select_cataloging_candidate_types", {"types": ["outline_create_chapter"]}),
              ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": "c", "candidates": [outline], "finalize": True})]
    seen = []
    async def model(**kwargs):
        index = len(seen)
        seen.append(kwargs)
        name, args = script[index]
        yield {"type": "tool_call_delta", "index": 0, "id": f"native-{index}", "name": name,
               "arguments_delta": json.dumps(args, ensure_ascii=False)}
        yield {"type": "done", "reasoning_content": "", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    async def check():
        return [event async for event in orchestrator._extract_run(db, job, run)]
    events = asyncio.run(check())
    assert run.status == "awaiting_confirmation", events
    assert len(seen) == 5
    assert {tool["function"]["name"] for tool in seen[0]["tools"]} == {
        "get_next_external_cataloging_chapter", "read_cataloging_archive",
        "save_external_cataloging_candidates", "list_cataloging_candidates",
    }
    assert seen[0]["tools"] == seen[2]["tools"]
    assert any(tool["function"]["name"] == "select_cataloging_candidate_types" for tool in seen[3]["tools"])
    assert all("save_external_cataloging_facts" not in str(request["tools"]) for request in seen)
    assert any("read_cataloging_archive" in event for event in events)
    assert db.query(CatalogingApplyLog).count() == 0
    assert db.query(CatalogingFact).count() == 0


def test_native_agent_requires_complete_summary_before_exposing_other_candidate_shapes(archive, monkeypatch, tmp_path):
    from app.services.cataloging import orchestrator
    from app.services.workspace.tools import external_cataloging

    db, job, run = archive
    chapter_file = tmp_path / "chapter.txt"
    chapter_file.write_text(run.chapter.content, encoding="utf-8")
    monkeypatch.setattr(external_cataloging, "ensure_chapter_mirror", lambda *a, **kw: (tmp_path, chapter_file))
    summary, outline = plan_rows()
    script = [
        ("get_next_external_cataloging_chapter", {"job_id": job.id, "include_prompt_pack": False}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                  "candidates": [{"type": "chapter_summary", "title": "旧炉"}]}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                  "candidates": [summary]}),
        ("select_cataloging_candidate_types", {"types": ["outline_create_chapter"]}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                  "candidates": [{"type": "outline_create", "sections": []}]}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                  "candidates": [outline], "finalize": True}),
    ]
    seen = []

    async def model(**kwargs):
        index = len(seen)
        seen.append(kwargs)
        name, args = script[index]
        yield {"type": "tool_call_delta", "index": 0, "id": f"summary-stage-{index}", "name": name,
               "arguments_delta": json.dumps(args, ensure_ascii=False)}
        yield {"type": "done"}

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    asyncio.run(_collect_cataloging_extract(db, job, run, orchestrator))

    def offered_candidate(index):
        save = next(tool for tool in seen[index]["tools"]
                    if tool["function"]["name"] == "save_external_cataloging_candidates")
        return save["function"]["parameters"]["properties"]["candidates"]["items"]

    for index in (0, 1, 2):
        offered = offered_candidate(index)
        assert offered["properties"]["type"]["enum"] == ["chapter_summary"]
        assert {"summary_text", "coverage_manifest", "scenes", "character_bindings",
                "worldbuilding_bindings", "narrative_state", "narrative_review"} <= set(offered["required"])
        assert "title" not in offered["properties"]
    selection_save = next(tool for tool in seen[3]["tools"]
                          if tool["function"]["name"] == "save_external_cataloging_candidates")
    assert selection_save["function"]["parameters"]["properties"]["candidates"]["maxItems"] == 0
    assert offered_candidate(4)["properties"]["type"]["enum"] == ["outline_create"]
    assert offered_candidate(5)["properties"]["type"]["enum"] == ["outline_create"]
    assert run.status == "awaiting_confirmation"
    assert db.query(CatalogingCandidate).count() == 2
    assert "summary_text" in run.raw_output


async def _collect_cataloging_extract(db, job, run, orchestrator):
    return [event async for event in orchestrator._extract_run(db, job, run)]


def test_scene_type_stays_open_across_partial_batches_until_model_switches(archive, monkeypatch, tmp_path):
    from app.services.cataloging import orchestrator
    from app.services.workspace.tools import external_cataloging

    db, job, run = archive
    chapter_file = tmp_path / "chapter.txt"
    chapter_file.write_text(run.chapter.content, encoding="utf-8")
    monkeypatch.setattr(external_cataloging, "ensure_chapter_mirror", lambda *a, **kw: (tmp_path, chapter_file))
    chapter_outline = OutlineNode(id=str(uuid4()), project_id="p", node_type="chapter", title="旧炉")
    sections = [OutlineNode(id=str(uuid4()), project_id="p", node_type="section",
                            title=f"场景{i}", parent_id=chapter_outline.id) for i in range(1, 5)]
    db.add_all([chapter_outline, *sections])
    run.chapter.outline_node_id = chapter_outline.id
    db.commit()
    summary, outline = plan_rows()
    summary["scenes"] = [f"旧炉旁的交谈{i}" for i in range(1, 5)]
    summary["coverage_manifest"]["scene_count"] = 4
    outline.update(type="outline_update", id=chapter_outline.id)
    rows = [{
        "type": "outline_update", "id": section.id, "title": section.title,
        "node_type": "section", "scene_number": i, "parent_id": chapter_outline.id,
        "summary": f"旧炉旁的交谈{i}", "character_ids": [],
        "purpose": "确认身份", "location": "旧炉旁", "timeline": "当天",
        "pov_character": "", "characters": [], "entry_state": "正在提问",
        "exit_state": "获得回答", "emotional_residue": "仍有疑问", "unresolved_actions": [],
    } for i, section in enumerate(sections, 1)]
    script = [
        ("get_next_external_cataloging_chapter", {"job_id": job.id}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                  "candidates": [summary], "finalize": False}),
        ("select_cataloging_candidate_types", {"types": ["outline_update_chapter"]}),
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                  "candidates": [outline], "finalize": False}),
        ("select_cataloging_candidate_types", {"types": ["outline_update_section"]}),
        *[("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                    "candidates": [row], "finalize": False}) for row in rows],
        ("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                  "candidates": [], "finalize": True}),
    ]
    seen = []

    async def model(**kwargs):
        index = len(seen)
        seen.append(kwargs)
        name, args = script[index]
        yield {"type": "tool_call_delta", "index": 0, "id": f"partial-scene-{index}",
               "name": name, "arguments_delta": json.dumps(args, ensure_ascii=False)}
        yield {"type": "done"}

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    events = asyncio.run(_collect_cataloging_extract(db, job, run, orchestrator))
    assert run.status == "awaiting_confirmation", events
    for request in seen[5:]:
        save = next(tool for tool in request["tools"]
                    if tool["function"]["name"] == "save_external_cataloging_candidates")
        record = save["function"]["parameters"]["properties"]["candidates"]["items"]
        assert record["properties"]["node_type"]["enum"] == ["section"]
        assert record["properties"]["id"]["enum"] == [section.id for section in sections]
    assert db.query(CatalogingCandidate).count() == 6
    assert db.query(CatalogingApplyLog).count() == 0


def test_unopened_cataloging_batch_executes_nothing_and_same_model_recovers(archive, monkeypatch, tmp_path):
    from app.services.cataloging import orchestrator
    from app.services.workspace.tools import external_cataloging

    db, job, run = archive
    chapter_file = tmp_path / "chapter.txt"
    chapter_file.write_text(run.chapter.content, encoding="utf-8")
    monkeypatch.setattr(external_cataloging, "ensure_chapter_mirror", lambda *a, **kw: (tmp_path, chapter_file))
    original_handler = registry.get_handler

    def resolve_handler(name):
        if name == "read_cataloging_archive":
            pytest.fail("a valid read prefix in a denied batch must not execute")
        return original_handler(name)

    monkeypatch.setattr(registry, "get_handler", resolve_handler)
    summary, outline = plan_rows()
    script = [
        [("invented_cataloging_save", {}), ("read_cataloging_archive", {"kind": "character"})],
        [("get_next_external_cataloging_chapter", {"job_id": job.id})],
        [("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                   "candidates": [summary], "finalize": False})],
        [("select_cataloging_candidate_types", {"types": ["outline_create_chapter"]})],
        [("save_external_cataloging_candidates", {"job_id": job.id, "chapter_id": run.chapter_id,
                                                   "candidates": [outline], "finalize": True})],
    ]
    seen = []

    async def model(**kwargs):
        index = len(seen)
        seen.append({**kwargs, "messages": json.loads(json.dumps(kwargs["messages"]))})
        for slot, (name, args) in enumerate(script[index]):
            yield {"type": "tool_call_delta", "index": slot, "id": f"denied-{index}-{slot}",
                   "name": name, "arguments_delta": json.dumps(args)}
        yield {"type": "done"}

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    events = asyncio.run(_collect_cataloging_extract(db, job, run, orchestrator))
    assert run.status == "awaiting_confirmation", events
    receipts = [json.loads(message["content"]) for message in seen[1]["messages"][-2:]]
    assert len(receipts) == 2
    assert all(not receipt["data"]["executed"] for receipt in receipts)
    assert all(receipt["data"]["unavailable_tools"] == ["invented_cataloging_save"] for receipt in receipts)
    assert "save_external_cataloging_candidates" in receipts[0]["data"]["available_tools"]
    assert "set_tool_categories" not in receipts[0]["detail"]
    assert db.query(CatalogingCandidate).count() == 2


def test_unopened_cataloging_recovery_stops_after_three_denied_batches(archive, monkeypatch):
    from app.services.cataloging import orchestrator

    db, job, run = archive
    submit(archive, [plan_rows()[0]], finalize=False)
    summary_id = db.query(CatalogingCandidate).one().id
    calls = []

    async def model(**kwargs):
        calls.append(kwargs)
        yield {"type": "tool_call_delta", "index": 0, "id": f"unopened-{len(calls)}",
               "name": "invented_cataloging_save", "arguments_delta": "{}"}
        yield {"type": "done"}

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    asyncio.run(_collect_cataloging_extract(db, job, run, orchestrator))
    assert run.status == "failed"
    assert job.status == "paused_on_failure"
    assert "invented_cataloging_save" in run.error
    assert len(calls) == 3
    assert db.query(CatalogingCandidate).one().id == summary_id
    transcript = json.loads(run.raw_output)
    denied = [json.loads(message["content"]) for message in transcript if message["role"] == "tool"]
    assert len(denied) == 3
    assert denied[-1]["data"]["retryable"] is False


def test_section_write_schema_scopes_ids_and_requires_scene_number(archive):
    from app.services.cataloging.agent import _selection_context, cataloging_agent_tool_schemas

    db, _, run = archive
    chapter_outline = OutlineNode(id=str(uuid4()), project_id="p", node_type="chapter", title="旧炉")
    section_outline = OutlineNode(id=str(uuid4()), project_id="p", node_type="section", title="旧炉旁",
                                  parent_id=chapter_outline.id)
    other_chapter_outline = OutlineNode(id=str(uuid4()), project_id="p", node_type="chapter", title="另一章")
    foreign_outline = OutlineNode(id=str(uuid4()), project_id="p", node_type="section", title="另一章",
                                  parent_id=other_chapter_outline.id)
    db.add_all([chapter_outline, section_outline, other_chapter_outline, foreign_outline])
    run.chapter.outline_node_id = chapter_outline.id
    db.commit()
    submit(archive, [plan_rows()[0]], finalize=False)

    choices, targets, scene_count, limits, link_names = _selection_context(db, run)
    assert "outline_update_chapter" in choices and "outline_create_chapter" not in choices
    assert targets["outline_update_section"] == [section_outline.id]
    save = next(tool for tool in cataloging_agent_tool_schemas(
        summary_required=False, selected_type="outline_update_section",
        selection_types=choices, target_ids=targets, scene_count=scene_count,
        candidate_limits=limits, link_names=link_names,
    ) if tool["function"]["name"] == "save_external_cataloging_candidates")
    parameters = save["function"]["parameters"]
    section = parameters["properties"]["candidates"]["items"]
    assert "scene_number" in section["required"]
    assert section["properties"]["scene_number"]["maximum"] == 1
    assert parameters["properties"]["candidates"]["maxItems"] == 1
    assert section["properties"]["id"]["enum"] == [section_outline.id]
    for invalid in (
        {"type": "outline_update", "node_type": "section", "id": section_outline.id,
         "title": "旧炉旁", "summary": "已发生", "character_ids": []},
        {"type": "outline_update", "node_type": "section", "id": foreign_outline.id,
         "title": "旧炉旁", "summary": "已发生", "character_ids": [], "scene_number": 1},
    ):
        with pytest.raises(ValueError):
            _validate_exported_schema(parameters, {
                "job_id": "job", "chapter_id": "c", "candidates": [invalid],
            })


def test_worldbuilding_write_schema_requires_content_and_limits_bound_ids():
    from app.services.cataloging.agent import cataloging_agent_tool_schemas

    save = next(tool for tool in cataloging_agent_tool_schemas(
        summary_required=False, selected_type="worldbuilding_update",
        target_ids={"worldbuilding_update": ["world-1", "world-2", "world-3"]},
        candidate_limits={"worldbuilding_update": 3},
    ) if tool["function"]["name"] == "save_external_cataloging_candidates")
    parameters = save["function"]["parameters"]
    batch = parameters["properties"]["candidates"]
    record = batch["items"]
    assert batch["maxItems"] == 3
    assert record["properties"]["id"]["enum"] == ["world-1", "world-2", "world-3"]
    assert "content" in record["required"]
    for invalid in (
        [{"type": "worldbuilding_update", "id": "world-1", "title": "修真等级"}],
        [{"type": "worldbuilding_update", "id": "unbound", "title": "修真等级", "content": "已确认"}],
        [{"type": "worldbuilding_update", "id": "world-1", "title": "修真等级", "content": "已确认"}] * 4,
    ):
        with pytest.raises(ValueError):
            _validate_exported_schema(parameters, {
                "job_id": "job", "chapter_id": "c", "candidates": invalid,
            })


def test_chapter_link_schema_requires_all_declared_bindings():
    from app.services.cataloging.agent import cataloging_agent_tool_schemas

    schemas = cataloging_agent_tool_schemas(
        summary_required=False, selected_type="chapter_link",
        candidate_limits={"chapter_link": 1},
        link_names={"characters": ["李玄", "苏星河"], "worldbuilding": ["星纹", "宗门", "灵气"]},
    )
    parameters = next(tool for tool in schemas if tool["function"]["name"] ==
                      "save_external_cataloging_candidates")["function"]["parameters"]
    batch = parameters["properties"]["candidates"]
    link = batch["items"]
    assert batch["maxItems"] == 1
    assert {"characters", "worldbuilding_titles"} <= set(link["required"])
    assert link["properties"]["characters"]["minItems"] == 2
    assert link["properties"]["worldbuilding_titles"]["minItems"] == 3
    with pytest.raises(ValueError):
        _validate_exported_schema(parameters, {
            "job_id": "job", "chapter_id": "c",
            "candidates": [{"type": "chapter_link", "chapter_link_mode": "replace"}],
        })


def test_type_selection_does_not_reset_repeated_write_failures(archive, monkeypatch):
    from app.services.cataloging import orchestrator

    db, job, run = archive
    submit(archive, [plan_rows()[0]], finalize=False)
    calls = []

    async def model(**kwargs):
        index = len(calls)
        calls.append(kwargs)
        if index % 2 == 0:
            name, args = "select_cataloging_candidate_types", {"types": ["outline_create_chapter"]}
        else:
            name, args = "save_external_cataloging_candidates", {
                "job_id": job.id, "chapter_id": run.chapter_id,
                "candidates": [{"type": "outline_create", "node_type": "chapter"}],
            }
        yield {"type": "tool_call_delta", "index": 0, "id": f"repeat-{index}", "name": name,
               "arguments_delta": json.dumps(args)}
        yield {"type": "done"}

    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    asyncio.run(_collect_cataloging_extract(db, job, run, orchestrator))
    assert run.status == "failed"
    assert len(calls) == 6
    assert db.query(CatalogingCandidate).count() == 1


def test_late_api_result_cannot_write_after_pause(archive, monkeypatch):
    from app.services.cataloging import orchestrator
    db, job, run = archive
    async def model(**kwargs):
        job.status = "paused"
        db.commit()
        yield {"type": "tool_call_delta", "index": 0, "id": "late", "name": "get_next_external_cataloging_chapter",
               "arguments_delta": json.dumps({"job_id": job.id})}
    monkeypatch.setattr(orchestrator.LLMGateway, "stream_chat_completion_with_tools", model)
    async def check():
        return [event async for event in orchestrator._extract_run(db, job, run)]
    asyncio.run(check())
    assert db.query(CatalogingCandidate).count() == 0
    assert job.status == "paused"
