"""Opening bodies and parents survive generation, author writes and materialization."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.database.models import OutlineNode, Project
from app.modules.creation.domain.generation_contract import CreationGenerationError
from app.modules.creation.domain.opening_outline_contract import (
    normalize_opening_outline,
    validate_opening_outline,
)
from app.services.novel_creation_entities import creation_volume_index
from app.services.novel_creation_workspace import save_stage
from app.services.workspace.tools import novel_creation_v2
from app.services.workspace.tools.novel_creation import finalize_creation_session
from tests.test_novel_creation_workspace_v2 import _db, _ready_session

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "contracts/fixtures/creation_opening_outline.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", FIXTURE["invalid_cases"], ids=lambda case: case["name"])
def test_shared_contract_rejects_empty_bodies_and_invalid_links(case):
    data = deepcopy(FIXTURE["data"])
    data[case["field"]][case["index"]][case["key"]] = case["value"]
    with pytest.raises(CreationGenerationError) as caught:
        validate_opening_outline(data, volume_index=FIXTURE["volume_index"])
    assert caught.value.reason == case["reason"]
    assert caught.value.path == f"$.data.{case['field']}[{case['index']}].{case['key']}"


def _opening_for_session(session):
    macro = deepcopy(session.draft_json["stages"]["macro_outline"]["data"])
    macro["volumes"] = [
        {key: value for key, value in row.items() if key != "id"} for row in FIXTURE["volume_index"]
    ]
    save_stage(session, "macro_outline", macro, confirm=True)
    ids = creation_volume_index(session)
    data = deepcopy(FIXTURE["data"])
    mapping = {
        source["id"]: target["id"]
        for source, target in zip(FIXTURE["volume_index"], ids, strict=True)
    }
    for chapter in data["chapters"]:
        chapter["volume_id"] = mapping[chapter["volume_id"]]
    return data


@pytest.mark.parametrize("opaque_ids", [False, True])
def test_materialization_preserves_bodies_metadata_and_ids_after_volumes_reorder(opaque_ids):
    with _db() as db:
        session = _ready_session(db)
        opening = _opening_for_session(session)
        if opaque_ids:
            ids = {
                row["client_id"]: "chapter" + (" " * index)
                for index, row in enumerate(opening["chapters"])
            }
            for chapter in opening["chapters"]:
                chapter["client_id"] = ids[chapter["client_id"]]
            for section in opening["sections"]:
                section["parent_client_id"] = ids[section["parent_client_id"]]
        macro = deepcopy(session.draft_json["stages"]["macro_outline"]["data"])
        macro["volumes"].reverse()
        save_stage(session, "macro_outline", macro, confirm=True)
        save_stage(session, "opening_outline", opening, confirm=True)
        db.commit()
        result = asyncio.run(finalize_creation_session(db, "", {"session_id": session.id}))
        assert result["status"] == "ok", result
        nodes = db.query(OutlineNode).filter_by(project_id=result["data"]["project_id"]).all()
        volumes = {row.title: row.id for row in nodes if row.node_type == "volume"}
        chapters = sorted(
            (row for row in nodes if row.node_type == "chapter"), key=lambda row: row.sort_order
        )
        assert [row.parent_id for row in chapters] == [
            volumes["卷一"],
            volumes["卷一"],
            volumes["卷二"],
        ]
        scenes = [row for row in nodes if row.node_type == "section"]
        assert len(scenes) == 9
        assert all(row.summary and row.planned_summary == row.summary for row in chapters + scenes)
        for row, source in zip(chapters, opening["chapters"], strict=True):
            assert row.summary == source["summary"]
            assert row.metadata_json["key_events"] == source["key_events"]
            assert row.metadata_json["chapter_hook"] == source["chapter_hook"]
            assert sum(scene.parent_id == row.id for scene in scenes) == 3


@pytest.mark.parametrize("field", ["summary", "volume_id"])
def test_invalid_save_and_existing_invalid_confirmation_do_not_write(field):
    with _db() as db:
        session = _ready_session(db)
        data = deepcopy(session.draft_json["stages"]["opening_outline"]["data"])
        before = deepcopy(session.draft_json)
        revision = session.revision
        data["chapters"][0][field] = ""
        with pytest.raises(CreationGenerationError):
            save_stage(session, "opening_outline", data, confirm=True)
        assert session.draft_json == before
        assert session.revision == revision
        # Simulate a confirmed snapshot saved by an older app, then try confirmation/finalization.
        before["stages"]["opening_outline"]["data"] = data
        session.draft_json = before
        db.commit()
        confirmation = asyncio.run(
            novel_creation_v2.save_creation_artifact(
                db,
                "",
                {
                    "session_id": session.id,
                    "stage": "opening_outline",
                    "data": data,
                    "confirm": True,
                },
            )
        )
        assert confirmation["status"] == "error"
        result = asyncio.run(finalize_creation_session(db, "", {"session_id": session.id}))
        assert result["status"] == "error"
        assert result["data"]["path"] == f"$.data.chapters[0].{field}"
        assert db.query(Project).count() == 0
        assert db.query(OutlineNode).count() == 0
        assert session.revision == revision


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_model_repairs_body_and_explicit_volume_reference_or_writes_nothing(
    monkeypatch, repair_succeeds
):
    with _db() as db:
        session = _ready_session(db)
        valid = _opening_for_session(session)
        db.commit()
        invalid = deepcopy(valid)
        invalid["chapters"][0]["summary"] = ""
        invalid["chapters"][0].pop("volume_id")
        before = deepcopy(session.draft_json)
        revision = session.revision
        calls = []

        def stream(**kwargs):
            calls.append(kwargs)
            prompt = kwargs["messages"][1]["content"]
            assert "volume_index" in prompt
            assert valid["chapters"][0]["volume_id"] in prompt
            data = valid if len(calls) == 2 and repair_succeeds else invalid

            async def generate():
                yield json.dumps({"data": data}, ensure_ascii=False)

            return generate()

        monkeypatch.setattr(novel_creation_v2.LLMGateway, "stream_chat_completion", stream)
        result = asyncio.run(
            novel_creation_v2.run_creation_artifact_generation(
                db,
                "",
                {
                    "session_id": session.id,
                    "stage": "opening_outline",
                    "model": "openai:test",
                    "use_model": True,
                },
            )
        )
        assert len(calls) == 2
        if repair_succeeds:
            assert result["status"] == "ok", result
            assert session.draft_json["stages"]["opening_outline"][
                "data"
            ] == normalize_opening_outline(valid)
            assert session.revision == revision + 1
        else:
            assert result["status"] == "error"
            assert session.draft_json == before
            assert session.revision == revision


def test_single_entity_generation_still_validates_parent_against_owned_volume_index():
    data = {"chapters": [deepcopy(FIXTURE["data"]["chapters"][0])]}
    validate_opening_outline(data, volume_index=FIXTURE["volume_index"], partial=True)
    with pytest.raises(CreationGenerationError, match="volume_id"):
        validate_opening_outline(data, volume_index=[], partial=True)


@pytest.mark.parametrize("entity_type", ["chapter_outline", "scene_outline"])
def test_first_opening_entity_initializes_chapters_and_scenes_together(monkeypatch, entity_type):
    with _db() as db:
        session = _ready_session(db)
        data = _opening_for_session(session)
        draft = deepcopy(session.draft_json)
        draft["stages"]["opening_outline"] = {"status": "pending", "data": None}
        session.draft_json = draft
        db.commit()

        async def stream(**kwargs):
            yield json.dumps({"data": data}, ensure_ascii=False)

        monkeypatch.setattr(novel_creation_v2.LLMGateway, "stream_chat_completion", stream)
        result = asyncio.run(
            novel_creation_v2.run_creation_artifact_generation(
                db,
                "",
                {
                    "session_id": session.id,
                    "stage": "opening_outline",
                    "model": "openai:test",
                    "entity_type": entity_type,
                    "use_model": True,
                },
            )
        )
        assert result["status"] == "ok", result
        saved = session.draft_json["stages"]["opening_outline"]["data"]
        assert len(saved["chapters"]) == 3
        assert len(saved["sections"]) == 9
        validate_opening_outline(saved, volume_index=creation_volume_index(session))


def test_failed_rest_confirmation_does_not_complete_the_generating_run():
    from fastapi import HTTPException

    from app.routers import novel_creation
    from app.services.novel_creation_runs import create_run

    with _db() as db:
        session = _ready_session(db)
        data = deepcopy(session.draft_json["stages"]["opening_outline"]["data"])
        run = create_run(db, session, "opening_outline", {})
        run.status = "waiting_user"
        db.commit()
        revision = session.revision
        data["chapters"][0]["summary"] = ""
        with pytest.raises(HTTPException) as caught:
            asyncio.run(
                novel_creation.confirm_creation_stage(
                    session.id,
                    "opening_outline",
                    novel_creation.NovelCreationStageConfirmRequest(data=data, confirm=True),
                    db,
                )
            )
        assert caught.value.status_code == 400
        assert run.status == "waiting_user"
        assert session.revision == revision


def test_undo_cannot_restore_an_empty_outline_and_final_review_reports_the_problem():
    from app.services.novel_creation_workspace import derive_stage, undo_creation_artifact

    with _db() as db:
        session = _ready_session(db)
        data = deepcopy(session.draft_json["stages"]["opening_outline"]["data"])
        data["sections"][0]["summary"] = ""
        session.checkpoints_json = {"opening_outline": [{"data": data, "status": "confirmed"}]}
        before = deepcopy(session.draft_json)
        with pytest.raises(CreationGenerationError):
            undo_creation_artifact(session, "opening_outline")
        assert session.draft_json == before
        before["stages"]["opening_outline"]["data"] = data
        session.draft_json = before
        review = derive_stage(session, "final_review")
        assert not review["ready"]
        assert "sections[0].summary" in review["blocking"][0]


def test_whole_stage_regeneration_cannot_change_a_locked_body():
    from app.modules.creation.domain.artifact_lock_contract import validate_artifact_locks

    baseline = deepcopy(FIXTURE["data"])
    changed = deepcopy(baseline)
    changed["chapters"][0]["summary"] = "试图改写作者已经锁定的正文。"
    paths = ["/chapters/0/summary"]
    validate_artifact_locks("opening_outline", baseline, baseline, paths)
    with pytest.raises(CreationGenerationError) as caught:
        validate_artifact_locks("opening_outline", changed, baseline, paths)
    assert caught.value.reason == "creation_opening_locked_changed"


def test_explicit_planned_body_is_preserved_through_materialization():
    with _db() as db:
        session = _ready_session(db)
        data = _opening_for_session(session)
        detail = "作者补充的详细章纲：核对证据、安排伏笔，并明确这一章的行动代价。"
        data["chapters"][0]["planned_summary"] = detail
        normalized = normalize_opening_outline(data)
        assert normalized["chapters"][0]["planned_summary"] == detail
        save_stage(session, "opening_outline", normalized, confirm=True)
        db.commit()
        result = asyncio.run(finalize_creation_session(db, "", {"session_id": session.id}))
        assert result["status"] == "ok"
        chapter = db.query(OutlineNode).filter_by(node_type="chapter", sort_order=1).one()
        assert chapter.summary == data["chapters"][0]["summary"]
        assert chapter.planned_summary == detail


def test_creation_materializes_explicit_character_links_after_character_reorder():
    from app.database.models import Character
    from app.services.novel_creation_entities import creation_character_index

    with _db() as db:
        session = _ready_session(db)
        opening = _opening_for_session(session)
        index = creation_character_index(session)
        chosen = [row["id"] for row in index[:2]]
        names = {row["name"] for row in index[:2]}
        for row in opening["chapters"] + opening["sections"]:
            row["character_ids"] = chosen
        characters = deepcopy(session.draft_json["stages"]["characters"]["data"])
        characters["characters"].reverse()
        save_stage(session, "characters", characters, confirm=True)
        save_stage(session, "opening_outline", opening, confirm=True)
        db.commit()
        result = asyncio.run(finalize_creation_session(db, "", {"session_id": session.id}))
        assert result["status"] == "ok", result
        for node in db.query(OutlineNode).filter(OutlineNode.node_type.in_(["chapter", "section"])).all():
            assert {link.character.name for link in node.linked_characters} == names
            assert all(db.get(Character, link.character_id).project_id == node.project_id for link in node.linked_characters)


@pytest.mark.parametrize("references", [None, ["foreign-character"], ["林七"]])
def test_invalid_character_references_do_not_change_creation_session(references):
    with _db() as db:
        session = _ready_session(db)
        before = deepcopy(session.draft_json)
        data = deepcopy(before["stages"]["opening_outline"]["data"])
        data["sections"][0]["character_ids"] = references
        with pytest.raises(CreationGenerationError) as error:
            save_stage(session, "opening_outline", data)
        assert error.value.reason == "creation_opening_characters_invalid"
        assert session.draft_json == before
