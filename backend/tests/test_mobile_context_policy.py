from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.core.model_limits import DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS
from app.services.mobile_context_policy import portable_context_policy

ROOT = Path(__file__).resolve().parents[2]
ASSET = (
    ROOT
    / "mobile"
    / "android"
    / "app"
    / "src"
    / "main"
    / "assets"
    / "pc_context_manifest_policy.json"
)
EXPORTER = ROOT / "scripts" / "export-mobile-context-policy.py"


def _exporter_module():
    spec = importlib.util.spec_from_file_location("export_mobile_context_policy", EXPORTER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_android_context_policy_asset_is_generated_from_pc_source() -> None:
    module = _exporter_module()
    expected = module.build_policy()
    actual = json.loads(ASSET.read_text(encoding="utf-8"))
    assert actual == expected
    assert len(actual["source_sha256"]) == 64


def test_portable_writing_policy_keeps_pc_contract_and_budget() -> None:
    policy = portable_context_policy("writing")
    assert policy["contract"]["required_categories"] == ["target_outline", "style"]
    assert policy["model_defaults"] == {
        "context_window_tokens": DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS,
        "safety_margin_tokens": 512,
        "minimum_output_reserve_tokens": 2_048,
        "output_ratio": 0.45,
        "soft_input_target_tokens": 32_000,
        "input_budget_mode": "model_window_minus_output_reserve_and_safety_margin",
    }
    selection = policy["selection"]
    assert selection["mode"] == "model_retrieval_then_exact_selection"
    assert selection["hard_source_count_limit"] is None
    assert selection["exact_source_content_limit_chars"] is None
    assert selection["search_excerpt_chars"] == 600
    assert selection["search_page_limit"] == 10
    assert set(selection["search_source_types"]) == {
        "chapter",
        "chapter_summary",
        "outline",
        "character",
        "character_timeline",
        "worldbuilding",
        "assistant_memory",
        "narrative_governance",
    }


def test_portable_outline_planning_policy_uses_position_anchor() -> None:
    policy = portable_context_policy("outline_planning")
    assert policy["contract"]["required_categories"] == ["style", "outline_position"]
    assert policy["model_defaults"]["output_ratio"] == 0.30
    assert policy["categories"]["outline_position"]["required"] is True
    assert policy["selection"]["hard_source_count_limit"] is None


def test_portable_policy_declares_ranking_hashing_and_android_degradation() -> None:
    policy = portable_context_policy("writing")
    assert policy["ranking"]["hybrid"] == {
        "lexical": 0.45,
        "semantic": 0.35,
        "recency": 0.15,
        "structural": 0.05,
    }
    assert policy["ranking"]["lexical_fallback"] == {
        "lexical": 0.70,
        "recency": 0.20,
        "structural": 0.10,
    }
    assert policy["hashing"]["algorithm"] == "sha256"
    assert policy["token_estimation"]["non_cjk_characters_per_token"] == 4
    mobile = policy["mobile_projection"]
    assert mobile["retrieval"] == "model_driven_local_lexical_fallback"
    assert mobile["fts_available"] is False
    assert mobile["semantic_embeddings_available"] is False
    assert mobile["pinned_chunks_available"] is False
    assert mobile["manifest_persistence"] == "session_only"
