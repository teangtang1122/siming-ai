"""Serialization, validation, and generic search persistence for manifests."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..database.models import ContextManifest, ContextManifestItem
from .task_context_selection import (
    MODEL_SELECTED_TASK_TYPES,
    TASK_CONTEXT_SOFT_TARGET_TOKENS,
    generation_items,
    selection_state,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _item_payload(item: ContextManifestItem, include_content: bool) -> dict[str, Any]:
    payload = {
        "id": item.id,
        "category": item.category,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "chunk_id": item.chunk_id,
        "source_hash": item.source_hash,
        "title": item.title,
        "required": bool(item.required),
        "pinned": bool(item.pinned),
        "tier": item.tier,
        "scores": {
            "lexical": item.lexical_score,
            "semantic": item.semantic_score,
            "recency": item.recency_score,
            "structural": item.structural_score,
            "final": item.final_score,
        },
        "selection_reason": item.selection_reason,
        "estimated_tokens": item.estimated_tokens,
        "evidence_submitted_at": _iso(item.evidence_submitted_at),
    }
    if include_content:
        payload["content"] = item.content_excerpt
    return payload


def manifest_payload(manifest: ContextManifest, include_content: bool = True) -> dict[str, Any]:
    input_budget = int(manifest.input_budget_tokens or 0)
    estimated_tokens = int(manifest.estimated_input_tokens or 0)
    soft_target = (
        min(
            TASK_CONTEXT_SOFT_TARGET_TOKENS,
            input_budget or TASK_CONTEXT_SOFT_TARGET_TOKENS,
        )
        if manifest.task_type in MODEL_SELECTED_TASK_TYPES
        else None
    )
    return {
        "id": manifest.id,
        "project_id": manifest.project_id,
        "session_id": manifest.session_id,
        "task_type": manifest.task_type,
        "model": manifest.model,
        "provider": manifest.provider,
        "execution_route": manifest.execution_route,
        "policy_version": manifest.policy_version,
        "status": manifest.status,
        "budget": {
            "context_window_tokens": int(manifest.context_window_tokens or 0),
            "input_budget_tokens": input_budget,
            "input_budget_mode": "model_window_minus_output_reserve_and_safety_margin",
            "soft_input_target_tokens": soft_target,
            "soft_target_exceeded": bool(
                soft_target is not None and estimated_tokens > soft_target
            ),
            "output_reserve_tokens": int(manifest.output_reserve_tokens or 0),
            "safety_margin_tokens": int(manifest.safety_margin_tokens or 0),
            "estimated_input_tokens": estimated_tokens,
            "estimated_input_chars": int(manifest.estimated_input_chars or 0),
            "remaining_input_tokens": max(0, input_budget - estimated_tokens),
        },
        "coverage": manifest.coverage_json or {},
        "warnings": manifest.warnings_json or [],
        "contract": manifest.contract_json or {},
        "selection": selection_state(manifest),
        "override": {
            "reason": manifest.override_reason,
            "actor": manifest.override_actor,
            "at": _iso(manifest.overridden_at),
        },
        "stale_reason": manifest.stale_reason,
        "items": [_item_payload(item, include_content) for item in manifest.items],
        "rendered_context": manifest.rendered_context if include_content else "",
        "created_at": _iso(manifest.created_at),
        "updated_at": _iso(manifest.updated_at),
        "last_validated_at": _iso(manifest.last_validated_at),
    }


def validate_manifest(
    db: Session,
    manifest: ContextManifest,
    *,
    require_external_evidence: bool,
    source_hash: Callable[[str | None, str, str | None, str], str | None],
) -> tuple[bool, str]:
    if manifest.status == "blocked_rebuild":
        return False, "Context rebuild is still in progress for this project."
    if manifest.status == "needs_confirmation":
        return False, "Required context is missing; confirm an override or narrow the task."
    if manifest.status == "stale":
        return False, manifest.stale_reason or "The context sources have changed."
    if manifest.status not in {"ready", "overridden"}:
        return False, f"Manifest is not usable: {manifest.status}."
    for item in generation_items(manifest):
        current = source_hash(
            manifest.project_id,
            item.source_type,
            item.source_id,
            item.content_excerpt,
        )
        if item.source_hash and item.source_hash != current:
            manifest.status = "stale"
            manifest.stale_reason = (
                f"Source changed: {item.title}"
                if current is not None
                else f"Source is unavailable: {item.title}"
            )
            manifest.last_validated_at = datetime.utcnow()
            db.flush()
            return False, manifest.stale_reason
    if require_external_evidence and manifest.task_type in MODEL_SELECTED_TASK_TYPES:
        selection = selection_state(manifest)
        if selection.get("status") != "ready" or not selection.get("token"):
            return False, "The Agent must finalize its selected context evidence first."
    elif require_external_evidence:
        missing = [
            item.title
            for item in manifest.items
            if item.required and item.evidence_submitted_at is None
        ]
        if missing:
            return False, (
                "External Agent must submit verified evidence for every required context anchor: "
                + ", ".join(missing[:6])
            )
    manifest.last_validated_at = datetime.utcnow()
    db.flush()
    return True, ""


def persist_search_candidates(
    db: Session,
    manifest: ContextManifest,
    candidates: Sequence[Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows = list(candidates)[: max(1, min(limit, 40))]
    existing = {item.chunk_id: item for item in manifest.items if item.chunk_id}
    next_order = max((item.sort_order for item in manifest.items), default=-1) + 1
    payload: list[dict[str, Any]] = []
    for candidate in rows:
        item = existing.get(candidate.chunk_id)
        if item is None:
            item = ContextManifestItem(
                manifest_id=manifest.id,
                project_id=manifest.project_id,
                category="agent_search",
                source_type=candidate.source_type,
                source_id=candidate.source_id,
                chunk_id=candidate.chunk_id,
                source_hash=candidate.source_hash,
                title=candidate.title,
                content_excerpt=candidate.content,
                tier=4,
                lexical_score=candidate.lexical_score,
                semantic_score=candidate.semantic_score,
                recency_score=candidate.recency_score,
                structural_score=candidate.structural_score,
                final_score=candidate.final_score,
                selection_reason="Verified Agent task-context search result. "
                + candidate.selection_reason,
                estimated_tokens=candidate.estimated_tokens,
                sort_order=next_order,
            )
            next_order += 1
            manifest.items.append(item)
            db.flush()
        payload.append(
            {
                "item_id": item.id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "chunk_id": item.chunk_id,
                "source_hash": item.source_hash,
                "title": item.title,
                "excerpt": item.content_excerpt[:600],
                "estimated_chunk_tokens": item.estimated_tokens,
                "scores": {
                    "lexical": item.lexical_score,
                    "semantic": item.semantic_score,
                    "recency": item.recency_score,
                    "structural": item.structural_score,
                    "final": item.final_score,
                },
            }
        )
    db.flush()
    return payload


__all__ = ["manifest_payload", "persist_search_candidates", "validate_manifest"]
