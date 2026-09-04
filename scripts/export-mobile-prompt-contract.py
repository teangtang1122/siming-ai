"""Export the PC workspace prompt/tool contract consumed by Android standalone mode.

This is a build-time projection, not a second hand-written prompt. Re-running
the script after any PC PromptSpec or tool schema change keeps Android's
embedded agent contract byte-for-byte aligned with the desktop sources.
"""

from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    tool_category_contract,
    tool_category_controller_schema,
)
from app.database.models import NovelCreationSession
from app.modules.assistant.infrastructure.runtime import render_prompt
from app.modules.creation.interfaces.agent_scope import (
    CREATION_AGENT_REVISION_TOOL_NAMES,
    CREATION_AGENT_WRITE_TOOL_NAMES,
    CREATION_TURN_MAX_FAILED_WRITES,
    CREATION_TURN_MAX_SUCCESSFUL_WRITES,
    MOBILE_CREATION_AGENT_TOOL_NAMES,
    MOBILE_CREATION_UNSUPPORTED_TOOL_NAMES,
)
from app.prompts.character_writer_prompts import (
    build_character_writer_messages,
)
from app.prompts.outline_writer_prompts import (
    build_outline_writer_messages,
)
from app.prompts.packs.chapter_quality import PACK as CHAPTER_QUALITY_PACK
from app.prompts.style_prompts import build_style_context
from app.prompts.worldbuilding_writer_prompts import (
    build_worldbuilding_writer_messages,
)
from app.services.agent.prompt_builder import (
    compose_chapter_writer_messages,
)
from app.services.novel_creation_agent import (
    _domain_tool_schemas as creation_agent_domain_tool_schemas,
)
from app.services.novel_creation_agent import (
    _system_prompt as creation_agent_system_prompt,
)
from app.services.novel_creation_authoring import _stage_contract
from app.services.novel_creation_contract import (
    IMPACT_DEPENDENCIES,
    STAGE_LABELS,
    STAGE_ORDER,
)
from app.services.novel_creation_prompting import (
    COMPACT_CONCEPT_SHAPE,
    CONCEPT_TASK_KINDS,
    CONCEPT_TASK_RULES,
    CONCEPT_USER_INTROS,
    CREATION_REPAIR_SYSTEM_PROMPT,
    CREATION_REPAIR_USER_TEMPLATE,
    CREATION_STAGE_TASK_RULES,
    CREATION_STAGE_USER_PREFIX,
)
from app.services.novel_creation_workspace import (
    derive_stage,
    get_presets,
    initialize_session_draft,
)
from app.services.workspace.registry import registry
from app.services.workspace.tool_schemas import (
    build_workspace_tool_schemas,
)
from app.services.workspace.tools.character_writer import (
    CHARACTER_CARD_TOOL,
)
from app.services.workspace.tools.novel_creation_v2 import (
    _normalize_stage_data,
)
from app.services.workspace.tools.outline_writer import (
    OUTLINE_PROPOSAL_TOOL,
)
from app.services.workspace.tools.worldbuilding_writer import (
    WORLDBUILDING_ENTRY_TOOL,
)

MOBILE_TOOL_NAMES = [
    "get_project_info",
    "list_chapters",
    "list_characters",
    "list_worldbuilding",
    "search_chapters",
    "search_characters",
    "search_outline",
    "search_outline_tree",
    "search_worldbuilding",
    "prepare_task_context",
    "search_task_context",
    "submit_context_evidence",
    "chapter_writer",
    "character_writer",
    "outline_writer",
    "worldbuilding_writer",
    "create_outline_node",
    "create_outline_nodes",
    "update_outline_node",
    "create_character",
    "update_character",
    "create_worldbuilding_entry",
    "update_worldbuilding_entry",
    "update_project_info",
]


def _style_template(*, short_sentences: bool, rhetoric: bool, custom: bool) -> str:
    project = SimpleNamespace(
        narrative_perspective="third_person",
        writing_style="natural",
        short_sentences=short_sentences,
        rhetoric_guidelines="{{rhetoric_guidelines}}" if rhetoric else "",
        custom_style_prompt="{{custom_style_prompt}}" if custom else "",
        forbidden_sentence_patterns="",
    )
    rendered = build_style_context(project, include_anti_ai=False)
    return rendered.replace("第三人称", "{{perspective}}").replace("自然", "{{writing_style}}", 1)


