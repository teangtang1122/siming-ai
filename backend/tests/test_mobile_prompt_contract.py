"""Android standalone prompts must remain generated from PC sources."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "scripts" / "export-mobile-prompt-contract.py"
ASSET = (
    ROOT
    / "mobile"
    / "android"
    / "app"
    / "src"
    / "main"
    / "assets"
    / "pc_workspace_prompt_contract.json"
)


def test_android_prompt_contract_has_no_pc_source_drift():
    namespace = runpy.run_path(str(EXPORTER), run_name="mobile_prompt_contract_test")
    generated = namespace["build_contract"]()
    committed = json.loads(ASSET.read_text(encoding="utf-8"))

    assert committed == generated
    assert committed["source_sha256"]
    assert committed["source_versions"] == {
        "workspace": "assistant.workspace.quality@3.4.7",
        "chapter_quality": "assistant.chapter.quality@3.1.0",
        "novel_creation": "creation.novel.stage@3.1.0",
    }


def test_pc_and_mobile_default_to_one_outline_without_overriding_explicit_count():
    from app.modules.story.domain.outline_contract import DEFAULT_OUTLINE_BATCH_COUNT
    from app.schemas.ai_writer import WorkspaceAssistantRequest

    contract = json.loads(ASSET.read_text(encoding="utf-8"))
    assert contract["outline_generation"]["default_batch_count"] == DEFAULT_OUTLINE_BATCH_COUNT == 1
    assert WorkspaceAssistantRequest(message="Plan ahead").outline_batch_count == 1
    assert WorkspaceAssistantRequest(message="Plan ahead", outline_batch_count=5).outline_batch_count == 5
    assert "“下一章”只规划一章，batch_count=1" in contract["workspace_system_template"]


def test_mobile_cataloging_request_policy_matches_pc_execution():
    from app.services.cataloging.constants import (
        CATALOGING_MAX_TOKENS,
        CATALOGING_TEMPERATURE,
        CATALOGING_TIMEOUT_SECONDS,
    )
    from app.services.cataloging.model_selection import cataloging_extra_body

    request = json.loads(ASSET.read_text(encoding="utf-8"))["cataloging"]["model_request"]
    assert request["stream_idle_timeout_seconds"] == CATALOGING_TIMEOUT_SECONDS == 300
    assert request["max_output_tokens"] == CATALOGING_MAX_TOKENS
    assert request["temperature"] == CATALOGING_TEMPERATURE
    assert request["non_thinking_providers"] == ["deepseek"]
    assert cataloging_extra_body("deepseek:deepseek-flash")["thinking"] == {"type": "disabled"}
    assert "thinking" not in cataloging_extra_body("openai:fixture")


def test_android_prompt_contract_contains_full_nested_writer_pipeline():
    contract = json.loads(ASSET.read_text(encoding="utf-8"))
    names = set(contract["tool_names"])

    assert {
        "chapter_writer",
        "character_writer",
        "outline_writer",
        "worldbuilding_writer",
        "update_project_info",
    } <= names
    assert "create_chapter" not in names
    assert "update_chapter" not in names
    assert "只调用本轮实际提供的工具" in contract["workspace_system_template"]
    assert "质量模式宁可多检查一次因果" in contract["chapter"]["quality_system_template"]
    assert "{{length_instruction}}" in contract["chapter"]["user_template"]
    assert "1800-2500" not in contract["chapter"]["user_template"]
    assert "{{source_draft}}" in contract["chapter"]["revision_user_template"]
    assert set(contract["writer_output_tools"]) == {"character", "outline", "world"}


def test_mobile_uses_the_same_live_writing_state_instruction_as_pc():
    from app.prompts.workspace_assistant import CHAPTER_WRITING_STATE_INSTRUCTION

    contract = json.loads(ASSET.read_text(encoding="utf-8"))
    assert contract["chapter_writing_state_instruction"] == CHAPTER_WRITING_STATE_INSTRUCTION
    assert "不得据此要求重复保存建档" in CHAPTER_WRITING_STATE_INSTRUCTION
    assert "不能推断已经建档" in CHAPTER_WRITING_STATE_INSTRUCTION


def test_android_prompt_contract_contains_pc_novel_creation_pipeline():
    payload = json.loads(ASSET.read_text(encoding="utf-8"))
    contract = payload["creation"]
    agent = payload["creation_agent"]

    assert contract["schema_version"] == 3
    assert "max_iterations" not in agent
    assert "可按任意顺序工作" in agent["system_template"]
    assert "立即增量写入" in agent["system_template"]
    assert "最多完成一次成功的写工具调用" in agent["system_template"]
    assert "创意方向 data 为空时" in agent["system_template"]
    assert "默认只生成 1 张创意卡（options 长度为 1）" in agent["system_template"]
    assert "默认只返回 1 张创意卡（concepts 数组长度为 1）" in contract["concept_task_rules"]["explore"]
    assert agent["max_successful_writes_per_turn"] == 1
    assert agent["max_failed_writes_per_turn"] == 3
    assert "confirm_creation_artifact" in agent["write_tool_names"]
    assert "patch_creation_artifact" in agent["revision_tool_names"]
    unsupported = {
        "get_creation_operation",
        "cancel_creation_operation",
        "pause_creation_operation",
        "resume_creation_operation",
        "retry_creation_operation",
        "undo_creation_artifact",
        "list_creation_artifact_versions",
        "get_creation_artifact_diff",
        "restore_creation_artifact_version",
        "preview_creation_import",
        "apply_creation_import",
    }
    assert set(agent["excluded_pc_tool_names"]) == unsupported
    assert unsupported.isdisjoint(agent["tool_names"])
    assert unsupported.isdisjoint(agent["revision_tool_names"])
    assert unsupported.isdisjoint(agent["write_tool_names"])
    assert set(agent["capacity_preflight_read_tool_names"]) == {
        "get_creation_session", "get_creation_snapshot",
        "get_creation_artifact", "list_creation_artifacts",
        "list_creation_entities", "get_creation_entity",
    }
    from app.services.novel_creation_contract import STAGE_ORDER

    artifact_schema = next(
        tool["function"]["parameters"] for tool in agent["tool_schemas"]
        if tool["function"]["name"] == "get_creation_artifact"
    )
    assert artifact_schema["properties"]["artifact"]["enum"] == list(STAGE_ORDER)
    write_limits = agent["write_result_contract"]["max_json_bytes_by_tool"]
    assert set(write_limits) == set(agent["write_tool_names"])
    assert write_limits["patch_creation_artifact"] == 2 * 1024
    patch_schema = next(
        tool["function"]["parameters"] for tool in agent["tool_schemas"]
        if tool["function"]["name"] == "patch_creation_artifact"
    )
    patch_change = patch_schema["properties"]["changes"]["items"]
    assert patch_change["required"] == ["path"]
    assert patch_change["properties"]["action"]["type"] == "string"
    assert patch_change["properties"]["op"]["type"] == "string"
    assert patch_change["oneOf"] == [
        {"required": ["action"]}, {"required": ["op"]},
    ]
    assert payload["tool_categories"]["controller"] == "set_tool_categories"
    assert payload["tool_categories"]["max_active_categories"] == 2
    assert all(2 <= len(group["tools"]) <= 6 for group in payload["tool_categories"]["categories"].values())
    def offered_categories(schemas):
        return next(tool["function"]["parameters"]["properties"]["enabled_categories"]["items"]["enum"]
                    for tool in schemas if tool["function"]["name"] == "set_tool_categories")
    assert all(name.startswith("creation_") for name in offered_categories(agent["tool_schemas"]))
    assert not any(name.startswith("creation_") for name in offered_categories(payload["tool_schemas"]))
    assert "chapter_writing" in offered_categories(payload["tool_schemas"])
    assert len(payload["cataloging"]["tool_schemas"]) == 4
    assert "set_tool_categories" not in str(payload["cataloging"]["tool_schemas"])
    advertised_schema_names = {
        schema["function"]["name"] for schema in agent["tool_schemas"]
    }
    assert advertised_schema_names == set(agent["tool_names"])
    assert advertised_schema_names >= {
        "set_tool_categories",
        "get_creation_snapshot",
        "finalize_creation_session",
    }
    assert unsupported.isdisjoint(advertised_schema_names)
    confirm_schema = next(
        schema["function"]
        for schema in agent["tool_schemas"]
        if schema["function"]["name"] == "confirm_creation_artifact"
    )
    assert "data" not in confirm_schema["parameters"]["properties"]
    assert contract["stage_order"] == [
        "constraints",
        "concepts",
        "world_style",
        "characters",
        "locations",
        "macro_outline",
        "opening_outline",
        "final_review",
    ]
    assert "正式作品" in contract["stage_system_template"]
    assert "parent_client_id" in contract["stage_contracts"]["opening_outline"]
    assert contract["impact_dependencies"]["characters"] == [
        "macro_outline",
        "opening_outline",
        "final_review",
    ]
    assert set(contract["deterministic_baseline_fixture"]["expected"]) == {
        "world_style", "characters", "locations", "macro_outline", "opening_outline", "final_review",
    }
    assert set(contract["normalization_fixture"]["expected"]) == {
        "world_style", "characters", "locations", "macro_outline", "opening_outline",
    }
    assert len(contract["presets"]["categories"]) >= 10
