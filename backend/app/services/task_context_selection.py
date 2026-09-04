"""Model-driven exact evidence selection for generation context manifests."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..database.models import ContextManifest, ContextManifestItem
from .rag.context_packer import estimate_tokens
from .task_context_delivery import (
    context_delivery_ready,
    set_context_delivery_state,
)
from .task_context_sources import (
    ExactTaskContextSource,
    TaskContextSourceResolver,
)
from .task_context_sources import (
    clean_context_text as _clean_text,
)

MODEL_SELECTED_TASK_TYPES = frozenset({"writing", "outline_planning"})
TASK_CONTEXT_SOFT_TARGET_TOKENS = 32_000
TASK_CONTEXT_SEARCH_EXCERPT_CHARS = 600
TASK_CONTEXT_SEARCH_PAGE_LIMIT = 10
TASK_CONTEXT_SEARCH_MAX_CURSOR = 20
TASK_CONTEXT_SEARCH_SOURCE_TYPES = frozenset(
    {
        "chapter",
        "chapter_summary",
        "outline",
        "character",
        "character_timeline",
        "worldbuilding",
        "assistant_memory",
        "narrative_governance",
    }
)
TASK_CONTEXT_ANCHOR_CATEGORIES = frozenset(
    {
        "style",
        "target_outline",
        "target_draft",
        "outline_parent",
        "outline_position",
        "user_requirement",
        "pinned",
    }
)


def selection_state(manifest: ContextManifest) -> dict[str, Any]:
    query = manifest.query_json if isinstance(manifest.query_json, dict) else {}
    selection = query.get("context_selection")
    if isinstance(selection, dict):
        return dict(selection)
    return {
        "status": "not_required",
        "token": None,
        "selected_item_ids": [],
        "selected_at": None,
    }


def set_selection_state(manifest: ContextManifest, selection: dict[str, Any]) -> None:
    query = dict(manifest.query_json or {})
    query["context_selection"] = selection
    manifest.query_json = query


def generation_items(manifest: ContextManifest) -> list[ContextManifestItem]:
    """Return only anchors, explicit pins, and finalized exact evidence."""
    if manifest.task_type not in MODEL_SELECTED_TASK_TYPES:
        return list(manifest.items)
    selected_ids = {
        str(value) for value in selection_state(manifest).get("selected_item_ids", []) if str(value)
    }
    return [
        item
        for item in manifest.items
        if item.category in TASK_CONTEXT_ANCHOR_CATEGORIES
        or (item.category == "agent_selected" and item.id in selected_ids)
    ]


def render_generation_context(manifest: ContextManifest) -> str:
    groups: dict[str, list[ContextManifestItem]] = {}
    for item in sorted(generation_items(manifest), key=lambda value: value.sort_order):
        groups.setdefault(item.category, []).append(item)
    parts = ["# Governed Task Context"]
    for category, items in groups.items():
        parts.append(f"\n## {category}")
        for item in items:
            parts.append(f"### {item.title}\n{item.content_excerpt}")
    return "\n\n".join(parts).strip()


class TaskContextSelector:
    """Own the shared generation evidence state machine and source expansion."""

    def __init__(self, db: Session):
        self.db = db
        self.source_resolver = TaskContextSourceResolver(db)

    def clear(self, manifest: ContextManifest) -> None:
        if manifest.task_type not in MODEL_SELECTED_TASK_TYPES:
            return
        for item in list(manifest.items):
            if item.category == "agent_selected":
                manifest.items.remove(item)
            elif item.category == "agent_search":
                item.evidence_submitted_at = None
        coverage = dict(manifest.coverage_json or {})
        coverage["agent_selection"] = {
            "required": False,
            "status": "pending",
            "item_count": 0,
            "reason": "The Agent must review and finalize retrieved evidence.",
        }
        manifest.coverage_json = coverage
        manifest.consumed_at = None
        set_context_delivery_state(manifest, None)
        set_selection_state(
            manifest,
            {
                "status": "pending",
                "token": None,
                "selected_item_ids": [],
                "selected_at": None,
            },
        )
        self.db.flush()
        self._refresh(manifest)

    def _refresh(self, manifest: ContextManifest) -> None:
        items = generation_items(manifest)
        manifest.estimated_input_tokens = sum(
            int(item.estimated_tokens or estimate_tokens(item.content_excerpt)) for item in items
        )
        manifest.estimated_input_chars = sum(len(item.content_excerpt) for item in items)
        manifest.rendered_context = render_generation_context(manifest)

    def _resolve_sources(
        self,
        manifest: ContextManifest,
        sources: Sequence[dict[str, Any]],
    ) -> tuple[list[tuple[ContextManifestItem, ExactTaskContextSource]], list[dict[str, Any]]]:
        search_items = [item for item in manifest.items if item.category == "agent_search"]
        by_id: dict[str, list[ContextManifestItem]] = {}
        by_chunk: dict[str, list[ContextManifestItem]] = {}
        by_source: dict[tuple[str, str], list[ContextManifestItem]] = {}
        for item in search_items:
            by_id.setdefault(item.id, []).append(item)
            if item.chunk_id:
                by_chunk.setdefault(item.chunk_id, []).append(item)
            if item.source_id:
                by_source.setdefault((item.source_type, item.source_id), []).append(item)
        chosen: list[tuple[ContextManifestItem, ExactTaskContextSource]] = []
        rejected: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for source in sources:
            if not isinstance(source, dict):
                rejected.append({"source": source, "reason": "Evidence must be an object."})
                continue
            def field(name: str, current_source: dict[str, Any] = source) -> str:
                value = current_source.get(name)
                return value.strip() if isinstance(value, str) else ""

            item_id = field("item_id")
            chunk_id = field("chunk_id")
            source_type = field("source_type")
            source_id = field("source_id")
            source_hash = field("source_hash")
            if not item_id and not chunk_id and not (source_type and source_id):
                rejected.append(
                    {
                        "item_id": item_id or None,
                        "source_id": source_id or None,
                        "reason": (
                            "Source must include item_id, chunk_id, or both "
                            "source_type and source_id from search_task_context."
                        ),
                    }
                )
                continue

            if item_id:
                matches = list(by_id.get(item_id, []))
            elif chunk_id:
                matches = list(by_chunk.get(chunk_id, []))
            else:
                matches = list(by_source.get((source_type, source_id), []))
            matches = [
                item
                for item in matches
                if (not chunk_id or item.chunk_id == chunk_id)
                and (not source_type or item.source_type == source_type)
                and (not source_id or item.source_id == source_id)
            ]
            if not matches:
                rejected.append(
                    {
                        "item_id": item_id or None,
                        "source_id": source_id or None,
                        "reason": "Source is not a verified result from search_task_context.",
                    }
                )
                continue
            if source_hash:
                hash_matches = [item for item in matches if item.source_hash == source_hash]
                if not hash_matches:
                    rejected.append(
                        {
                            "item_id": matches[0].id,
                            "source_id": matches[0].source_id,
                            "reason": "Source hash does not match the verified search result.",
                        }
                    )
                    continue
                matches = hash_matches
            if len(matches) != 1:
                rejected.append(
                    {
                        "item_id": item_id or None,
                        "source_id": source_id or None,
                        "reason": "Source reference is ambiguous; submit its unique item_id.",
                    }
                )
                continue
            item = matches[0]
            identity = (item.source_type, str(item.source_id or item.id))
            if identity in seen:
                continue
            exact = self.source_resolver.exact_source(manifest, item)
            if exact is None or item.source_hash != exact.source_hash:
                rejected.append(
                    {
                        "item_id": item.id,
                        "source_id": item.source_id,
                        "reason": "Source changed after retrieval; search again.",
                    }
                )
                continue
            chosen.append((item, exact))
            seen.add(identity)
        return chosen, rejected

    @staticmethod
    def _fit_sources(
        manifest: ContextManifest,
        chosen: list[tuple[ContextManifestItem, ExactTaskContextSource]],
    ) -> tuple[list[tuple[ContextManifestItem, ExactTaskContextSource]], list[dict[str, Any]], int]:
        used = sum(
            int(item.estimated_tokens or estimate_tokens(item.content_excerpt))
            for item in manifest.items
            if item.category not in {"agent_search", "agent_selected"}
        )
        accepted: list[tuple[ContextManifestItem, ExactTaskContextSource]] = []
        rejected: list[dict[str, Any]] = []
        for item, source in chosen:
            if used + source.estimated_tokens > int(manifest.input_budget_tokens or 0):
                rejected.append(
                    {
                        "item_id": item.id,
                        "source_type": item.source_type,
                        "source_id": item.source_id,
                        "reason": (
                            "Exact source would consume the model input space reserved for "
                            "generation; reduce evidence or output reserve."
                        ),
                        "estimated_tokens": source.estimated_tokens,
                    }
                )
            else:
                used += source.estimated_tokens
                accepted.append((item, source))
        return accepted, rejected, used

    def _persist_selection(
        self,
        manifest: ContextManifest,
        pairs: list[tuple[ContextManifestItem, ExactTaskContextSource]],
    ) -> tuple[list[ContextManifestItem], str]:
        base_items = [item for item in manifest.items if item.category != "agent_search"]
        next_order = max((item.sort_order for item in base_items), default=-1) + 1
        now = datetime.utcnow()
        selected: list[ContextManifestItem] = []
        for search_item, source in pairs:
            search_item.evidence_submitted_at = now
            item = ContextManifestItem(
                manifest_id=manifest.id,
                project_id=manifest.project_id,
                category="agent_selected",
                source_type=source.source_type,
                source_id=source.source_id,
                source_hash=source.source_hash,
                title=_clean_text(source.title, 300),
                content_excerpt=source.content,
                tier=3,
                lexical_score=source.lexical_score,
                semantic_score=source.semantic_score,
                recency_score=source.recency_score,
                structural_score=source.structural_score,
                final_score=source.final_score,
                selection_reason=("Exact source selected by the Agent after retrieval review."),
                estimated_tokens=source.estimated_tokens,
                evidence_submitted_at=now,
                sort_order=next_order,
            )
            next_order += 1
            manifest.items.append(item)
            selected.append(item)
        self.db.flush()
        token = secrets.token_urlsafe(24)
        set_selection_state(
            manifest,
            {
                "status": "ready",
                "token": token,
                "selected_item_ids": [item.id for item in selected],
                "selected_at": now.isoformat(),
            },
        )
        coverage = dict(manifest.coverage_json or {})
        coverage["agent_selection"] = {
            "required": False,
            "status": "covered",
            "item_count": len(selected),
            "reason": "Exact sources were selected by the Agent after retrieval review.",
        }
        manifest.coverage_json = coverage
        self._refresh(manifest)
        soft_target = min(
            TASK_CONTEXT_SOFT_TARGET_TOKENS,
            int(manifest.input_budget_tokens or TASK_CONTEXT_SOFT_TARGET_TOKENS),
        )
        soft_warning_prefix = "Task context exceeded the soft target:"
        warnings = [
            warning
            for warning in (manifest.warnings_json or [])
            if not str(warning).startswith(soft_warning_prefix)
        ]
        if int(manifest.estimated_input_tokens or 0) > soft_target:
            warnings.append(
                f"{soft_warning_prefix} {manifest.estimated_input_tokens}/{soft_target} tokens. "
                "The Agent explicitly selected this evidence; generation may continue."
            )
        manifest.warnings_json = warnings[:100]
        self.db.flush()
        return selected, token

    def submit(
        self,
        manifest: ContextManifest,
        sources: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        self.clear(manifest)
        soft_target = min(
            TASK_CONTEXT_SOFT_TARGET_TOKENS,
            int(manifest.input_budget_tokens or TASK_CONTEXT_SOFT_TARGET_TOKENS),
        )
        chosen, rejected = self._resolve_sources(manifest, sources)
        accepted_pairs, budget_rejections, used = self._fit_sources(manifest, chosen)
        rejected.extend(budget_rejections)
        if rejected:
            return {
                "accepted": [],
                "rejected": rejected,
                "accepted_count": 0,
                "selection_ready": False,
                "input_budget_tokens": int(manifest.input_budget_tokens or 0),
                "estimated_tokens_if_accepted": used,
                "soft_target_tokens": soft_target,
            }
        selected, token = self._persist_selection(manifest, accepted_pairs)
        soft_target_exceeded = int(manifest.estimated_input_tokens or 0) > soft_target
        accepted = [
            {
                "item_id": item.id,
                "source_type": item.source_type,
                "source_id": item.source_id,
                "source_hash": item.source_hash,
                "title": item.title,
                "estimated_tokens": item.estimated_tokens,
            }
            for item in selected
        ]
        return {
            "accepted": accepted,
            "rejected": [],
            "accepted_count": len(accepted),
            "selection_ready": True,
            "context_selection_token": token,
            "task_context": manifest.rendered_context,
            "estimated_input_tokens": int(manifest.estimated_input_tokens or 0),
            "input_budget_tokens": int(manifest.input_budget_tokens or 0),
            "soft_target_tokens": soft_target,
            "soft_target_exceeded": soft_target_exceeded,
            "warnings": list(manifest.warnings_json or []),
        }

    @staticmethod
    def validate_token(
        manifest: ContextManifest,
        token: str,
        *,
        task_type: str,
        outline_node_id: str | None = None,
        source_draft_id: str | None = None,
        parent_id: str | None = None,
        insert_after_id: str | None = None,
    ) -> tuple[bool, str]:
        if manifest.consumed_at is not None:
            return (
                False,
                "context_selection_token has already been consumed; search and submit "
                "evidence again before generation.",
            )
        selection = selection_state(manifest)
        expected_token = str(selection.get("token") or "")
        if selection.get("status") != "ready" or not expected_token:
            return False, "Search as needed and submit the exact task evidence before generation."
        if not token or not secrets.compare_digest(expected_token, token):
            return (
                False,
                "context_selection_token is missing or stale; use the token returned by "
                "submit_context_evidence.",
            )
        if not context_delivery_ready(manifest, expected_token):
            return (
                False,
                "Selected context pages have not been read completely in order; continue "
                "with prepare_task_context and its exact next_arguments before generation.",
            )
        if manifest.task_type != task_type:
            return False, "The context manifest task does not match the requested generator."
        if task_type == "writing":
            target_ids = {
                str(item.source_id)
                for item in generation_items(manifest)
                if item.category == "target_outline" and item.source_id
            }
            if not outline_node_id or outline_node_id not in target_ids:
                return False, "The context manifest target does not match the chapter outline."
            query = manifest.query_json if isinstance(manifest.query_json, dict) else {}
            arguments = query.get("arguments") if isinstance(query.get("arguments"), dict) else {}
            expected_draft_id = str(arguments.get("source_draft_id") or "")
            if str(source_draft_id or "") != expected_draft_id:
                return False, "The context manifest pending draft does not match this revision."
            if expected_draft_id:
                draft_ids = {
                    str(item.source_id)
                    for item in generation_items(manifest)
                    if item.category == "target_draft" and item.source_id
                }
                if expected_draft_id not in draft_ids:
                    return False, "The pending draft revision source is missing from task context."
        elif task_type == "outline_planning":
            query = manifest.query_json if isinstance(manifest.query_json, dict) else {}
            arguments = query.get("arguments") if isinstance(query.get("arguments"), dict) else {}
            expected_parent = str(arguments.get("parent_id") or "")
            expected_after = str(arguments.get("insert_after_id") or "")
            if (
                str(parent_id or "") != expected_parent
                or str(insert_after_id or "") != expected_after
            ):
                return False, "The context manifest outline position does not match this draft."
        return True, ""

    def search(
        self,
        manifest: ContextManifest,
        *,
        query: str,
        limit: int,
        offset: int = 0,
        source_types: Sequence[str],
        hybrid_search: Callable[[Sequence[str]], list[Any]],
        include_next_probe: bool = False,
    ) -> list[dict[str, Any]]:
        self.clear(manifest)
        requested = {str(value).strip() for value in source_types if str(value).strip()}
        effective = sorted(
            (requested & TASK_CONTEXT_SEARCH_SOURCE_TYPES)
            if requested
            else TASK_CONTEXT_SEARCH_SOURCE_TYPES
        )
        if not effective:
            return []
        candidates: list[Any] = []
        if "narrative_governance" in effective:
            governance = self.source_resolver.governance_candidate(manifest)
            if governance:
                candidates.append(governance)
        retrieval_types = [value for value in effective if value != "narrative_governance"]
        if retrieval_types:
            candidates.extend(hybrid_search(retrieval_types))
        # Selection expands a chosen result to the complete authoritative
        # source, so several RAG chunks from the same entity are not distinct
        # evidence choices.  Collapse them before pagination; otherwise one
        # character or chapter can occupy most of a search page and hide other
        # relevant entities.
        unique_candidates: list[Any] = []
        seen_sources: set[tuple[str, str]] = set()
        for candidate in candidates:
            source_type = str(getattr(candidate, "source_type", "") or "")
            source_id = str(getattr(candidate, "source_id", "") or "")
            identity = (
                source_type,
                source_id
                or str(getattr(candidate, "chunk_id", "") or "")
                or str(getattr(candidate, "source_hash", "") or ""),
            )
            if identity in seen_sources:
                continue
            seen_sources.add(identity)
            unique_candidates.append(candidate)
        candidates = unique_candidates
        page_size = max(1, min(limit, TASK_CONTEXT_SEARCH_PAGE_LIMIT))
        page_offset = max(0, min(offset, TASK_CONTEXT_SEARCH_MAX_CURSOR))
        fetch_size = page_size + int(include_next_probe)
        candidates = candidates[page_offset : page_offset + fetch_size]
        existing = {
            (item.source_type, item.source_id, item.chunk_id, item.source_hash): item
            for item in manifest.items
            if item.category == "agent_search"
        }
        next_order = max((item.sort_order for item in manifest.items), default=-1) + 1
        payload: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_title = candidate.title
            candidate_content = candidate.content
            # RAG is a discovery index, never the authority shown to the Agent.
            # A committed entity edit can race an index refresh; presenting the
            # old chunk here would leak superseded character/world/chapter data
            # into the model conversation even though evidence submission later
            # expands the current record. Hydrate every mutable search hit from
            # its authoritative source before persisting or returning a preview.
            exact_preview = self.source_resolver.exact_identity_source(
                manifest,
                candidate.source_type,
                candidate.source_id,
            )
            if exact_preview and exact_preview.source_hash == candidate.source_hash:
                candidate_title = exact_preview.title
                candidate_content = exact_preview.content
            key = (
                candidate.source_type,
                candidate.source_id,
                getattr(candidate, "chunk_id", None),
                candidate.source_hash,
            )
            item = existing.get(key)
            if item is None:
                item = ContextManifestItem(
                    manifest_id=manifest.id,
                    project_id=manifest.project_id,
                    category="agent_search",
                    source_type=candidate.source_type,
                    source_id=candidate.source_id,
                    chunk_id=getattr(candidate, "chunk_id", None),
                    source_hash=candidate.source_hash,
                    title=candidate_title,
                    content_excerpt=candidate_content,
                    tier=4,
                    lexical_score=getattr(candidate, "lexical_score", None),
                    semantic_score=getattr(candidate, "semantic_score", None),
                    recency_score=getattr(candidate, "recency_score", None),
                    structural_score=getattr(candidate, "structural_score", None),
                    final_score=float(getattr(candidate, "final_score", 0) or 0),
                    selection_reason="Verified Agent task-context search result.",
                    estimated_tokens=estimate_tokens(candidate_content),
                    sort_order=next_order,
                )
                next_order += 1
                manifest.items.append(item)
                self.db.flush()
            payload.append(
                {
                    "item_id": item.id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "chunk_id": item.chunk_id,
                    "source_hash": item.source_hash,
                    "title": _clean_text(item.title, 100),
                    "excerpt": _clean_text(item.content_excerpt, TASK_CONTEXT_SEARCH_EXCERPT_CHARS),
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
        self.db.flush()
        return payload


__all__ = [
    "MODEL_SELECTED_TASK_TYPES",
    "TASK_CONTEXT_SOFT_TARGET_TOKENS",
    "TASK_CONTEXT_SEARCH_EXCERPT_CHARS",
    "TASK_CONTEXT_SEARCH_MAX_CURSOR",
    "TASK_CONTEXT_SEARCH_PAGE_LIMIT",
    "TASK_CONTEXT_SEARCH_SOURCE_TYPES",
    "TaskContextSelector",
    "generation_items",
    "selection_state",
]
