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
        "workspace": "assistant.workspace.quality@3.2.6",
        "chapter_quality": "assistant.chapter.quality@3.1.0",
        "novel_creation": "creation.novel.stage@3.1.0",
    }


def test_mobile_cataloging_request_policy_matches_pc_execution():
    from app.services.cataloging.constants import (
        CATALOGING_MAX_TOKENS, CATALOGING_TIMEOUT_SECONDS, CATALOGING_TEMPERATURE,
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
    assert payload["tool_categories"]["controller"] == "set_tool_categories"
    assert set(payload["tool_categories"]["categories"]) == {
        "project_files",
        "story_knowledge",
        "writing_context",
        "cataloging",
        "analysis_governance",
        "creation_data",
        "creation_flow",
        "agent_runtime",
        "extensions",
    }
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
