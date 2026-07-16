"""Tests for the V2 new-book workbench contract."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import MagicMock, patch

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
from app.services.novel_creation_workspace import (
    STAGE_ORDER,
    attach_concepts,
    build_stage_flow,
    build_apply_blueprint,
    derive_stage,
    generation_blockers,
    get_presets,
    initialize_session_draft,
    patch_session,
    save_stage,
)
from app.services.workspace.registry import registry
from app.services.workspace.tools.novel_creation import apply_novel_blueprint
from app.services.workspace.tools.novel_creation_v2 import (
    generate_novel_creation_stage,
    submit_novel_creation_stage,
)


def _blueprint(title: str = "雾城记") -> dict:
    return {
        "title": title,
        "subtitle": "长篇悬疑成长",
        "logline": "能看见病毒记忆的女孩进入封锁城市，在遗忘母亲之前追出感染源。",
        "premise": "一场不断改写记忆的疫情迫使主角在救人、保存自我和追查母亲下落之间作出选择。",
        "core_conflict": "每次读取感染记忆都能接近真相，也会永久失去一段自己的过去。",
        "protagonist": {
            "name": "林七",
            "goal": "找到母亲并阻止感染扩散",
            "conflict": "能力的代价是遗忘",
            "background": "封锁城外来的实习医生",
            "appearance": "短发，左眉有旧伤",
            "age": "19",
            "current_location": "灰港隔离站",
        },
        "characters": [{"name": "周渡", "role_type": "supporting", "goal": "守住隔离线", "personality": "克制"}],
        "relationships": [{"character_a": "林七", "character_b": "周渡", "relationship_type": "uneasy_alliance"}],
        "worldbuilding": [
            {"title": "灰港隔离站", "dimension": "geography", "content": "城市唯一仍运转的医疗节点。"},
            {"title": "白塔防疫局", "dimension": "factions", "content": "控制样本与通行权限的机构。"},
            {"title": "记忆病毒", "dimension": "power_system", "content": "感染者会交换并丢失记忆。"},
        ],
        "volume_outline": [{"title": "第一卷 封锁线", "summary": "找到进入灰港的路径。", "start_chapter": 1, "end_chapter": 80}],
        "outline": [{"title": f"第{number}章 失真记录", "summary": f"第{number}次线索推进。", "node_type": "chapter"} for number in range(1, 13)],
        "golden_three": {"opening_scene": "隔离车中有人说出主角忘记的童年", "chapter_1": "入城", "chapter_2": "验忆", "chapter_3": "失踪"},
        "creative_slots": {"story_engine": "救人换线索，读忆换遗忘", "world_rules": "记忆可以传播但不可无损复制"},
        "selling_points": ["记忆感染", "母女谜团", "封锁城求生"],
        "risks": ["记忆规则需要始终可验证"],
        "requirement_coverage": {"score": 92, "covered": ["女性成长", "悬疑"], "missing": []},
        "style_rules": ["物证先于解释"],
        "forbidden_patterns": ["空降解药"],
    }


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ready_session(db) -> NovelCreationSession:
    session = NovelCreationSession(mode="internal_llm", status="drafting", user_brief="病毒记忆悬疑")
    db.add(session)
    initialize_session_draft(session, {"preset_id": "suspense", "target_chapters": 1000})
    session.blueprint_json = [_blueprint(), _blueprint("回声病历"), _blueprint("灰港遗忘症")]
    attach_concepts(session, session.blueprint_json)
    patch_session(session, {"selected_concept_id": "concept-1"})
    save_stage(session, "constraints", session.draft_json["form"], confirm=True)
    save_stage(session, "concepts", {"options": session.draft_json["concepts"], "selected_concept_id": "concept-1"}, confirm=True)
    for stage in STAGE_ORDER[2:]:
        save_stage(session, stage, derive_stage(session, stage), confirm=stage != "final_review")
    db.commit()
    return session


def test_presets_share_editable_taxonomy_contract():
    payload = get_presets()
    assert payload["schema_version"] == 2
    assert len(payload["categories"]) >= 10
    assert all(item["themes"] and item["defaults"]["avoid"] for item in payload["categories"])


def test_opening_outline_has_fifteen_chapters_and_two_to_six_sections_each():
    db = _db()
    session = _ready_session(db)
    opening = session.draft_json["stages"]["opening_outline"]["data"]
    assert len(opening["chapters"]) == 15
    counts = {chapter["client_id"]: 0 for chapter in opening["chapters"]}
    for section in opening["sections"]:
        counts[section["parent_client_id"]] += 1
        assert set(section["metadata"]) >= {
            "scene_number", "purpose", "location", "timeline", "pov_character",
            "characters", "entry_state", "exit_state", "emotional_residue", "unresolved_actions",
        }
    assert all(2 <= count <= 6 for count in counts.values())


def test_stage_edit_keeps_three_checkpoints_and_invalidates_downstream():
    db = _db()
    session = _ready_session(db)
    for revision in range(5):
        data = deepcopy(derive_stage(session, "characters"))
        data["characters"][0]["background"] = f"修订 {revision}"
        save_stage(session, "characters", data, confirm=True, source="author")
    assert len(session.checkpoints_json["characters"]) == 3
    assert session.draft_json["stages"]["locations"]["status"] == "stale"
    assert session.draft_json["stages"]["opening_outline"]["status"] == "stale"


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
    assert flow["pending_confirmations"] == ["world_style"]
    assert flow["items"]["characters"]["can_view"] is False
    assert flow["items"]["characters"]["blocked_by"][0]["stage"] == "world_style"


def test_generation_blockers_require_confirmed_upstream_stages():
    db = _db()
    session = _ready_session(db)
    session.draft_json["stages"]["world_style"]["status"] = "generated"

    blockers = generation_blockers(session, "characters")

    assert [item["stage"] for item in blockers] == ["world_style"]
    assert generation_blockers(session, "concepts") == []


def test_stage_submission_rejects_a_stale_expected_revision():
    db = _db()
    session = _ready_session(db)
    current_revision = int(session.revision or 0)
    world = deepcopy(derive_stage(session, "world_style"))

    result = asyncio.run(submit_novel_creation_stage(db, "", {
        "session_id": session.id,
        "stage": "world_style",
        "data": world,
        "confirm": False,
        "expected_revision": current_revision - 1,
    }))

    assert result["status"] == "error"
    assert result["data"]["failure_class"] == "revision_conflict"
    assert result["data"]["current_revision"] == current_revision
    assert int(session.revision or 0) == current_revision


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

    result = asyncio.run(submit_novel_creation_stage(db, "", {
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

    result = asyncio.run(submit_novel_creation_stage(db, "", {
        "session_id": session.id,
        "stage": "world_style",
        "data": world,
        "confirm": False,
        "expected_revision": int(session.revision or 0),
    }))

    assert result["status"] == "error"
    assert "叙事节奏" in result["detail"]


def test_build_apply_blueprint_keeps_macro_only_and_first_fifteen_detailed():
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
    blueprint = build_apply_blueprint(session)
    chapters = [item for item in blueprint["outline"] if item["node_type"] == "chapter"]
    sections = [item for item in blueprint["outline"] if item["node_type"] == "section"]
    assert len(chapters) == 15
    assert len(sections) == 45
    assert len(blueprint["volume_outline"]) == 10
    assert blueprint["volume_outline"][-1]["end_chapter"] == 1000
    assert blueprint["protagonist"]["profile"]["core_motivation"]
    assert blueprint["writing_style"] == "第三人称限知，危机段落使用短句"
    assert blueprint["world_tone"] == "冷峻但保留希望"
    assert blueprint["style_rules"] == ["先呈现证据，再允许角色解释"]
    assert blueprint["forbidden_patterns"] == ["禁止无证据反转"]


def test_v2_apply_is_idempotent_and_persists_profiles_relations_and_sections():
    db = _db()
    session = _ready_session(db)
    with patch("app.services.workspace.tools.novel_creation._is_real_session", return_value=False):
        first = asyncio.run(apply_novel_blueprint(db, "", {"session_id": session.id, "mode": "auto"}))
        second = asyncio.run(apply_novel_blueprint(db, "", {"session_id": session.id, "mode": "auto"}))
    assert first["status"] == "ok"
    assert second["data"]["idempotent"] is True
    assert db.query(Project).count() == 1
    assert db.query(Character).filter(Character.profile_json.isnot(None)).count() >= 1
    assert db.query(WorldbuildingRelation).count() >= 1
    assert db.query(OutlineNode).filter(OutlineNode.node_type == "chapter").count() == 15
    sections = db.query(OutlineNode).filter(OutlineNode.node_type == "section").all()
    assert len(sections) == 45
    assert all(item.parent_id and item.metadata_json for item in sections)


def test_v2_workspace_tools_are_registered():
    assert registry.get("get_novel_creation_session") is not None
    assert registry.get("generate_novel_creation_stage") is not None
    assert registry.get("submit_novel_creation_stage") is not None


def test_quick_stage_run_streams_each_stage_and_keeps_final_review_unapplied():
    db = _db()
    session = _ready_session(db)
    result = asyncio.run(generate_novel_creation_stage(db, "", {
        "session_id": session.id,
        "stage": "all",
        "use_model": False,
        "auto_confirm": True,
    }))
    assert result["status"] == "ok"
    run = result["data"]["run"]
    event_types = [item["event_type"] for item in run["events"]]
    assert event_types.count("stage_progress") == 5
    assert event_types.count("stage_completed") == 6
    assert run["status"] == "completed"
    final = result["data"]["session"]["draft"]["stages"]["final_review"]
    assert final["status"] == "generated"
    assert final["data"]["ready"] is True


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
        result = asyncio.run(generate_novel_creation_stage(db, "", {
            "session_id": session.id,
            "stage": "world_style",
            "model": "codex_cli:codex-cli",
            "use_model": True,
        }))
    assert result["status"] == "error"
    assert result["data"]["run"]["failure_class"] == "auth"
    assert "凭据" in result["data"]["run"]["next_action"]