def _writer_systems() -> dict:
    character = build_character_writer_messages(
        style_context="{{style_context}}",
        world_context="{{world_context}}",
        existing_characters="暂无角色。",
    )[0]["content"]
    outline = build_outline_writer_messages(
        task_context="{{task_context}}",
    )[0]["content"]
    world = {
        dimension: build_worldbuilding_writer_messages(
            style_context="{{style_context}}",
            world_context="暂无世界观设定。",
            dimension=dimension,
        )[0]["content"]
        for dimension in ("geography", "history", "factions", "power_system", "races", "culture")
    }
    return {"character": character, "outline": outline, "world": world}


def _writer_user_templates() -> dict:
    character: dict[str, str] = {}
    for requirements in (False, True):
        for name in (False, True):
            for role in (False, True):
                for existing in (False, True):
                    key = (
                        f"requirements={str(requirements).lower()};"
                        f"name={str(name).lower()};role={str(role).lower()};"
                        f"existing={str(existing).lower()}"
                    )
                    character[key] = build_character_writer_messages(
                        style_context="{{style_context}}",
                        world_context="{{world_context}}",
                        existing_characters=(
                            "{{existing_characters}}" if existing else "暂无角色。"
                        ),
                        requirements="{{requirements}}" if requirements else "",
                        name_hint="{{name}}" if name else "",
                        role_hint="{{role_type}}" if role else "",
                    )[1]["content"]

    outline = {
        "governed": build_outline_writer_messages(
            task_context="{{task_context}}",
            batch_count="{{batch_count}}",  # type: ignore[arg-type]
        )[1]["content"]
    }

    world: dict[str, str] = {}
    for requirements in (False, True):
        for title in (False, True):
            for dimension in (
                "geography",
                "history",
                "factions",
                "power_system",
                "races",
                "culture",
            ):
                key = (
                    f"requirements={str(requirements).lower()};"
                    f"title={str(title).lower()};dimension={dimension}"
                )
                world[key] = build_worldbuilding_writer_messages(
                    style_context="{{style_context}}",
                    world_context="{{world_context}}",
                    requirements="{{requirements}}" if requirements else "",
                    dimension=dimension,
                    title_hint="{{title}}" if title else "",
                )[1]["content"]
    return {"character": character, "outline": outline, "world": world}


def _creation_baseline_fixture() -> dict:
    """Export a deterministic PC-derived fixture that Android tests replay.

    This closes the gap between sharing prompt text and sharing the non-model
    baseline/normalization semantics that shape every stage result.
    """
    session = NovelCreationSession(
        id="mobile-baseline-fixture",
        mode="hybrid",
        schema_version=3,
        user_brief="林舟在会吞噬记忆的城里寻找失踪姐姐。",
        genre="xuanhuan",
        target_audience="成年大众",
        platform="暂不确定",
    )
    draft = initialize_session_draft(session, {
        "brief": session.user_brief,
        "preset_id": "xuanhuan",
        "genre": "玄幻奇幻",
        "target_audience": session.target_audience,
        "platform": session.platform,
        "target_words": 600_000,
        "target_chapters": 240,
        "creation_mode": "author_led",
        "author_brief": session.user_brief,
        "author_outline": "全书必须分为四卷。",
        "locked_requirements": ["主角必须叫林舟"],
    })
    card = {
        "id": "concept-1",
        "source_index": 0,
        "title": "记忆城",
        "subtitle": "记忆即货币",
        "logline": "林舟必须在记忆耗尽前找到姐姐。",
        "protagonist_seed": {
            "name": "林舟",
            "identity": "记忆修复师",
            "goal": "找到姐姐",
            "lack": "不敢信任他人",
        },
        "world_hook": "城中的每次交易都会消耗一段记忆。",
        "core_conflict": "救回姐姐与保住自我记忆不可兼得。",
        "story_engine": "失去记忆会改变林舟对线索和盟友的判断。",
        "opening_hook": "林舟醒来时忘了姐姐的脸。",
        "differentiators": [],
        "risks": [],
        "coverage": {"score": 100, "covered": [], "missing": []},
    }
    draft["concepts"] = [deepcopy(card)]
    draft["concept_seeds"] = {card["id"]: deepcopy(card)}
    draft["selected_concept_id"] = card["id"]
    draft["stages"]["constraints"]["status"] = "confirmed"
    draft["stages"]["concepts"] = {
        "status": "confirmed",
        "data": {"options": [deepcopy(card)], "selected_concept_id": card["id"]},
        "source": "author",
        "updated_at": "2000-01-01T00:00:00Z",
    }
    draft["created_at"] = "2000-01-01T00:00:00Z"
    draft["updated_at"] = "2000-01-01T00:00:00Z"
    draft["stages"]["constraints"]["updated_at"] = "2000-01-01T00:00:00Z"

    expected: dict[str, dict] = {}
    for stage in ("world_style", "characters", "locations", "macro_outline"):
        expected[stage] = derive_stage(session, stage, draft)
        draft["stages"][stage] = {
            "status": "confirmed",
            "data": deepcopy(expected[stage]),
            "source": "model",
            "updated_at": "2000-01-01T00:00:00Z",
        }
    expected["opening_outline"] = derive_stage(session, "opening_outline", draft)
    expected["final_review"] = derive_stage(session, "final_review", draft)
    return {
        "session": {"draft": draft},
        "expected": expected,
    }


