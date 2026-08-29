"""Portable projection of the PC model-selected ContextManifest policy.

Thin clients consume this generated contract instead of maintaining a second
set of context tiers, budget defaults, ranking weights, or stale rules.
"""
from __future__ import annotations

from typing import Any

from .context_orchestrator import (
    CONTEXT_INDEX_VERSION,
    CONTEXT_POLICY_VERSION,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_SAFETY_MARGIN_TOKENS,
    TASK_CONTEXT_CONTRACTS,
)
from .task_context_selection import (
    TASK_CONTEXT_SEARCH_EXCERPT_CHARS,
    TASK_CONTEXT_SEARCH_SOURCE_TYPES,
    TASK_CONTEXT_SOFT_TARGET_TOKENS,
)


def portable_context_policy(task_type: str = "writing") -> dict[str, Any]:
    """Return deterministic context-selection rules safe to embed on Android.

    PC-only capabilities (FTS, semantic embeddings, pinned chunks and durable
    audit rows) are explicitly described as unavailable rather than silently
    reimplemented with different semantics.
    """
    contract = TASK_CONTEXT_CONTRACTS[task_type]
    categories: dict[str, dict[str, Any]] = {
        "style": {
            "tier": 1,
            "max_items": 1,
            "required": "style" in contract.required_categories,
            "source_type": "project_style",
        },
        "user_requirement": {
            "tier": 2,
            "max_items": 1,
            "required": False,
            "source_type": "inline",
            "content_limit_chars": 4_000,
        },
    }
    if task_type == "writing":
        categories["target_outline"] = {
            "tier": 1,
            "max_items": 1,
            "required": True,
            "source_type": "outline",
            "field_limit_chars": 1_200,
        }
    elif task_type == "outline_planning":
        categories.update(
            {
                "outline_parent": {
                    "tier": 1,
                    "max_items": 1,
                    "required": False,
                    "source_type": "outline",
                    "field_limit_chars": 1_200,
                },
                "outline_position": {
                    "tier": 1,
                    "max_items": 1,
                    "required": True,
                    "source_type": "inline",
                    "content_limit_chars": 2_000,
                },
            }
        )
    return {
        "schema_version": 1,
        "policy_version": CONTEXT_POLICY_VERSION,
        "index_version": CONTEXT_INDEX_VERSION,
        "task_type": contract.task_type,
        "contract": {
            "required_categories": list(contract.required_categories),
            "optional_categories": list(contract.optional_categories),
        },
        "model_defaults": {
            "context_window_tokens": DEFAULT_CONTEXT_WINDOW_TOKENS,
            "safety_margin_tokens": DEFAULT_SAFETY_MARGIN_TOKENS,
            "minimum_output_reserve_tokens": 2_048,
            "output_ratio": contract.output_ratio,
            "soft_input_target_tokens": TASK_CONTEXT_SOFT_TARGET_TOKENS,
            "input_budget_mode": "model_window_minus_output_reserve_and_safety_margin",
        },
        "categories": categories,
        "selection": {
            "mode": "model_retrieval_then_exact_selection",
            "hard_source_count_limit": None,
            "search_excerpt_chars": TASK_CONTEXT_SEARCH_EXCERPT_CHARS,
            "search_page_limit": 20,
            "search_source_types": sorted(TASK_CONTEXT_SEARCH_SOURCE_TYPES),
            "exact_source_content_limit_chars": None,
            "ordering": ["tier", "required_first", "score_desc", "title"],
            "dedupe_identity": ["source_type", "source_id", "chunk_id"],
            "required_over_budget_status": "needs_confirmation",
            "unknown_model_uses_defaults": True,
        },
        "ranking": {
            "hybrid": {
                "lexical": 0.45,
                "semantic": 0.35,
                "recency": 0.15,
                "structural": 0.05,
            },
            "lexical_fallback": {
                "lexical": 0.70,
                "recency": 0.20,
                "structural": 0.10,
            },
        },
        "token_estimation": {
            "cjk_ranges": ["\\u4e00-\\u9fff", "\\u3400-\\u4dbf"],
            "cjk_tokens_per_character": 1,
            "non_cjk_characters_per_token": 4,
            "minimum_non_cjk_tokens_for_non_empty_text": 1,
        },
        "hashing": {
            "algorithm": "sha256",
            "encoding": "utf-8",
            "selected_content_hash": True,
            "selection_fingerprint_fields": [
                "category",
                "source_type",
                "source_id",
                "chunk_id",
                "source_hash",
            ],
        },
        "mobile_projection": {
            "execution_route": "android_standalone",
            "retrieval": "model_driven_local_lexical_fallback",
            "style_projection": "pc_prompt_contract_style_context",
            "fts_available": False,
            "semantic_embeddings_available": False,
            "pinned_chunks_available": False,
            "durable_manifest_audit_available": False,
            "manifest_persistence": "session_only",
            "stale_on_model_change": True,
            "stale_on_request_change": True,
            "stale_on_selected_source_change": True,
        },
    }
