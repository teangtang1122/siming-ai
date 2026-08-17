from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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
        "context_window_tokens": 16_384,
        "safety_margin_tokens": 512,
        "minimum_output_reserve_tokens": 2_048,
        "output_ratio": 0.45,
    }
    assert policy["categories"]["previous_summary"]["max_items"] == 3
    assert policy["categories"]["scene_character"]["max_items"] == 12
    assert policy["categories"]["hybrid_retrieval"]["max_items"] == 24


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
    assert mobile["retrieval"] == "deterministic_local_lexical_fallback"
    assert mobile["fts_available"] is False
    assert mobile["semantic_embeddings_available"] is False
    assert mobile["pinned_chunks_available"] is False
    assert mobile["manifest_persistence"] == "session_only"