def _creation_normalization_fixture(baseline_fixture: dict) -> dict:
    baselines = baseline_fixture["expected"]
    raw = {
        "world_style": {
            "writing_style": ["克制", "明确"],
            "world_tone": {"core_tone": "冷峻", "atmosphere": "潮湿"},
            "story_structure": "目标—阻力—代价",
            "pacing": "每章改变局势",
            "worldbuilding": {
                "记忆税": {"dimension": "culture", "summary": "交易需要缴纳记忆"},
            },
        },
        "characters": {
            "characters": {
                "林舟": {"role_type": "protagonist", "current_goal": "找到姐姐", "background": "记忆修复师"},
                "沈岚": {"role_type": "mentor", "goal": "守住城门", "background": "旧城守门人"},
            },
            "relationships": {
                "r1": {"source": "林舟", "target": "沈岚", "relationship_type": "mentor"},
            },
        },
        "locations": {
            "entries": {
                "旧港": {"dimension": "geography", "content": "记忆交易的入口"},
            },
            "relations": [],
        },
        "macro_outline": {
            "volumes": [
                {"title": f"第{index + 1}卷", "range": f"{index * 60 + 1}-{(index + 1) * 60}", "focus": f"阶段{index + 1}"}
                for index in range(4)
            ],
        },
        "opening_outline": {
            "chapters": [
                {
                    "chapter": index + 1,
                    "title": f"自定义事件{index + 1}",
                    "beat": f"第{index + 1}章推进",
                    "sections": [
                        {"title": "进入", "purpose": "建立目标"},
                        {"title": "后果", "summary": "留下钩子"},
                    ],
                }
                for index in range(3)
            ],
        },
    }
    expected = {
        stage: _normalize_stage_data(stage, deepcopy(data), deepcopy(baselines[stage]))
        for stage, data in raw.items()
    }
    return {"baseline": baselines, "raw": raw, "expected": expected}


