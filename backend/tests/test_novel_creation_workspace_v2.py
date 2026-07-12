"""Tests for the V2 new-book workbench contract."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

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
    build_apply_blueprint,
    derive_stage,
    get_presets,
    initialize_session_draft,
    patch_session,
    save_stage,
)
from app.services.workspace.registry import registry
from app.services.workspace.tools.novel_creation import apply_novel_blueprint
from app.services.workspace.tools.novel_creation_v2 import generate_novel_creation_stage


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


def test_build_apply_blueprint_keeps_macro_only_and_first_fifteen_detailed():
    db = _db()
    session = _ready_session(db)
    blueprint = build_apply_blueprint(session)
    chapters = [item for item in blueprint["outline"] if item["node_type"] == "chapter"]
    sections = [item for item in blueprint["outline"] if item["node_type"] == "section"]
    assert len(chapters) == 15
    assert len(sections) == 45
    assert len(blueprint["volume_outline"]) == 10
    assert blueprint["volume_outline"][-1]["end_chapter"] == 1000
    assert blueprint["protagonist"]["profile"]["core_motivation"]


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
    with patch(
        "app.services.workspace.tools.novel_creation_v2.LLMGateway.chat_completion",
        new=AsyncMock(side_effect=RuntimeError("(InvalidToken)")),
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
