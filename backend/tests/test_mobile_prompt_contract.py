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
        "workspace": "assistant.workspace.quality@3.1.0",
        "chapter_quality": "assistant.chapter.quality@3.0.0",
        "chapter_fast": "assistant.chapter.fast@3.0.0",
        "novel_creation": "creation.novel.stage@3.0.0",
    }


def test_android_prompt_contract_contains_full_nested_writer_pipeline():
    contract = json.loads(ASSET.read_text(encoding="utf-8"))
    names = set(contract["tool_names"])

    assert {
        "chapter_writer",
        "character_writer",
        "outline_writer",
        "worldbuilding_writer",
        "create_chapter",
        "update_chapter",
        "update_project_info",
    } <= names
    assert "只调用本轮实际提供的工具" in contract["workspace_system_template"]
    assert "质量模式宁可多检查一次因果" in contract["chapter"]["quality_system_template"]
    assert set(contract["writer_output_tools"]) == {"character", "outline", "world"}


def test_android_prompt_contract_contains_pc_novel_creation_pipeline():
    payload = json.loads(ASSET.read_text(encoding="utf-8"))
    contract = payload["creation"]
    agent = payload["creation_agent"]

    assert contract["schema_version"] == 3
    assert agent["max_iterations"] == 6
    assert "不要强迫用户走固定阶段" in agent["system_template"]
    assert "立即增量写入" in agent["system_template"]
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