def build_contract() -> dict:
    missing = [name for name in MOBILE_TOOL_NAMES if registry.get(name) is None]
    if missing:
        raise RuntimeError(f"Missing workspace tools: {missing}")
    tool_names = sorted(MOBILE_TOOL_NAMES)
    workspace_system = render_prompt(
        "assistant.workspace.quality",
        outline_batch_count="{{outline_batch_count}}",
    )
    chapter_messages = compose_chapter_writer_messages(
        pack=CHAPTER_QUALITY_PACK,
        style_context="{{style_context}}",
        outline_context="{{outline_context}}",
        world_context="{{world_context}}",
        character_profiles="{{character_profiles}}",
        recent_summaries="{{recent_summaries}}",
        requirements="{{requirements}}",
    )
    baseline_fixture = _creation_baseline_fixture()
    mobile_creation_names = set(MOBILE_CREATION_AGENT_TOOL_NAMES)
    mobile_creation_schemas = [
        schema
        for schema in creation_agent_domain_tool_schemas()
        if schema.get("function", {}).get("name") in mobile_creation_names
    ]
    contract = {
        "schema_version": 3,
        "source_versions": {
            "workspace": "assistant.workspace.quality@3.2.5",
            "chapter_quality": "assistant.chapter.quality@3.1.0",
            "novel_creation": "creation.novel.stage@3.1.0",
        },
        "tool_names": sorted({*tool_names, TOOL_CATEGORY_CONTROLLER}),
        "tool_schemas": [
            tool_category_controller_schema(),
            *build_workspace_tool_schemas(tool_names),
        ],
        "tool_categories": tool_category_contract(),
        "workspace_system_template": workspace_system,
        "workspace_current_user_contract": {
            "source": "conversation_context_frame.current_user_message",
            "content": "verbatim",
        },
        "chapter": {
            "quality_system_template": chapter_messages[0]["content"],
            "user_template": chapter_messages[1]["content"],
        },
        "style_templates": {
            f"short={str(short).lower()};rhetoric={str(rhetoric).lower()};custom={str(custom).lower()}":
                _style_template(short_sentences=short, rhetoric=rhetoric, custom=custom)
            for short in (False, True)
            for rhetoric in (False, True)
            for custom in (False, True)
        },
        "writer_systems": _writer_systems(),
        "writer_user_templates": _writer_user_templates(),
        "writer_output_tools": {
            "character": CHARACTER_CARD_TOOL,
            "outline": OUTLINE_PROPOSAL_TOOL,
            "world": WORLDBUILDING_ENTRY_TOOL,
        },
        "creation_agent": {
            "system_template": creation_agent_system_prompt("{{session_id}}"),
            "tool_names": sorted({*mobile_creation_names, TOOL_CATEGORY_CONTROLLER}),
            "excluded_pc_tool_names": sorted(MOBILE_CREATION_UNSUPPORTED_TOOL_NAMES),
            "revision_tool_names": sorted(
                set(CREATION_AGENT_REVISION_TOOL_NAMES) & mobile_creation_names
            ),
            "write_tool_names": sorted(
                set(CREATION_AGENT_WRITE_TOOL_NAMES) & mobile_creation_names
            ),
            "max_successful_writes_per_turn": CREATION_TURN_MAX_SUCCESSFUL_WRITES,
            "max_failed_writes_per_turn": CREATION_TURN_MAX_FAILED_WRITES,
            "tool_schemas": [
                tool_category_controller_schema(),
                *mobile_creation_schemas,
            ],
        },
        "creation": {
            "schema_version": 3,
            "presets": get_presets(),
            "stage_order": list(STAGE_ORDER),
            "stage_labels": STAGE_LABELS,
            "impact_dependencies": {
                stage: list(dependencies)
                for stage, dependencies in IMPACT_DEPENDENCIES.items()
            },
            "stage_system_template": render_prompt(
                "creation.novel.stage",
                task_kind="{{task_kind}}",
                task_rules="{{task_rules}}",
            ),
            "stage_task_rules": CREATION_STAGE_TASK_RULES,
            "stage_user_prefix": CREATION_STAGE_USER_PREFIX,
            "repair_system_prompt": CREATION_REPAIR_SYSTEM_PROMPT,
            "repair_user_template": CREATION_REPAIR_USER_TEMPLATE,
            "stage_contracts": {
                stage: _stage_contract(stage)
                for stage in STAGE_ORDER
                if stage not in {"constraints", "concepts"}
            },
            "concept_shape": COMPACT_CONCEPT_SHAPE,
            "concept_shape_json": json.dumps(COMPACT_CONCEPT_SHAPE, ensure_ascii=False),
            "concept_task_kinds": CONCEPT_TASK_KINDS,
            "concept_task_rules": CONCEPT_TASK_RULES,
            "concept_user_intros": CONCEPT_USER_INTROS,
            "deterministic_baseline_fixture": baseline_fixture,
            "normalization_fixture": _creation_normalization_fixture(baseline_fixture),
        },
    }
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    contract["source_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return contract


def main() -> None:
    destination = (
        ROOT
        / "mobile"
        / "android"
        / "app"
        / "src"
        / "main"
        / "assets"
        / "pc_workspace_prompt_contract.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(build_contract(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(destination)


if __name__ == "__main__":
    main()
