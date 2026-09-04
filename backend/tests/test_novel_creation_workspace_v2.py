"""Tests for the new-book workbench contract."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    Character,
    NovelCreationSession,
    OutlineNode,
    Project,
    WorldbuildingRelation,
)
from app.database.session import Base
from app.services.novel_creation_authoring import _stage_contract
from app.services.novel_creation_contract import (
    LEGACY_OPENING_OUTLINE_CHAPTER_COUNT,
    OPENING_OUTLINE_CHAPTER_COUNT,
)
from app.services.novel_creation_workspace import (
    STAGE_ORDER,
    build_project_materialization_payload,
    build_stage_flow,
    creation_artifact_dependencies,
    derive_stage,
    get_presets,
    initialize_session_draft,
    patch_creation_artifact,
    patch_session,
    save_compact_concepts,
    save_stage,
    serialize_creation_artifact,
    serialize_session,
    set_creation_artifact_locks,
    undo_creation_artifact,
)
from app.services.workspace.registry import registry
from app.services.workspace.tools.novel_creation import finalize_creation_session
from app.services.workspace.tools.novel_creation_v2 import (
    _normalize_stage_data,
    _validate_stage,
    confirm_creation_artifact,
    generate_creation_artifact,
    get_creation_artifact,
    run_creation_artifact_generation,
    get_creation_snapshot,
    list_creation_entities_tool,
    patch_creation_session_tool,
    save_creation_artifact,
)


def _concept_seed(title: str = "雾城记") -> dict:
    return {
        "title": title,
        "subtitle": "长篇悬疑成长",
        "logline": "能看见病毒记忆的女孩进入封锁城市，在遗忘母亲之前追出感染源。",
        "core_conflict": "每次读取感染记忆都能接近真相，也会永久失去一段自己的过去。",
        "protagonist_seed": {
            "name": "林七",
            "identity": "封锁城外来的实习医生",
            "goal": "找到母亲并阻止感染扩散",
            "lack": "能力的代价是遗忘",
        },
        "world_hook": "记忆可以传播但不可无损复制",
        "story_engine": "救人换线索，读忆换遗忘",
        "opening_hook": "隔离车中有人说出主角忘记的童年",
        "differentiators": ["记忆感染", "母女谜团", "封锁城求生"],
        "risks": ["记忆规则需要始终可验证"],
        "coverage": {"score": 92, "covered": ["女性成长", "悬疑"], "missing": []},
    }


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ready_session(db) -> NovelCreationSession:
    session = NovelCreationSession(mode="internal_llm", status="drafting", user_brief="病毒记忆悬疑")
    db.add(session)
    initialize_session_draft(session, {"preset_id": "suspense", "target_chapters": 1000})
    save_compact_concepts(session, [_concept_seed()])
    patch_session(session, {"selected_concept_id": "concept-1"})
    save_stage(session, "constraints", session.draft_json["form"], confirm=True)
    save_stage(session, "concepts", {"options": session.draft_json["concepts"], "selected_concept_id": "concept-1"}, confirm=True)
    for stage in STAGE_ORDER[2:]:
        data = derive_stage(session, stage)
        if stage == "world_style":
            data["worldbuilding"] = [
                {"title": "灰港隔离站", "dimension": "geography", "content": "城市唯一仍运转的医疗节点。"},
                {"title": "白塔防疫局", "dimension": "factions", "content": "控制样本与通行权限的机构。"},
                {"title": "记忆病毒", "dimension": "power_system", "content": "感染者会交换并丢失记忆。"},
            ]
        elif stage == "characters":
            data["characters"].append({
                "name": "周渡",
                "role_type": "supporting",
                "goal": "守住隔离线",
                "personality": "克制",
            })
            data["relationships"] = [{
                "character_a": "林七",
                "character_b": "周渡",
                "relationship_type": "uneasy_alliance",
            }]
        elif stage == "locations":
            data["entries"] = [
                {"title": "灰港隔离站", "dimension": "geography", "content": "城市唯一仍运转的医疗节点。"},
                {"title": "白塔防疫局", "dimension": "factions", "content": "控制样本与通行权限的机构。"},
            ]
            data["relations"] = [{
                "source_title": "灰港隔离站",
                "target_title": "白塔防疫局",
                "relation_type": "connected_to",
                "description": "双方通过封锁线与通行许可互相影响。",
            }]
        save_stage(session, stage, data, confirm=stage != "final_review")
    db.commit()
    return session


def test_presets_share_editable_taxonomy_contract():
    payload = get_presets()
    assert payload["schema_version"] == 3
    assert len(payload["categories"]) >= 10
    assert all(item["themes"] and item["defaults"]["avoid"] for item in payload["categories"])


def test_v2_draft_migrates_to_v3_as_exploration_without_losing_content():
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting", user_brief="旧版悬疑草稿", schema_version=2)
    session.draft_json = {
        "schema_version": 2,
        "form": {"brief": "旧版悬疑草稿", "target_words": 600000, "target_chapters": 240},
        "concepts": [],
        "stages": {},
    }
    db.add(session)

    draft = initialize_session_draft(session)

    assert draft["schema_version"] == 3
    assert draft["creation_mode"] == "explore"
    assert draft["form"]["brief"] == "旧版悬疑草稿"


def test_opening_outline_contract_defaults_to_three_chapters():
    assert "chapters 恰好3章" in _stage_contract("opening_outline")


def test_opening_outline_has_three_chapters_and_two_to_six_sections_each():
    db = _db()
    session = _ready_session(db)
    opening = session.draft_json["stages"]["opening_outline"]["data"]
    assert len(opening["chapters"]) == OPENING_OUTLINE_CHAPTER_COUNT
    counts = {chapter["client_id"]: 0 for chapter in opening["chapters"]}
    for section in opening["sections"]:
        counts[section["parent_client_id"]] += 1
        assert set(section["metadata"]) >= {
            "scene_number", "purpose", "location", "timeline", "pov_character",
            "characters", "entry_state", "exit_state", "emotional_residue", "unresolved_actions",
        }
    assert all(2 <= count <= 6 for count in counts.values())


def test_existing_fifteen_chapter_opening_outline_remains_usable_after_upgrade():
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    draft["form"]["opening_chapters"] = LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
    legacy_opening = derive_stage(session, "opening_outline", draft)
    draft["stages"]["opening_outline"]["data"] = legacy_opening
    session.draft_json = draft

    migrated = initialize_session_draft(session)
    final = derive_stage(session, "final_review", migrated)

    assert migrated["form"]["opening_chapters"] == LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
    assert len(legacy_opening["chapters"]) == LEGACY_OPENING_OUTLINE_CHAPTER_COUNT
    _validate_stage("opening_outline", legacy_opening)
    assert final["ready"] is True


def test_structure_validation_uses_actual_ids_without_replacing_saved_review():
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    opening = draft["stages"]["opening_outline"]["data"]
    id_map = {
        chapter["client_id"]: f"model-chapter-{index}"
        for index, chapter in enumerate(opening["chapters"], start=1)
    }
    for chapter in opening["chapters"]:
        chapter["client_id"] = id_map[chapter["client_id"]]
    for section in opening["sections"]:
        section["parent_client_id"] = id_map[section["parent_client_id"]]
    draft["stages"]["final_review"] = {
        "status": "generated",
        "data": {"ready": False, "blocking": ["每章必须包含2至6个 section 场景事件"]},
        "source": "contract",
    }
    session.draft_json = draft

    final = derive_stage(session, "final_review", draft)
    assert final["ready"] is True
    assert final["blocking"] == []

    serialized = serialize_session(session)
    assert serialized["draft"]["stages"]["final_review"]["data"] == draft["stages"]["final_review"]["data"]
    assert build_project_materialization_payload(session)["outline"]


@pytest.mark.parametrize("source", ["author", "model"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_saved_final_review_round_trips_through_artifact_and_session(source, confirmed):
    db = _db()
    session = _ready_session(db)
    review = {
        "ready": True,
        "blocking": [],
        "warnings": ["真实模型曾超时，不能算作完成", "原始证据未校准，作者要求继续复核"],
        "counts": {"saved_chapters": 8, "cataloged_chapters": 8},
    }
    save_stage(session, "final_review", review, source=source, confirm=confirmed)
    db.commit()
    identity = session.id
    db.expire_all()
    session = db.get(NovelCreationSession, identity)
    before = deepcopy(session.draft_json)
    revision = session.revision

    artifact = serialize_creation_artifact(session, "final_review")
    whole_session = serialize_session(session)

    assert artifact["data"] == review
    assert whole_session["draft"]["stages"]["final_review"]["data"] == review
    assert whole_session["draft"]["stages"]["final_review"]["source"] == source
    assert session.draft_json == before
    assert session.revision == revision
    # Live structural validation is still used at materialization, without
    # laundering the saved author's observations into a generated review.
    assert build_project_materialization_payload(session)["outline"]


def test_core_project_can_be_written_before_opening_outline_is_generated():
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    draft["stages"]["opening_outline"] = {
        "status": "pending",
        "data": None,
        "source": "unknown",
    }
    session.draft_json = draft

    final = derive_stage(session, "final_review", draft)
    project_payload = build_project_materialization_payload(session)

    assert final["ready"] is True
    assert final["counts"]["chapters"] == 0
    assert any("正式作品" in warning for warning in final["warnings"])
    assert project_payload["outline"] == []
    assert any("正式作品" in warning for warning in project_payload["apply_warnings"])


def test_unconfirmed_opening_outline_is_not_silently_applied():
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    draft["stages"]["opening_outline"]["status"] = "generated"
    session.draft_json = draft

    project_payload = build_project_materialization_payload(session)

    assert project_payload["outline"] == []


def test_stage_edit_keeps_three_checkpoints_and_invalidates_downstream():
    db = _db()
    session = _ready_session(db)
    for revision in range(5):
        data = deepcopy(derive_stage(session, "characters"))
        data["characters"][0]["background"] = f"修订 {revision}"
        save_stage(session, "characters", data, confirm=True, source="author")
    assert len(session.checkpoints_json["characters"]) == 3
    assert session.draft_json["stages"]["locations"]["status"] == "confirmed"
    assert session.draft_json["stages"]["macro_outline"]["status"] == "stale"
    assert session.draft_json["stages"]["opening_outline"]["status"] == "stale"


def test_artifact_patch_is_atomic_and_reports_downstream_impact():
    db = _db()
    session = _ready_session(db)
    before_revision = int(session.revision or 0)
    before_volume_count = len(session.draft_json["stages"]["macro_outline"]["data"]["volumes"])

    result = patch_creation_artifact(session, "macro_outline", [
        {"path": "/volumes/0/title", "action": "replace", "value": "第一卷 失忆封锁线"},
        {"path": "/volumes", "action": "append", "value": {"title": "第二卷 白塔回声"}},
    ], source="assistant")

    assert result["artifact"]["data"]["volumes"][0]["title"] == "第一卷 失忆封锁线"
    assert len(result["artifact"]["data"]["volumes"]) == before_volume_count + 1
    assert int(session.revision or 0) == before_revision + 1
    assert result["affected_artifacts"]
    assert session.draft_json["stages"]["opening_outline"]["status"] == "stale"

    saved = deepcopy(session.draft_json)
    with pytest.raises(ValueError, match="invalid list path segment"):
        patch_creation_artifact(session, "macro_outline", [
            {"path": "/volumes/99/title", "action": "replace", "value": "不会写入"},
        ])
    assert session.draft_json == saved


def test_serializing_creation_artifact_does_not_mutate_the_session():
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    draft["updated_at"] = "2000-01-01T00:00:00Z"
    session.draft_json = draft
    saved = deepcopy(session.draft_json)

    artifact = serialize_creation_artifact(session, "macro_outline")

    assert artifact["data"]
    assert session.draft_json == saved


def test_artifact_patch_accepts_standard_json_patch_add_to_array():
    db = _db()
    session = _ready_session(db)
    before_revision = int(session.revision or 0)

    result = patch_creation_artifact(session, "constraints", [
        {"op": "add", "path": "/special_requirements/-", "value": "MCP write probe"},
    ], source="external_agent")

    assert result["artifact"]["data"]["special_requirements"][-1] == "MCP write probe"
    assert result["changes"] == [{"path": "/special_requirements", "action": "append"}]
    assert int(session.revision or 0) == before_revision + 1


def test_author_patch_keeps_confirmed_facts_confirmed_but_assistant_patch_requires_review():
    db = _db()
    session = _ready_session(db)

    author_result = patch_creation_artifact(session, "constraints", [
        {"path": "/genre", "action": "replace", "value": "记忆悬疑"},
    ], source="author")
    assert author_result["artifact"]["status"] == "confirmed"

    assistant_result = patch_creation_artifact(session, "constraints", [
        {"path": "/genre", "action": "replace", "value": "记忆悬疑与都市奇谈"},
    ], source="assistant")
    assert assistant_result["artifact"]["status"] == "generated"


def test_artifact_locks_block_parent_and_child_patch_paths():
    db = _db()
    session = _ready_session(db)
    set_creation_artifact_locks(session, "characters", ["/characters/0"], locked=True)
    artifact = serialize_creation_artifact(session, "characters")
    assert artifact["locked_paths"] == ["/characters/0"]

    with pytest.raises(ValueError, match="字段已锁定"):
        patch_creation_artifact(session, "characters", [
            {"path": "/characters/0/goal", "action": "replace", "value": "改写目标"},
        ])

    set_creation_artifact_locks(session, "characters", ["/characters/0"], locked=False)
    result = patch_creation_artifact(session, "characters", [
        {"path": "/characters/0/goal", "action": "replace", "value": "找到母亲并保存记忆"},
    ])
    assert result["artifact"]["data"]["characters"][0]["goal"] == "找到母亲并保存记忆"


def test_artifact_patch_runs_schema_validation_before_writing():
    db = _db()
    session = _ready_session(db)
    saved = deepcopy(session.draft_json)
    revision = int(session.revision or 0)

    def reject_missing_characters(stage: str, data: dict) -> None:
        assert stage == "characters"
        if not data.get("characters"):
            raise ValueError("缺少角色档案")

    with pytest.raises(ValueError, match="缺少角色档案"):
        patch_creation_artifact(
            session,
            "characters",
            [{"path": "/characters", "action": "replace", "value": []}],
            validator=reject_missing_characters,
        )

    assert session.draft_json == saved
    assert int(session.revision or 0) == revision


def test_artifact_dependencies_keep_existing_downstream_data_visible():
    db = _db()
    session = _ready_session(db)
    dependencies = creation_artifact_dependencies(session, "characters")
    affected = {item["artifact"] for item in dependencies["affected_artifacts"]}
    assert affected == {"macro_outline", "opening_outline", "final_review"}
    assert all(item["effect"] == "stale" for item in dependencies["affected_artifacts"])


def test_generated_stage_remains_current_until_the_author_confirms_it():
    db = _db()
    session = _ready_session(db)
    world = deepcopy(derive_stage(session, "world_style"))

    save_stage(session, "world_style", world, confirm=False, source="model")

    assert session.current_stage == "world_style"
    assert session.draft_json["stages"]["world_style"]["status"] == "generated"
    flow = build_stage_flow(session)
    assert flow["attention_stage"] == "world_style"
    assert flow["recommended_stage"] == "world_style"
    assert flow["items"]["world_style"]["can_confirm"] is True

    save_stage(session, "world_style", world, confirm=True, source="author")
    assert session.current_stage == "characters"


def test_stage_flow_recovers_a_legacy_session_that_advanced_before_confirmation():
    db = _db()
    session = _ready_session(db)
    world = deepcopy(derive_stage(session, "world_style"))
    session.draft_json["stages"]["world_style"] = {
        "status": "generated",
        "data": world,
        "source": "model",
    }
    session.draft_json["stages"]["characters"] = {"status": "pending", "data": None}
    session.current_stage = "characters"

    flow = build_stage_flow(session)

    assert flow["legacy_current_stage"] == "characters"
    assert flow["attention_stage"] == "world_style"
    assert "world_style" in flow["pending_confirmations"]
    assert "generate" in flow["items"]["characters"]["actions"]
    assert flow["items"]["characters"]["soft_dependencies"][0]["stage"] == "world_style"


def test_confirming_downstream_does_not_restore_old_constraints_form():
    db = _db()
    session = _ready_session(db)
    edited_constraints = {
        **session.draft_json["stages"]["constraints"]["data"],
        "brief": "保留这次上游修改",
        "special_requirements": ["主角不能失忆"],
    }

    save_stage(session, "constraints", edited_constraints, confirm=True, source="author")
    save_stage(
        session,
        "world_style",
        deepcopy(session.draft_json["stages"]["world_style"]["data"]),
        confirm=True,
        source="author",
    )

    assert session.draft_json["form"]["brief"] == "保留这次上游修改"
    assert session.draft_json["stages"]["constraints"]["data"] == session.draft_json["form"]
    assert session.draft_json["form"]["special_requirements"] == ["主角不能失忆"]


def test_initialize_recovers_constraints_saved_without_form_sync():
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    draft["form"]["brief"] = "旧表单内容"
    draft["stages"]["constraints"]["data"]["brief"] = "已保存的新约束"
    session.draft_json = draft

    migrated = initialize_session_draft(session)

    assert migrated["form"]["brief"] == "已保存的新约束"
    assert migrated["stages"]["constraints"]["data"]["brief"] == "已保存的新约束"


def test_generation_uses_soft_dependency_hints_without_stage_blockers():
    db = _db()
    session = _ready_session(db)
    session.draft_json["stages"]["world_style"]["status"] = "generated"

    dependencies = creation_artifact_dependencies(session, "characters")
    assert [item["stage"] for item in dependencies["soft_dependencies"]] == ["world_style"]
    assert "regenerate" in build_stage_flow(session)["items"]["characters"]["actions"]


def test_generation_without_a_model_does_not_write_a_derived_stage():
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting", user_brief="少女在雾城追查失踪的母亲")
    db.add(session)
    initialize_session_draft(session, {"creation_mode": "author_led"})
    db.flush()

    result = asyncio.run(run_creation_artifact_generation(db, "", {
        "session_id": session.id,
        "stage": "world_style",
        "use_model": False,
    }))
    project_payload = build_project_materialization_payload(session)

    assert result["status"] == "error", result
    assert session.draft_json["stages"]["world_style"]["status"] == "pending"
    assert "雾城" in project_payload["logline"]


def test_serialize_incomplete_work_keeps_context_selector_available():
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting", user_brief="旧立项草稿")
    db.add(session)
    initialize_session_draft(session)
    session.draft_json["stages"]["final_review"] = {
        "status": "generated",
        "source": "model",
        "data": {"ready": False},
    }

    serialized = serialize_session(session)

    assert serialized["id"] == session.id
    assert serialized["display_title"] == "旧立项草稿"
    assert serialized["draft"]["stages"]["final_review"]["data"]["ready"] is False


def test_artifact_undo_restores_latest_checkpoint_and_keeps_dependents_stale():
    db = _db()
    session = _ready_session(db)
    original = deepcopy(session.draft_json["stages"]["macro_outline"]["data"])
    patch_creation_artifact(session, "macro_outline", [
        {"path": "/volumes/0/title", "action": "replace", "value": "修改后的卷名"},
    ])
    revision_after_patch = int(session.revision or 0)

    result = undo_creation_artifact(session, "macro_outline")

    assert result["artifact"]["data"] == original
    assert result["artifact"]["can_undo"] is False
    assert int(session.revision or 0) == revision_after_patch + 1
    assert session.draft_json["stages"]["opening_outline"]["status"] == "stale"


def test_artifact_undo_rejects_an_artifact_without_a_checkpoint():
    db = _db()
    session = NovelCreationSession(mode="internal_llm", status="drafting", user_brief="先写卷纲")
    db.add(session)
    initialize_session_draft(session)

    with pytest.raises(ValueError, match="没有可撤销"):
        undo_creation_artifact(session, "macro_outline")


def test_stage_submission_rejects_a_stale_expected_revision():
    db = _db()
    session = _ready_session(db)
    current_revision = int(session.revision or 0)
    world = deepcopy(derive_stage(session, "world_style"))

    result = asyncio.run(save_creation_artifact(db, "", {
        "session_id": session.id,
        "stage": "world_style",
        "data": world,
        "confirm": False,
        "expected_revision": current_revision - 1,
    }))

    assert result["status"] == "error"
    assert result["data"]["failure_class"] == "revision_conflict"


def test_mcp_concept_submission_accepts_one_card_without_fixed_count():
    db = _db()
    session = _ready_session(db)
    options = deepcopy(session.draft_json["concepts"])

    result = asyncio.run(save_creation_artifact(db, "", {
        "session_id": session.id,
        "stage": "concepts",
        "data": {"options": options, "selected_concept_id": options[0]["id"]},
        "confirm": True,
        "expected_revision": int(session.revision or 0),
    }))

    assert result["status"] == "ok"
    assert len(session.draft_json["stages"]["concepts"]["data"]["options"]) == 1


def test_world_style_submission_normalizes_structured_model_fields_for_authors():
    db = _db()
    session = _ready_session(db)
    current_revision = int(session.revision or 0)
    world = deepcopy(derive_stage(session, "world_style"))
    world.update({
        "world_tone": {
            "core_tone": "冷峻但保留希望",
            "reader_experience": "持续感到规则压力",
        },
        "writing_style": {
            "narrative_perspective": "第三人称限知",
            "sentence_rhythm": ["危机用短句", "余波用长句"],
        },
        "story_structure": {
            "main_line": "逃亡与揭密并进",
            "stages": ["失控", "结盟", "反攻"],
        },
        "pacing": {
            "opening": "快速入局",
            "middle": "张弛交替",
        },
    })

    result = asyncio.run(save_creation_artifact(db, "", {
        "session_id": session.id,
        "stage": "world_style",
        "data": world,
        "confirm": False,
        "expected_revision": current_revision,
    }))

    assert result["status"] == "ok"
    stored = session.draft_json["stages"]["world_style"]["data"]
    assert all(isinstance(stored[field], str) for field in ("world_tone", "writing_style", "story_structure", "pacing"))
    assert "冷峻但保留希望" in stored["world_tone"]
    assert "第三人称限知" in stored["writing_style"]
    assert "逃亡与揭密并进" in stored["story_structure"]
    assert "快速入局" in stored["pacing"]
    assert "[object Object]" not in " ".join(stored[field] for field in ("world_tone", "writing_style", "story_structure", "pacing"))


def test_world_style_submission_rejects_an_empty_structured_required_field():
    db = _db()
    session = _ready_session(db)
    world = deepcopy(derive_stage(session, "world_style"))
    world["pacing"] = {}

    result = asyncio.run(save_creation_artifact(db, "", {
        "session_id": session.id,
        "stage": "world_style",
        "data": world,
        "confirm": False,
        "expected_revision": int(session.revision or 0),
    }))

    assert result["status"] == "error"
    assert "叙事节奏" in result["detail"]


def test_project_materialization_keeps_macro_only_and_first_three_detailed():
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    draft["stages"]["world_style"]["data"].update({
        "writing_style": "第三人称限知，危机段落使用短句",
        "world_tone": "冷峻但保留希望",
        "story_structure": "逃亡与揭密双线并进",
        "pacing": "张弛交替，每三章兑现一次线索",
        "style_rules": ["先呈现证据，再允许角色解释"],
        "forbidden_patterns": ["禁止无证据反转"],
    })
    session.draft_json = draft
    project_payload = build_project_materialization_payload(session)
    chapters = [item for item in project_payload["outline"] if item["node_type"] == "chapter"]
    sections = [item for item in project_payload["outline"] if item["node_type"] == "section"]
    assert len(chapters) == OPENING_OUTLINE_CHAPTER_COUNT
    assert len(sections) == OPENING_OUTLINE_CHAPTER_COUNT * 3
    assert len(project_payload["volume_outline"]) == 1
    assert project_payload["volume_outline"][-1]["end_chapter"] == 1000
    assert project_payload["protagonist"]["profile"]["core_motivation"]
    assert project_payload["writing_style"] == "第三人称限知，危机段落使用短句"
    assert project_payload["world_tone"] == "冷峻但保留希望"
    assert project_payload["style_rules"] == ["先呈现证据，再允许角色解释"]
    assert project_payload["forbidden_patterns"] == ["禁止无证据反转"]


@pytest.mark.parametrize("rules", ["保持限知\n先核实证据", ["保持限知", "先核实证据"], "", []])
def test_finalization_preserves_author_style_rules_as_text_or_lines(rules):
    db = _db()
    session = _ready_session(db)
    draft = deepcopy(session.draft_json)
    draft["stages"]["world_style"]["data"].update({
        "style_rules": rules,
        "forbidden_patterns": rules,
    })
    session.draft_json = draft
    with patch("app.services.workspace.tools.novel_creation._is_real_session", return_value=False):
        result = asyncio.run(finalize_creation_session(db, "", {"session_id": session.id}))
    assert result["status"] == "ok"
    project = db.query(Project).one()
    expected = ("\n".join(rules) if isinstance(rules, list) else rules) or None
    assert project.rhetoric_guidelines == expected
    assert project.forbidden_sentence_patterns == expected


def test_v2_apply_is_idempotent_and_persists_profiles_relations_and_sections():
    db = _db()
    session = _ready_session(db)
    with patch("app.services.workspace.tools.novel_creation._is_real_session", return_value=False):
        first = asyncio.run(finalize_creation_session(db, "", {"session_id": session.id}))
        second = asyncio.run(finalize_creation_session(db, "", {"session_id": session.id}))
    assert first["status"] == "ok"
    assert second["data"]["idempotent"] is True
    assert db.query(Project).count() == 1
    assert db.query(Character).filter(Character.profile_json.isnot(None)).count() >= 1
    assert db.query(WorldbuildingRelation).count() >= 1
    assert db.query(OutlineNode).filter(OutlineNode.node_type == "chapter").count() == OPENING_OUTLINE_CHAPTER_COUNT
    sections = db.query(OutlineNode).filter(OutlineNode.node_type == "section").all()
    assert len(sections) == OPENING_OUTLINE_CHAPTER_COUNT * 3
    assert all(item.parent_id and item.metadata_json for item in sections)


def test_creation_workspace_tools_are_registered():
    expected = {
        "get_creation_session",
        "get_creation_snapshot",
        "get_creation_operation",
        "patch_creation_session",
        "confirm_creation_artifact",
        "generate_creation_artifact",
        "refine_creation_artifact",
        "regenerate_creation_artifact",
        "cancel_creation_operation",
        "pause_creation_operation",
        "resume_creation_operation",
        "retry_creation_operation",
        "validate_creation_session",
        "finalize_creation_session",
    }
    assert all(registry.get(name) is not None for name in expected)


def test_confirmation_cannot_edit_and_confirm_an_artifact_atomically():
    db = _db()
    session = _ready_session(db)
    before_revision = int(session.revision or 0)

    result = asyncio.run(confirm_creation_artifact(db, "", {
        "session_id": session.id,
        "artifact": "final_review",
        "expected_revision": before_revision,
        "data": {"ready": True},
    }))

    assert result["status"] == "error"
    assert "不能同时修改内容" in result["detail"]
    db.refresh(session)
    assert int(session.revision or 0) == before_revision


def test_creation_snapshot_and_session_patch_are_revision_protected():
    db = _db()
    session = _ready_session(db)
    initial_revision = int(session.revision or 0)

    patched = asyncio.run(patch_creation_session_tool(db, "", {
        "session_id": session.id,
        "expected_revision": initial_revision,
        "changes": {"user_brief": "只保留悬疑主线，目标八卷"},
    }))
    assert patched["status"] == "ok"
    assert patched["data"]["revision"] > initial_revision

    conflict = asyncio.run(patch_creation_session_tool(db, "", {
        "session_id": session.id,
        "expected_revision": initial_revision,
        "changes": {"user_brief": "不应覆盖"},
    }))
    assert conflict["status"] == "error"
    assert conflict["data"]["reason"] == "revision_conflict"

    snapshot = asyncio.run(get_creation_snapshot(db, "", {"session_id": session.id}))
    assert snapshot["status"] == "ok"
    assert snapshot["data"]["revision"] == patched["data"]["revision"]
    assert len(snapshot["data"]["artifacts"]) == len(STAGE_ORDER)
    assert "runs" not in snapshot["data"]["session"]
    assert "stage_flow" not in snapshot["data"]["session"]
    assert "checkpoints" not in snapshot["data"]["session"]
    assert "draft" not in snapshot["data"]["session"]
    assert all("data" not in item for item in snapshot["data"]["artifacts"])
    assert all("data_shape" in item for item in snapshot["data"]["artifacts"])
    assert all("flow" not in item for item in snapshot["data"]["artifacts"])
    assert all("running_operation" not in item for item in snapshot["data"]["artifacts"])
    assert len(json.dumps(snapshot["data"], ensure_ascii=False)) < 8_000


def test_large_cast_snapshot_and_entity_search_stay_bounded():
    db = _db()
    session = _ready_session(db)
    characters = deepcopy(
        session.draft_json["stages"]["characters"]["data"]
    )
    characters["characters"].extend([
        {
            "name": f"同人角色-{index:03d}",
            "role_type": "supporting",
            "goal": f"完成支线目标-{index:03d}",
            "description": "只属于该角色的精确资料" * 20,
        }
        for index in range(180)
    ])
    save_stage(session, "characters", characters, source="author")
    db.commit()

    snapshot = asyncio.run(get_creation_snapshot(db, "", {"session_id": session.id}))
    snapshot_wire = json.dumps(snapshot["data"], ensure_ascii=False)
    assert len(snapshot_wire) < 8_000
    assert "同人角色-179" not in snapshot_wire
    character_overview = next(
        item for item in snapshot["data"]["artifacts"]
        if item["artifact"] == "characters"
    )
    assert character_overview["data_shape"]["collection_counts"]["characters"] == 182

    page = asyncio.run(list_creation_entities_tool(db, "", {
        "session_id": session.id,
        "artifact": "characters",
        "entity_type": "character",
        "query": "同人角色-179",
        "limit": 5,
    }))
    assert page["status"] == "ok"
    assert page["data"]["total"] == 1
    assert page["data"]["entities"][0]["label"] == "同人角色-179"
    assert "data" not in page["data"]["entities"][0]

    artifact = asyncio.run(get_creation_artifact(db, "", {
        "session_id": session.id,
        "artifact": "characters",
    }))
    assert artifact["data"]["omitted_collections"]["characters"] == 182
    assert "同人角色-179" not in json.dumps(artifact, ensure_ascii=False)


def test_entity_generation_prompt_uses_only_target_and_explicit_references():
    db = _db()
    session = _ready_session(db)
    characters = deepcopy(
        session.draft_json["stages"]["characters"]["data"]
    )
    characters["characters"].extend([
        {
            "name": f"未召回角色-{index:03d}",
            "role_type": "supporting",
            "goal": f"未召回目标-{index:03d}",
        }
        for index in range(80)
    ])
    save_stage(session, "characters", characters, source="author")
    db.commit()
    target_page = asyncio.run(list_creation_entities_tool(db, "", {
        "session_id": session.id,
        "artifact": "characters",
        "entity_type": "character",
        "query": "林七",
    }))
    reference_page = asyncio.run(list_creation_entities_tool(db, "", {
        "session_id": session.id,
        "artifact": "characters",
        "entity_type": "character",
        "query": "周渡",
    }))
    target_id = target_page["data"]["entities"][0]["id"]
    reference_id = reference_page["data"]["entities"][0]["id"]
    captured: dict = {}

    def scoped_stream(**kwargs):
        captured.update(kwargs)

        async def generate():
            yield json.dumps({"data": {
                "characters": [{
                    "name": "林七",
                    "role_type": "protagonist",
                    "goal": "找到母亲并保护周渡",
                }],
                "relationships": [],
            }}, ensure_ascii=False)

        return generate()

    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion",
        new=MagicMock(side_effect=scoped_stream),
    ):
        result = asyncio.run(generate_creation_artifact(db, "", {
            "session_id": session.id,
            "artifact": "characters",
            "entity_id": target_id,
            "context_entity_ids": [reference_id],
            "instruction": "让林七明确保护周渡",
            "expected_revision": int(session.revision or 0),
            "model": "openai:test",
            "use_model": True,
        }))

    assert result["status"] == "ok"
    prompt_wire = captured["messages"][1]["content"]
    assert "林七" in prompt_wire
    assert "周渡" in prompt_wire
    assert "未召回角色-079" not in prompt_wire
    assert captured["extra_body"]["moshu_context_manifest_rendered"] is True
    saved_names = {
        item["name"]
        for item in session.draft_json["stages"]["characters"]["data"]["characters"]
    }
    assert "未召回角色-079" in saved_names
    # Read the entity directly after a fresh session load, without a list call
    # that would rebuild its projection from the artifact and hide lost writes.
    from app.services.novel_creation_entities import get_creation_entity
    db.expire_all()
    saved_entity = get_creation_entity(db, target_id)
    assert saved_entity.data_json["goal"] == "找到母亲并保护周渡"
    assert saved_entity.revision == session.revision


@pytest.mark.parametrize("bad_payload", [
    {"world_style": {"worldbuilding": [{"title": "失落的修改", "content": "新生成内容"}]}},
    {"worldbuilding": []},
])
def test_entity_generation_cannot_report_old_baseline_as_new_model_output(bad_payload):
    db = _db()
    session = _ready_session(db)
    from app.services.novel_creation_entities import list_creation_entities
    entities = list_creation_entities(session, artifact="world_style")
    db.commit()
    target = entities[0]
    before = deepcopy(session.draft_json["stages"]["world_style"]["data"])
    before_revision = session.revision

    def invalid_stream(**_kwargs):
        async def generate():
            yield json.dumps({"data": bad_payload}, ensure_ascii=False)
        return generate()

    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion",
        new=MagicMock(side_effect=invalid_stream),
    ):
        result = asyncio.run(run_creation_artifact_generation(db, "", {
            "session_id": session.id,
            "stage": "world_style",
            "entity_id": target["id"],
            "operation": "refine",
            "instruction": "修改所选设定",
            "expected_revision": before_revision,
            "model": "openai:test",
            "use_model": True,
        }))
    db.refresh(session)
    assert result["status"] == "error"
    assert session.revision == before_revision
    assert session.draft_json["stages"]["world_style"]["data"] == before


def test_generation_does_not_auto_select_the_first_concept():
    db = _db()
    session = NovelCreationSession(
        mode="internal_llm",
        status="drafting",
        user_brief="只使用作者明确选中的方向",
    )
    db.add(session)
    initialize_session_draft(session, {"preset_id": "free"})
    first = _concept_seed("不应自动选中的方向")
    second = _concept_seed("另一个未选择方向")
    save_compact_concepts(session, [first, second])
    db.commit()
    captured: dict = {}

    def world_stream(**kwargs):
        captured.update(kwargs)

        async def generate():
            yield json.dumps({"data": {
                "writing_style": "克制",
                "world_tone": "现实",
                "story_structure": "线性",
                "pacing": "稳健",
                "style_rules": [],
                "forbidden_patterns": [],
                "worldbuilding": [{
                    "title": "作者事实",
                    "dimension": "culture",
                    "content": "只来自用户简介",
                }],
                "display_groups": [],
            }}, ensure_ascii=False)

        return generate()

    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion",
        new=MagicMock(side_effect=world_stream),
    ):
        result = asyncio.run(run_creation_artifact_generation(db, "", {
            "session_id": session.id,
            "stage": "world_style",
            "expected_revision": int(session.revision or 0),
            "model": "openai:test",
            "use_model": True,
        }))

    assert result["status"] == "ok"
    prompt_wire = captured["messages"][1]["content"]
    assert '"selected_concept": null' in prompt_wire
    assert "不应自动选中的方向" not in prompt_wire
    assert "另一个未选择方向" not in prompt_wire


def test_all_stage_run_without_a_model_fails_without_contract_generated_content():
    db = _db()
    session = _ready_session(db)
    result = asyncio.run(run_creation_artifact_generation(db, "", {
        "session_id": session.id,
        "stage": "all",
        "use_model": False,
        "auto_confirm": True,
    }))
    assert result["status"] == "error"
    run = result["data"]["run"]
    event_types = [item["event_type"] for item in run["events"]]
    assert event_types.count("stage_progress") == 1
    assert event_types.count("stage_completed") == 0
    assert run["status"] == "failed"


def test_agent_artifact_generation_returns_a_structured_error_for_an_invalid_entity_target():
    db = _db()
    session = _ready_session(db)

    result = asyncio.run(generate_creation_artifact(db, "", {
        "session_id": session.id,
        "artifact": "world_style",
        "entity_type": "character",
        "use_model": False,
    }))

    assert result["tool"] == "generate_creation_artifact"
    assert result["status"] == "error"
    assert "目标实体类型" in result["detail"]
    assert result["data"] is None


def test_quick_run_fails_without_writing_when_model_returns_no_output():
    db = _db()
    session = _ready_session(db)

    def empty_stream(**_kwargs):
        async def generate():
            if False:
                yield ""

        return generate()

    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion",
        new=MagicMock(side_effect=empty_stream),
    ):
        result = asyncio.run(run_creation_artifact_generation(db, "", {
            "session_id": session.id,
            "stage": "all",
            "model": "opencode_cli:test-free",
            "use_model": True,
            "auto_confirm": True,
        }))

    assert result["status"] == "error"
    assert result["data"]["run"]["status"] == "failed"
    assert not any(item["event_type"] == "stage_repaired" for item in result["data"]["run"]["events"])


def test_truncated_stage_json_is_repaired_without_a_second_model_call():
    db = _db()
    session = _ready_session(db)
    world = derive_stage(session, "world_style")
    raw = json.dumps({"data": world}, ensure_ascii=False)[:-1]

    def truncated_stream(**_kwargs):
        async def generate():
            yield raw

        return generate()

    completion = MagicMock(side_effect=truncated_stream)
    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion",
        new=completion,
    ):
        result = asyncio.run(run_creation_artifact_generation(db, "", {
            "session_id": session.id,
            "stage": "world_style",
            "model": "openai:test",
            "use_model": True,
        }))

    assert result["status"] == "ok"
    assert completion.call_count == 1
    completed = [event for event in result["data"]["run"]["events"] if event["event_type"] == "stage_completed"][-1]
    assert completed["payload"]["repair_method"] == "deterministic_json"
    assert completed["payload"]["original_response_excerpt"]


def test_stage_run_classifies_invalid_token_with_actionable_next_step():
    db = _db()
    session = _ready_session(db)

    def invalid_token_stream(**_kwargs):
        async def generate():
            raise RuntimeError("(InvalidToken)")
            yield ""

        return generate()

    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.stream_chat_completion",
        new=MagicMock(side_effect=invalid_token_stream),
    ):
        result = asyncio.run(run_creation_artifact_generation(db, "", {
            "session_id": session.id,
            "stage": "world_style",
            "model": "codex_cli:codex-cli",
            "use_model": True,
        }))
    assert result["status"] == "error"
    assert result["data"]["run"]["failure_class"] == "auth"
    assert "凭据" in result["data"]["run"]["next_action"]
    assert session.last_error_json["failed_stage"] == "world_style"
    assert session.last_error_json["failed_stage_label"] == "文风与世界观"

    save_stage(session, "world_style", derive_stage(session, "world_style"), confirm=False)
    assert session.last_error_json is None


def test_lifecycle_metadata_cannot_replace_a_macro_outline():
    db = _db()
    session = _ready_session(db)
    baseline = derive_stage(session, "macro_outline")

    normalized = _normalize_stage_data(
        "macro_outline",
        {"type": "step_start", "part": {"type": "step-start"}},
        baseline,
    )

    _validate_stage("macro_outline", normalized)
    assert normalized["story_overview"] == baseline["story_overview"]
    assert normalized["volumes"] == baseline["volumes"]
    assert "type" not in normalized


def test_opening_outline_flattens_nested_scenes_and_repairs_the_full_three_chapters():
    db = _db()
    session = _ready_session(db)
    baseline = derive_stage(session, "opening_outline")
    source = {
        "chapters": [{
            "chapter_number": 1,
            "title": "死亡通知",
            "summary": "林七收到未来死亡通知。",
            "sections": [
                {"title": "档案室异响", "summary": "通知从停机终端吐出。"},
                {"title": "三日倒计时", "summary": "她确认通知带着自己的签名。"},
            ],
        }],
    }

    normalized = _normalize_stage_data("opening_outline", source, baseline)

    _validate_stage("opening_outline", normalized)
    assert len(normalized["chapters"]) == OPENING_OUTLINE_CHAPTER_COUNT
    assert len([item for item in normalized["sections"] if item["parent_client_id"] == normalized["chapters"][0]["client_id"]]) == 2
    assert all("sections" not in chapter for chapter in normalized["chapters"])
    assert all(section["client_id"] and section["metadata"]["purpose"] for section in normalized["sections"])


def test_opening_outline_validation_names_the_failed_chapters_in_chinese():
    chapters = [
        {"client_id": f"chapter-{number:02d}", "title": f"第{number}章 失真记录"}
        for number in range(1, OPENING_OUTLINE_CHAPTER_COUNT + 1)
    ]

    with pytest.raises(ValueError) as error:
        _validate_stage("opening_outline", {"chapters": chapters, "sections": []})

    assert "第1章 失真记录" in str(error.value)
    assert "场景数量" in str(error.value)
    assert "section" not in str(error.value)
