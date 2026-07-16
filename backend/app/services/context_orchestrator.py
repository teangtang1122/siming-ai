"""Auditable, budgeted task-context orchestration.

This module is deliberately independent from individual workspace tools.  A
tool asks for a manifest, renders the same manifest for any execution route,
and records the exact source hashes that justified the response.  That gives
API models, CLI-as-model execution, and external MCP agents one context
contract instead of three unrelated prompt assemblers.
"""
from __future__ import annotations

from array import array
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import event, or_
from sqlalchemy.orm import Session

from ..database.models import (
    AssistantMemory,
    Chapter,
    ChapterCharacter,
    ChapterSummary,
    Character,
    CharacterNarrativeState,
    CharacterTimeline,
    ChapterQualityMetric,
    CausalEdge,
    ContextManifest,
    ContextManifestItem,
    ContextRebuildJob,
    ContextRebuildProject,
    Foreshadowing,
    ModelContextProfile,
    NarrativeDebt,
    OutlineNode,
    OutlineNodeCharacter,
    Project,
    RagChunk,
    RagChunkEmbedding,
    WorldbuildingEntry,
)
from .rag.context_packer import ContextBudget, estimate_tokens
from .rag.indexer import _get_source_content_hash, reindex_project
from .rag.retriever import search_chunks


CONTEXT_POLICY_VERSION = 1
CONTEXT_INDEX_VERSION = 1
DEFAULT_CONTEXT_WINDOW_TOKENS = 16_384
DEFAULT_SAFETY_MARGIN_TOKENS = 512
LOCAL_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
MANIFEST_STATUSES = {
    "ready",
    "needs_confirmation",
    "overridden",
    "stale",
    "blocked_rebuild",
}


_MANIFEST_INVALIDATION_KEY = "siming_context_manifest_source_changes"
_MANIFEST_INVALIDATION_GUARD = "siming_context_manifest_invalidation_running"


@dataclass(frozen=True)
class ActiveContextManifest:
    """Request-local manifest rendered once for a gateway execution."""

    manifest_id: str
    rendered_context: str
    output_reserve_tokens: int


_ACTIVE_CONTEXT_MANIFEST: ContextVar[ActiveContextManifest | None] = ContextVar(
    "siming_active_context_manifest",
    default=None,
)


@contextmanager
def activate_context_manifest(manifest: ContextManifest):
    """Bind a manifest to nested internal gateway calls for one task."""
    active = ActiveContextManifest(
        manifest_id=manifest.id,
        rendered_context=manifest.rendered_context or "",
        output_reserve_tokens=manifest.output_reserve_tokens,
    )
    token = _ACTIVE_CONTEXT_MANIFEST.set(active)
    try:
        yield active
    finally:
        _ACTIVE_CONTEXT_MANIFEST.reset(token)


def active_context_manifest() -> ActiveContextManifest | None:
    """Return the request-local manifest selected by the workspace executor."""
    return _ACTIVE_CONTEXT_MANIFEST.get()


def _sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _normalise_score(value: float | None, values: Sequence[float]) -> float:
    if value is None:
        return 0.0
    if not values:
        return 0.0
    low = min(values)
    high = max(values)
    if math.isclose(low, high):
        return 1.0 if value > 0 else 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _task_key(task_type: str) -> str:
    value = (task_type or "").strip().lower().replace("-", "_")
    if value in {"chapter_writer", "write", "writing", "chapter_writing", "external_writing"}:
        return "writing"
    if value in {"cataloging", "catalogue", "archive", "build_archive"}:
        return "cataloging"
    if value in {"review", "evaluate", "evaluation", "chapter_review"}:
        return "review"
    if value in {"rewrite", "expand", "continue", "text_operation", "text_operations"}:
        return "rewrite"
    if value in {"novel_creation", "new_project", "concepts", "planning_session"}:
        return "new_project"
    return "planning"


@dataclass(frozen=True)
class TaskContextContract:
    """Code-owned contract for a task family.

    ``required_categories`` are structural anchors.  A category can still be
    marked ``not_applicable`` for a genuinely empty project (for example there
    is no previous chapter for chapter one); a present-but-unresolved anchor is
    a recoverable ``needs_confirmation`` state.
    """

    task_type: str
    required_categories: tuple[str, ...]
    optional_categories: tuple[str, ...] = ()
    output_ratio: float = 0.30


TASK_CONTEXT_CONTRACTS: dict[str, TaskContextContract] = {
    "writing": TaskContextContract(
        task_type="writing",
        required_categories=("target_outline", "style"),
        optional_categories=(
            "user_requirement",
            "previous_summary",
            "scene_character",
            "worldbuilding",
            "narrative_governance",
            "memory",
            "skill",
            "hybrid_retrieval",
        ),
        output_ratio=0.45,
    ),
    "cataloging": TaskContextContract(
        task_type="cataloging",
        required_categories=("target_chapter",),
        optional_categories=("confirmed_fact", "adjacent_summary", "worldbuilding", "scene_character"),
        output_ratio=0.25,
    ),
    "review": TaskContextContract(
        task_type="review",
        required_categories=("target_text",),
        optional_categories=("scene_character", "worldbuilding", "previous_summary", "narrative_governance"),
        output_ratio=0.25,
    ),
    "rewrite": TaskContextContract(
        task_type="rewrite",
        required_categories=("target_text",),
        optional_categories=("user_requirement", "scene_character", "worldbuilding", "previous_summary"),
        output_ratio=0.20,
    ),
    "new_project": TaskContextContract(
        task_type="new_project",
        required_categories=("creation_session",),
        optional_categories=("confirmed_stage", "author_constraint"),
        output_ratio=0.30,
    ),
    "planning": TaskContextContract(
        task_type="planning",
        required_categories=("style",),
        optional_categories=("target_outline", "previous_summary", "scene_character", "worldbuilding", "narrative_governance"),
        output_ratio=0.30,
    ),
}


@dataclass(frozen=True)
class ResolvedModelContextProfile:
    provider: str
    model_name: str
    context_window_tokens: int
    max_output_tokens: int | None
    safety_margin_tokens: int
    known: bool


@dataclass
class ManifestCandidate:
    category: str
    source_type: str
    source_id: str | None
    title: str
    content: str
    required: bool = False
    pinned: bool = False
    tier: int = 4
    lexical_score: float | None = None
    semantic_score: float | None = None
    recency_score: float | None = None
    structural_score: float | None = None
    final_score: float = 0.0
    selection_reason: str = ""
    chunk_id: str | None = None
    source_hash: str | None = None
    applicable: bool = True

    @property
    def estimated_tokens(self) -> int:
        return estimate_tokens(self.content)


class LocalSemanticRuntime:
    """Optional local FastEmbed runtime.

    Importing FastEmbed is intentionally the only prerequisite check.  No
    cloud embedding endpoint is used.  A model download, if the author has
    installed FastEmbed and selected semantic indexing, remains a local model
    runtime operation handled by FastEmbed itself.
    """

    _model: Any = None
    _initialisation_error: str | None = None

    @classmethod
    def status(cls) -> dict[str, Any]:
        try:
            import fastembed  # noqa: F401
        except Exception as exc:
            return {
                "available": False,
                "model": LOCAL_EMBEDDING_MODEL,
                "reason": f"FastEmbed unavailable: {exc.__class__.__name__}",
            }
        if cls._initialisation_error:
            return {
                "available": False,
                "model": LOCAL_EMBEDDING_MODEL,
                "reason": cls._initialisation_error,
            }
        return {"available": True, "model": LOCAL_EMBEDDING_MODEL, "reason": ""}

    @classmethod
    def _get_model(cls):
        if cls._model is not None:
            return cls._model
        try:
            from fastembed import TextEmbedding

            cls._model = TextEmbedding(model_name=LOCAL_EMBEDDING_MODEL)
            return cls._model
        except Exception as exc:
            cls._initialisation_error = f"FastEmbed could not initialise: {exc}"
            raise RuntimeError(cls._initialisation_error) from exc

    @classmethod
    def embed(cls, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = cls._get_model()
        vectors = model.embed(list(texts))
        return [[float(value) for value in vector] for vector in vectors]


def _pack_float32(vector: Sequence[float]) -> bytes:
    values = array("f", (float(value) for value in vector))
    return values.tobytes()


def _unpack_float32(blob: bytes, dimension: int) -> list[float]:
    if not blob or dimension <= 0:
        return []
    values = array("f")
    values.frombytes(blob)
    return [float(value) for value in values[:dimension]]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _project_style_text(project: Project) -> str:
    parts = [
        f"Project: {project.title}",
        f"Perspective: {project.narrative_perspective or 'unspecified'}",
        f"Writing style: {project.writing_style or 'natural'}",
    ]
    if project.description:
        parts.append(f"Project brief: {_clean_text(project.description, 900)}")
    if project.custom_style_prompt:
        parts.append(f"Custom style: {_clean_text(project.custom_style_prompt, 1200)}")
    if project.forbidden_sentence_patterns:
        parts.append(f"Avoid: {_clean_text(project.forbidden_sentence_patterns, 900)}")
    if project.rhetoric_guidelines:
        parts.append(f"Rhetoric: {_clean_text(project.rhetoric_guidelines, 900)}")
    return "\n".join(parts)


def _character_text(character: Character) -> str:
    fields = [
        ("Role", character.role_type),
        ("Personality", character.personality),
        ("Background", character.background),
        ("Goal", character.current_goal),
        ("Conflict", character.active_conflict),
        ("Location", character.current_location),
        ("Physical state", character.physical_state),
        ("Mental state", character.mental_state),
        ("Abilities", character.abilities_state or character.abilities),
    ]
    values = [f"Character: {character.name}"]
    values.extend(f"{label}: {_clean_text(value, 420)}" for label, value in fields if value)
    return "\n".join(values)


def _outline_text(node: OutlineNode) -> str:
    values = [f"Outline: {node.title}", f"Node type: {node.node_type or 'unknown'}"]
    for label, value in (
        ("Summary", node.summary),
        ("Planned", node.planned_summary),
        ("Actual", node.actual_summary),
        ("Status", node.status),
    ):
        if value:
            values.append(f"{label}: {_clean_text(value, 1200)}")
    return "\n".join(values)


def _chapter_text(chapter: Chapter, *, max_chars: int = 12_000) -> str:
    body = _clean_text(chapter.content, max_chars)
    summary = chapter.summary.summary_text if chapter.summary else ""
    values = [f"Chapter: {chapter.title}"]
    if summary:
        values.append(f"Summary: {_clean_text(summary, 1800)}")
    values.append(f"Text:\n{body}")
    return "\n".join(values)


def _current_source_hash(db: Session, project_id: str | None, source_type: str, source_id: str | None, fallback_content: str) -> str | None:
    if source_type in {
        "chapter",
        "chapter_summary",
        "outline",
        "character",
        "character_timeline",
        "worldbuilding",
        "assistant_memory",
    }:
        if not source_id:
            return None
        value = _get_source_content_hash(db, source_type, source_id)
        return value or None
    if source_type == "project_style" and project_id:
        project = db.query(Project).filter(Project.id == project_id).first()
        return _sha256(_project_style_text(project)) if project else None
    if source_type == "narrative_governance" and project_id:
        try:
            from .narrative_governance import governance_context

            return _sha256(governance_context(db, project_id, limit=12))
        except Exception:
            return _sha256(fallback_content)
    if source_type in {"inline", "creation_session", "author_constraint", "confirmed_stage", "memory", "skill"}:
        # Inline request data is immutable inside a manifest. It deliberately
        # has no mutable database source to invalidate against.
        return _sha256(fallback_content)
    return _sha256(fallback_content)


def _source_recency(db: Session, source_type: str, source_id: str | None) -> float:
    if not source_id:
        return 0.2
    models: dict[str, Any] = {
        "chapter": Chapter,
        "outline": OutlineNode,
        "character": Character,
        "worldbuilding": WorldbuildingEntry,
        "assistant_memory": AssistantMemory,
    }
    model = models.get(source_type)
    if not model:
        return 0.25
    row = db.query(model).filter(model.id == source_id).first()
    updated = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
    if not updated:
        return 0.25
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - updated).total_seconds() / 86_400)
    return max(0.05, min(1.0, 1.0 / (1.0 + age_days / 180)))


def _manifest_item_payload(item: ContextManifestItem, *, include_content: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
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
        data["content"] = item.content_excerpt
    return data


def _project_id_for_related_source(session: Session, model: type[Any], source_id: str | None) -> str | None:
    """Resolve a project ID for sources that only point to a chapter/character."""
    if not source_id:
        return None
    if model is Chapter:
        return session.query(Chapter.project_id).filter(Chapter.id == source_id).scalar()
    if model is Character:
        return session.query(Character.project_id).filter(Character.id == source_id).scalar()
    return None


def _changed_manifest_sources(session: Session, instance: Any) -> list[tuple[str, str, str | None]]:
    """Return manifest source identities affected by one persisted model object."""
    project_id = str(getattr(instance, "project_id", "") or "").strip()
    if isinstance(instance, Project):
        return [(str(instance.id), "project_style", str(instance.id))] if instance.id else []
    if isinstance(instance, Chapter):
        if not project_id or not instance.id:
            return []
        return [
            (project_id, "chapter", str(instance.id)),
            (project_id, "chapter_summary", str(instance.id)),
        ]
    if isinstance(instance, ChapterSummary):
        project_id = project_id or _project_id_for_related_source(session, Chapter, instance.chapter_id)
        return [(project_id, "chapter_summary", str(instance.chapter_id))] if project_id and instance.chapter_id else []
    if isinstance(instance, CharacterTimeline):
        project_id = project_id or _project_id_for_related_source(session, Character, instance.character_id)
        return [(project_id, "character_timeline", str(instance.character_id))] if project_id and instance.character_id else []
    if isinstance(instance, Character):
        return [(project_id, "character", str(instance.id))] if project_id and instance.id else []
    if isinstance(instance, WorldbuildingEntry):
        return [(project_id, "worldbuilding", str(instance.id))] if project_id and instance.id else []
    if isinstance(instance, AssistantMemory):
        return [(project_id, "assistant_memory", str(instance.id))] if project_id and instance.id else []
    if isinstance(instance, (Foreshadowing, CausalEdge, NarrativeDebt, CharacterNarrativeState, ChapterQualityMetric)):
        # Governance context is a project-level derived ledger, so any update
        # to one of its rows invalidates every manifest that rendered it.
        return [(project_id, "narrative_governance", None)] if project_id else []
    if isinstance(instance, OutlineNode):
        return [(project_id, "outline", str(instance.id))] if project_id and instance.id else []
    return []


def invalidate_context_manifests(
    db: Session,
    sources: Iterable[tuple[str, str, str | None]],
) -> int:
    """Mark ready manifests stale as soon as one of their sources changes.

    Hash validation remains the final authority for old manifests created before
    this listener existed.  This eager marker makes the audit trail truthful in
    the UI immediately after ordinary edits and prevents a stale external
    Agent write from slipping through on a later request.
    """
    changed = 0
    now = datetime.utcnow()
    for project_id, source_type, source_id in set(sources):
        if not project_id or not source_type:
            continue
        item_query = db.query(ContextManifestItem.manifest_id).filter(
            ContextManifestItem.project_id == project_id,
            ContextManifestItem.source_type == source_type,
        )
        if source_id:
            item_query = item_query.filter(ContextManifestItem.source_id == source_id)
        query = db.query(ContextManifest).filter(
            ContextManifest.id.in_(item_query),
            ContextManifest.project_id == project_id,
            ContextManifest.status.in_(("ready", "overridden", "needs_confirmation")),
        )
        changed += query.update(
            {
                ContextManifest.status: "stale",
                ContextManifest.stale_reason: f"Source changed: {source_type}:{source_id or project_id}",
                ContextManifest.last_validated_at: now,
            },
            synchronize_session=False,
        )
    if changed:
        db.expire_all()
    return changed


@event.listens_for(Session, "before_flush")
def _collect_context_manifest_source_changes(session: Session, flush_context: Any, instances: Any) -> None:
    """Collect modified source objects without issuing writes during flush."""
    if session.info.get(_MANIFEST_INVALIDATION_GUARD):
        return
    pending = session.info.setdefault(_MANIFEST_INVALIDATION_KEY, set())
    for instance in set(session.dirty).union(session.deleted):
        if instance not in session.deleted and not session.is_modified(instance, include_collections=False):
            continue
        pending.update(_changed_manifest_sources(session, instance))


@event.listens_for(Session, "after_flush_postexec")
def _invalidate_changed_context_manifests(session: Session, flush_context: Any) -> None:
    """Apply eager invalidation after the source update has been persisted."""
    pending = session.info.pop(_MANIFEST_INVALIDATION_KEY, set())
    if not pending or session.info.get(_MANIFEST_INVALIDATION_GUARD):
        return
    session.info[_MANIFEST_INVALIDATION_GUARD] = True
    try:
        invalidate_context_manifests(session, pending)
    finally:
        session.info.pop(_MANIFEST_INVALIDATION_GUARD, None)


class ContextOrchestrator:
    """Create, render, validate and explain a task context manifest."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Model profiles and global budgets
    # ------------------------------------------------------------------
    def resolve_model_profile(self, model: str | None) -> ResolvedModelContextProfile:
        raw = str(model or "").strip()
        provider = "unknown"
        model_name = raw or "unknown"
        if ":" in raw:
            provider, model_name = raw.split(":", 1)
        elif raw:
            # Keep this resolution database-free. The gateway may have no
            # configured default while a caller is only previewing context.
            try:
                from ..ai.gateway import LLMGateway

                provider, model_name = LLMGateway.model_identity(raw)
            except Exception:
                provider = "unknown"
        profile = (
            self.db.query(ModelContextProfile)
            .filter(
                ModelContextProfile.provider == provider,
                ModelContextProfile.model_name == model_name,
                ModelContextProfile.enabled == True,  # noqa: E712
            )
            .first()
        )
        if profile:
            return ResolvedModelContextProfile(
                provider=provider,
                model_name=model_name,
                context_window_tokens=max(1, int(profile.context_window_tokens or DEFAULT_CONTEXT_WINDOW_TOKENS)),
                max_output_tokens=int(profile.max_output_tokens) if profile.max_output_tokens else None,
                safety_margin_tokens=max(0, int(profile.safety_margin_tokens or DEFAULT_SAFETY_MARGIN_TOKENS)),
                known=True,
            )
        return ResolvedModelContextProfile(
            provider=provider,
            model_name=model_name,
            context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
            max_output_tokens=None,
            safety_margin_tokens=DEFAULT_SAFETY_MARGIN_TOKENS,
            known=False,
        )

    @staticmethod
    def budget_for(contract: TaskContextContract, profile: ResolvedModelContextProfile) -> ContextBudget:
        ratio_limit = int(profile.context_window_tokens * contract.output_ratio)
        configured_limit = profile.max_output_tokens or ratio_limit
        output_reserve = max(2048, min(configured_limit, ratio_limit))
        input_budget = max(
            0,
            profile.context_window_tokens - output_reserve - profile.safety_margin_tokens,
        )
        return ContextBudget.from_token_window(
            context_window_tokens=profile.context_window_tokens,
            output_reserve_tokens=output_reserve,
            safety_margin_tokens=profile.safety_margin_tokens,
            task_type=contract.task_type,
        )

    # ------------------------------------------------------------------
    # Manifest preparation
    # ------------------------------------------------------------------
    def prepare(
        self,
        *,
        project_id: str | None,
        task_type: str,
        model: str | None = None,
        execution_route: str = "internal_api",
        arguments: dict[str, Any] | None = None,
        session_id: str | None = None,
        pinned_chunk_ids: Sequence[str] | None = None,
        pinned_source_ids: Sequence[str] | None = None,
    ) -> ContextManifest:
        arguments = dict(arguments or {})
        key = _task_key(task_type)
        contract = TASK_CONTEXT_CONTRACTS[key]
        profile = self.resolve_model_profile(model)
        budget = self.budget_for(contract, profile)
        blocked_reason = self.project_rebuild_block_reason(project_id)

        manifest = ContextManifest(
            project_id=project_id,
            session_id=session_id or str(arguments.get("session_id") or "") or None,
            task_type=key,
            model=profile.model_name if profile.model_name != "unknown" else (model or None),
            provider=profile.provider if profile.provider != "unknown" else None,
            execution_route=(execution_route or "internal_api")[:50],
            policy_version=CONTEXT_POLICY_VERSION,
            status="blocked_rebuild" if blocked_reason else "ready",
            context_window_tokens=profile.context_window_tokens,
            input_budget_tokens=budget.hard_input_budget_tokens,
            output_reserve_tokens=budget.output_reserve_tokens,
            safety_margin_tokens=budget.safety_margin_tokens,
            contract_json={
                "task_type": contract.task_type,
                "required_categories": list(contract.required_categories),
                "optional_categories": list(contract.optional_categories),
                "model_profile_known": profile.known,
                "model_profile": {
                    "provider": profile.provider,
                    "model": profile.model_name,
                    "context_window_tokens": profile.context_window_tokens,
                    "max_output_tokens": profile.max_output_tokens,
                },
            },
            query_json={
                "arguments": self._safe_query_arguments(arguments),
                "pinned_chunk_ids": [str(value) for value in (pinned_chunk_ids or []) if str(value)],
                "pinned_source_ids": [str(value) for value in (pinned_source_ids or []) if str(value)],
            },
            warnings_json=[blocked_reason] if blocked_reason else [],
        )
        self.db.add(manifest)
        self.db.flush()

        if blocked_reason:
            manifest.coverage_json = self._blocked_coverage(contract, blocked_reason)
            manifest.rendered_context = ""
            self.db.flush()
            return manifest

        candidates, coverage = self._collect_candidates(
            project_id=project_id,
            contract=contract,
            arguments=arguments,
            pinned_chunk_ids=pinned_chunk_ids or (),
            pinned_source_ids=pinned_source_ids or (),
        )
        self._persist_budgeted_candidates(manifest, candidates, coverage, budget)
        self.db.flush()
        return manifest

    @staticmethod
    def _safe_query_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        # Persist enough to replay selection without duplicating a chapter body
        # into the audit JSON. The original task source itself is represented by
        # a manifest item and hash.
        safe: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in {"content", "text", "chapter_text"} and isinstance(value, str):
                safe[key] = {"chars": len(value), "sha256": _sha256(value)}
            elif isinstance(value, str):
                safe[key] = _clean_text(value, 1200)
            elif isinstance(value, list):
                safe[key] = value[:30]
            elif isinstance(value, dict):
                safe[key] = value
            elif value is not None:
                safe[key] = value
        return safe

    @staticmethod
    def _blocked_coverage(contract: TaskContextContract, reason: str) -> dict[str, Any]:
        result = {
            category: {"required": True, "status": "blocked", "item_count": 0, "reason": reason}
            for category in contract.required_categories
        }
        result["rebuild"] = {"required": True, "status": "blocked", "item_count": 0, "reason": reason}
        return result

    def _collect_candidates(
        self,
        *,
        project_id: str | None,
        contract: TaskContextContract,
        arguments: dict[str, Any],
        pinned_chunk_ids: Sequence[str],
        pinned_source_ids: Sequence[str],
    ) -> tuple[list[ManifestCandidate], dict[str, Any]]:
        candidates: list[ManifestCandidate] = []
        coverage: dict[str, Any] = {}

        def add(candidate: ManifestCandidate) -> None:
            candidate.source_hash = candidate.source_hash or _current_source_hash(
                self.db,
                project_id,
                candidate.source_type,
                candidate.source_id,
                candidate.content,
            )
            candidates.append(candidate)

        if contract.task_type == "new_project":
            self._collect_new_project_candidates(add, coverage, arguments)
            return candidates, coverage

        project = self.db.query(Project).filter(Project.id == project_id).first() if project_id else None
        if not project:
            for category in contract.required_categories:
                coverage[category] = {
                    "required": True,
                    "status": "missing",
                    "item_count": 0,
                    "reason": "Project is required for this task.",
                }
            return candidates, coverage

        required = set(contract.required_categories)
        # 1. Structural anchors.
        if "style" in required or "style" in contract.optional_categories:
            add(ManifestCandidate(
                category="style",
                source_type="project_style",
                source_id=project.id,
                title="Project style and fixed constraints",
                content=_project_style_text(project),
                required="style" in required,
                tier=1,
                structural_score=1.0,
                final_score=1.0,
                selection_reason="Required project-level style and author constraints.",
            ))
            coverage["style"] = {"required": "style" in required, "status": "covered", "item_count": 1}

        self._collect_target_candidates(add, coverage, project_id, contract, arguments)
        self._collect_author_requirement(add, coverage, arguments)
        self._collect_current_state_candidates(add, coverage, project_id, contract, arguments)
        self._collect_pinned_candidates(add, coverage, project_id, pinned_chunk_ids, pinned_source_ids)

        # 2. Hybrid retrieval comes after hard anchors and pinned choices.
        query = self._retrieval_query(arguments, candidates)
        if query:
            for candidate in self._hybrid_candidates(project_id, query, arguments):
                add(candidate)
            coverage["hybrid_retrieval"] = {
                "required": False,
                "status": "covered" if any(c.category == "hybrid_retrieval" for c in candidates) else "not_applicable",
                "item_count": sum(1 for c in candidates if c.category == "hybrid_retrieval"),
            }

        # 3. Lowest priority memory. Skills are intentionally left to the
        # existing prompt-pack system until they expose stable source hashes.
        self._collect_memory_candidates(add, coverage, project_id, query)
        return candidates, coverage

    def _collect_new_project_candidates(self, add, coverage: dict[str, Any], arguments: dict[str, Any]) -> None:
        session_id = str(arguments.get("session_id") or "").strip()
        answers = arguments.get("answers") or arguments.get("interview_answers") or {}
        session_data = arguments.get("session") or {}
        if session_id or answers or session_data:
            content = json.dumps(
                {"session_id": session_id, "answers": answers, "session": session_data},
                ensure_ascii=False,
                sort_keys=True,
            )
            add(ManifestCandidate(
                category="creation_session",
                source_type="creation_session",
                source_id=session_id or "inline-session",
                title="Novel creation session",
                content=_clean_text(content, 6000),
                required=True,
                tier=1,
                structural_score=1.0,
                final_score=1.0,
                selection_reason="Current interview and confirmed creation-session data.",
            ))
            coverage["creation_session"] = {"required": True, "status": "covered", "item_count": 1}
        else:
            coverage["creation_session"] = {
                "required": True,
                "status": "missing",
                "item_count": 0,
                "reason": "No creation session or interview answers were supplied.",
            }
        stages = arguments.get("confirmed_stages") or arguments.get("stage_data")
        if stages:
            content = _clean_text(json.dumps(stages, ensure_ascii=False, sort_keys=True), 6000)
            add(ManifestCandidate(
                category="confirmed_stage",
                source_type="confirmed_stage",
                source_id="confirmed-stage",
                title="Confirmed creation stages",
                content=content,
                tier=2,
                structural_score=0.9,
                final_score=0.9,
                selection_reason="Author-confirmed stage data only.",
            ))
            coverage["confirmed_stage"] = {"required": False, "status": "covered", "item_count": 1}
        constraints = str(arguments.get("author_constraints") or arguments.get("requirements") or "").strip()
        if constraints:
            add(ManifestCandidate(
                category="author_constraint",
                source_type="author_constraint",
                source_id="author-constraint",
                title="Author constraints",
                content=_clean_text(constraints, 3000),
                tier=2,
                structural_score=0.9,
                final_score=0.9,
                selection_reason="Explicit constraints from the author.",
            ))
            coverage["author_constraint"] = {"required": False, "status": "covered", "item_count": 1}

    def _collect_target_candidates(
        self,
        add,
        coverage: dict[str, Any],
        project_id: str,
        contract: TaskContextContract,
        arguments: dict[str, Any],
    ) -> None:
        target_outline_id = str(arguments.get("outline_node_id") or arguments.get("target_outline_node_id") or "").strip()
        if contract.task_type in {"writing", "planning"}:
            if target_outline_id:
                node = (
                    self.db.query(OutlineNode)
                    .filter(OutlineNode.project_id == project_id, OutlineNode.id == target_outline_id)
                    .first()
                )
                if node:
                    add(ManifestCandidate(
                        category="target_outline",
                        source_type="outline",
                        source_id=node.id,
                        title=node.title or "Target outline",
                        content=_outline_text(node),
                        required=contract.task_type == "writing",
                        tier=1,
                        structural_score=1.0,
                        final_score=1.0,
                        selection_reason="Target outline/section required by the writing contract.",
                    ))
                    coverage["target_outline"] = {"required": contract.task_type == "writing", "status": "covered", "item_count": 1}
                else:
                    coverage["target_outline"] = {
                        "required": contract.task_type == "writing",
                        "status": "missing",
                        "item_count": 0,
                        "reason": "The selected outline node no longer exists.",
                    }
            else:
                coverage["target_outline"] = {
                    "required": contract.task_type == "writing",
                    "status": "missing" if contract.task_type == "writing" else "not_applicable",
                    "item_count": 0,
                    "reason": "Writing needs a target outline or section." if contract.task_type == "writing" else "",
                }

        chapter_id = str(arguments.get("chapter_id") or arguments.get("target_chapter_id") or "").strip()
        direct_text = str(arguments.get("content") or arguments.get("text") or arguments.get("chapter_text") or "").strip()
        if contract.task_type == "cataloging":
            if chapter_id:
                chapter = self._chapter(project_id, chapter_id)
                if chapter:
                    add(ManifestCandidate(
                        category="target_chapter",
                        source_type="chapter",
                        source_id=chapter.id,
                        title=chapter.title or "Target chapter",
                        content=_chapter_text(chapter),
                        required=True,
                        tier=1,
                        structural_score=1.0,
                        final_score=1.0,
                        selection_reason="Source chapter required for cataloging.",
                    ))
                    coverage["target_chapter"] = {"required": True, "status": "covered", "item_count": 1}
                else:
                    coverage["target_chapter"] = {"required": True, "status": "missing", "item_count": 0, "reason": "Target chapter not found."}
            else:
                coverage["target_chapter"] = {"required": True, "status": "missing", "item_count": 0, "reason": "Cataloging needs chapter_id."}
        elif contract.task_type in {"review", "rewrite"}:
            if chapter_id and (chapter := self._chapter(project_id, chapter_id)):
                add(ManifestCandidate(
                    category="target_text",
                    source_type="chapter",
                    source_id=chapter.id,
                    title=chapter.title or "Target text",
                    content=_chapter_text(chapter),
                    required=True,
                    tier=1,
                    structural_score=1.0,
                    final_score=1.0,
                    selection_reason="Target chapter required by the review/rewrite contract.",
                ))
                coverage["target_text"] = {"required": True, "status": "covered", "item_count": 1}
            elif direct_text:
                add(ManifestCandidate(
                    category="target_text",
                    source_type="inline",
                    source_id="inline-target",
                    title=str(arguments.get("title") or "Inline target text"),
                    content=_clean_text(direct_text, 20_000),
                    required=True,
                    tier=1,
                    structural_score=1.0,
                    final_score=1.0,
                    selection_reason="Inline target text supplied by the caller.",
                ))
                coverage["target_text"] = {"required": True, "status": "covered", "item_count": 1}
            else:
                coverage["target_text"] = {"required": True, "status": "missing", "item_count": 0, "reason": "A target chapter or text is required."}

    def _collect_author_requirement(self, add, coverage: dict[str, Any], arguments: dict[str, Any]) -> None:
        text = str(arguments.get("requirements") or arguments.get("instruction") or arguments.get("request") or "").strip()
        if not text:
            coverage["user_requirement"] = {"required": False, "status": "not_applicable", "item_count": 0}
            return
        add(ManifestCandidate(
            category="user_requirement",
            source_type="inline",
            source_id="user-requirement",
            title="Author request",
            content=_clean_text(text, 4000),
            tier=2,
            structural_score=0.95,
            final_score=0.95,
            selection_reason="Explicit request passed to this task.",
        ))
        coverage["user_requirement"] = {"required": False, "status": "covered", "item_count": 1}

    def _collect_current_state_candidates(
        self,
        add,
        coverage: dict[str, Any],
        project_id: str,
        contract: TaskContextContract,
        arguments: dict[str, Any],
    ) -> None:
        chapter_id = str(arguments.get("chapter_id") or arguments.get("target_chapter_id") or "").strip()
        target_outline_id = str(arguments.get("outline_node_id") or arguments.get("target_outline_node_id") or "").strip()

        # Previous / adjacent summaries: not applicable when there is no prior chapter.
        chapters = (
            self.db.query(Chapter)
            .filter(Chapter.project_id == project_id)
            .order_by(Chapter.created_at.desc())
            .all()
        )
        if chapter_id:
            chapters = [chapter for chapter in chapters if chapter.id != chapter_id]
        summaries = [chapter for chapter in chapters if chapter.summary and chapter.summary.summary_text][:3]
        category = "adjacent_summary" if contract.task_type == "cataloging" else "previous_summary"
        for rank, chapter in enumerate(reversed(summaries)):
            content = _clean_text(chapter.summary.summary_text, 1600)
            add(ManifestCandidate(
                category=category,
                source_type="chapter_summary",
                source_id=chapter.id,
                title=f"Previous summary: {chapter.title}",
                content=content,
                tier=3,
                recency_score=max(0.2, 1.0 - rank * 0.15),
                structural_score=0.8,
                final_score=max(0.2, 0.9 - rank * 0.1),
                selection_reason="Most recent confirmed chapter summary.",
            ))
        coverage[category] = {
            "required": False,
            "status": "covered" if summaries else "not_applicable",
            "item_count": len(summaries),
        }

        character_ids = {str(value) for value in (arguments.get("character_ids") or []) if str(value)}
        character_names = {str(value).strip() for value in (arguments.get("involved_characters") or arguments.get("character_names") or []) if str(value).strip()}
        if target_outline_id:
            links = self.db.query(OutlineNodeCharacter).filter(OutlineNodeCharacter.outline_node_id == target_outline_id).all()
            character_ids.update(link.character_id for link in links if link.character_id)
        char_query = self.db.query(Character).filter(
            Character.project_id == project_id,
            or_(Character.role_type.is_(None), Character.role_type != "merged_alias"),
        )
        if character_ids:
            chars = char_query.filter(Character.id.in_(character_ids)).all()
        elif character_names:
            chars = char_query.filter(Character.name.in_(character_names)).all()
        else:
            chars = []
        for character in chars[:12]:
            add(ManifestCandidate(
                category="scene_character",
                source_type="character",
                source_id=character.id,
                title=character.name,
                content=_character_text(character),
                tier=3,
                structural_score=0.95,
                final_score=0.95,
                selection_reason="Character explicitly selected or linked to the target outline.",
            ))
        project_character_count = self.db.query(Character.id).filter(
            Character.project_id == project_id,
            or_(Character.role_type.is_(None), Character.role_type != "merged_alias"),
        ).count()
        coverage["scene_character"] = {
            "required": False,
            "status": "covered" if chars else ("not_applicable" if not project_character_count else "missing"),
            "item_count": len(chars),
            "reason": "No target character was resolved." if project_character_count and not chars else "",
        }

        # Structured governance is concise and always represented, including an
        # empty ledger, so the model can distinguish no known debt from absent data.
        try:
            from .narrative_governance import governance_context

            governance = governance_context(self.db, project_id, chapter_id=chapter_id or None, limit=12)
        except Exception:
            governance = ""
        if not governance:
            governance = "Narrative governance: no due or high-risk items."
        add(ManifestCandidate(
            category="narrative_governance",
            source_type="narrative_governance",
            source_id=chapter_id or project_id,
            title="Narrative governance ledger",
            content=_clean_text(governance, 5000),
            tier=3,
            structural_score=0.85,
            final_score=0.85,
            selection_reason="Current debts, foreshadowing, causal chains and state conflicts.",
        ))
        coverage["narrative_governance"] = {"required": False, "status": "covered", "item_count": 1}

        if contract.task_type == "cataloging":
            facts = str(arguments.get("confirmed_facts") or arguments.get("facts") or "").strip()
            if facts:
                add(ManifestCandidate(
                    category="confirmed_fact",
                    source_type="inline",
                    source_id="confirmed-facts",
                    title="Confirmed facts",
                    content=_clean_text(facts, 6000),
                    tier=2,
                    structural_score=0.9,
                    final_score=0.9,
                    selection_reason="Confirmed facts supplied by the cataloging workflow.",
                ))
                coverage["confirmed_fact"] = {"required": False, "status": "covered", "item_count": 1}
            else:
                coverage["confirmed_fact"] = {"required": False, "status": "not_applicable", "item_count": 0}

    def _collect_pinned_candidates(
        self,
        add,
        coverage: dict[str, Any],
        project_id: str,
        pinned_chunk_ids: Sequence[str],
        pinned_source_ids: Sequence[str],
    ) -> None:
        found = 0
        for chunk_id in dict.fromkeys(str(value).strip() for value in pinned_chunk_ids if str(value).strip()):
            chunk = self.db.query(RagChunk).filter(RagChunk.project_id == project_id, RagChunk.id == chunk_id).first()
            if not chunk:
                continue
            found += 1
            add(ManifestCandidate(
                category="pinned",
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                chunk_id=chunk.id,
                title=chunk.title or chunk.source_type,
                content=_clean_text(chunk.content, 6000),
                required=True,
                pinned=True,
                tier=2,
                structural_score=1.0,
                final_score=1.0,
                selection_reason="Author-pinned source; it cannot be silently removed by the budget.",
            ))
        for source_id in dict.fromkeys(str(value).strip() for value in pinned_source_ids if str(value).strip()):
            chunks = (
                self.db.query(RagChunk)
                .filter(RagChunk.project_id == project_id, RagChunk.source_id == source_id)
                .order_by(RagChunk.chunk_index.asc())
                .limit(3)
                .all()
            )
            for chunk in chunks:
                found += 1
                add(ManifestCandidate(
                    category="pinned",
                    source_type=chunk.source_type,
                    source_id=chunk.source_id,
                    chunk_id=chunk.id,
                    title=chunk.title or chunk.source_type,
                    content=_clean_text(chunk.content, 3000),
                    required=True,
                    pinned=True,
                    tier=2,
                    structural_score=1.0,
                    final_score=1.0,
                    selection_reason="Author-pinned source; it cannot be silently removed by the budget.",
                ))
        if pinned_chunk_ids or pinned_source_ids:
            coverage["pinned"] = {
                "required": True,
                "status": "covered" if found else "missing",
                "item_count": found,
                "reason": "Pinned sources could not be resolved." if not found else "",
            }

    @staticmethod
    def _retrieval_query(arguments: dict[str, Any], candidates: Iterable[ManifestCandidate]) -> str:
        values = [
            str(arguments.get("requirements") or ""),
            str(arguments.get("instruction") or ""),
            str(arguments.get("query") or ""),
        ]
        for candidate in candidates:
            if candidate.category in {"target_outline", "target_chapter", "target_text", "user_requirement"}:
                values.append(candidate.title)
                values.append(_clean_text(candidate.content, 1200))
        return "\n".join(value for value in values if value.strip())[:12_000]

    def _hybrid_candidates(self, project_id: str, query: str, arguments: dict[str, Any]) -> list[ManifestCandidate]:
        try:
            if not self.db.query(RagChunk.id).filter(RagChunk.project_id == project_id).first():
                reindex_project(self.db, project_id)
        except Exception:
            pass
        lexical_results = search_chunks(self.db, project_id, query, limit=48)
        lexical_values = [float(result.score or 0) for result in lexical_results]
        lexical_by_chunk = {
            result.chunk_id: _normalise_score(float(result.score or 0), lexical_values)
            for result in lexical_results
        }
        semantic_by_chunk = self._semantic_scores(project_id, query)
        use_semantic = bool(semantic_by_chunk)
        chunk_ids = set(lexical_by_chunk) | set(semantic_by_chunk)
        if not chunk_ids:
            return []
        chunks = (
            self.db.query(RagChunk)
            .filter(RagChunk.project_id == project_id, RagChunk.id.in_(chunk_ids))
            .all()
        )
        target_sources = {
            str(arguments.get("outline_node_id") or ""),
            str(arguments.get("chapter_id") or ""),
            str(arguments.get("target_chapter_id") or ""),
        }
        ranked: list[ManifestCandidate] = []
        for chunk in chunks:
            lexical = lexical_by_chunk.get(chunk.id, 0.0)
            semantic = semantic_by_chunk.get(chunk.id)
            recency = _source_recency(self.db, chunk.source_type, chunk.source_id)
            structural = 1.0 if chunk.source_id in target_sources else 0.25
            if use_semantic:
                final = lexical * 0.45 + (semantic or 0.0) * 0.35 + recency * 0.15 + structural * 0.05
                reason = "Hybrid ranking: lexical 45%, semantic 35%, recency 15%, structure 5%."
            else:
                final = lexical * 0.70 + recency * 0.20 + structural * 0.10
                reason = "Lexical fallback ranking: lexical 70%, recency 20%, structure 10%."
            ranked.append(ManifestCandidate(
                category="hybrid_retrieval",
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                chunk_id=chunk.id,
                title=chunk.title or chunk.source_type,
                content=_clean_text(chunk.content, 1800),
                tier=4,
                lexical_score=lexical,
                semantic_score=semantic,
                recency_score=recency,
                structural_score=structural,
                final_score=final,
                selection_reason=reason,
                source_hash=_current_source_hash(
                    self.db,
                    project_id,
                    chunk.source_type,
                    chunk.source_id,
                    chunk.content,
                ),
            ))
        return sorted(ranked, key=lambda item: item.final_score, reverse=True)[:24]

    def _collect_memory_candidates(self, add, coverage: dict[str, Any], project_id: str, query: str) -> None:
        if not query:
            coverage["memory"] = {"required": False, "status": "not_applicable", "item_count": 0}
            return
        # Memory retrieval remains conservative: high-importance memories only,
        # then lexical overlap with the task query.
        query_lower = query.lower()
        rows = (
            self.db.query(AssistantMemory)
            .filter(AssistantMemory.project_id == project_id, AssistantMemory.importance >= 7)
            .order_by(AssistantMemory.importance.desc(), AssistantMemory.updated_at.desc())
            .limit(20)
            .all()
        )
        selected = []
        for row in rows:
            haystack = f"{row.key} {row.value}".lower()
            if row.key.lower() in query_lower or any(token and token in haystack for token in query_lower.split()[:12]):
                selected.append(row)
        for row in selected[:6]:
            add(ManifestCandidate(
                category="memory",
                source_type="assistant_memory",
                source_id=row.id,
                title=row.key,
                content=_clean_text(row.value, 900),
                tier=5,
                recency_score=_source_recency(self.db, "assistant_memory", row.id),
                final_score=min(1.0, float(row.importance or 0) / 10),
                selection_reason="High-importance memory matched the task query.",
            ))
        coverage["memory"] = {"required": False, "status": "covered" if selected else "not_applicable", "item_count": min(len(selected), 6)}

    def _semantic_scores(self, project_id: str, query: str) -> dict[str, float]:
        status = LocalSemanticRuntime.status()
        if not status.get("available"):
            return {}
        rows = (
            self.db.query(RagChunkEmbedding)
            .filter(
                RagChunkEmbedding.project_id == project_id,
                RagChunkEmbedding.embedding_model == LOCAL_EMBEDDING_MODEL,
                RagChunkEmbedding.index_version == CONTEXT_INDEX_VERSION,
            )
            .all()
        )
        if not rows:
            return {}
        try:
            query_vector = LocalSemanticRuntime.embed([f"query: {query}"])[0]
        except Exception:
            return {}
        raw: dict[str, float] = {}
        for row in rows:
            vector = _unpack_float32(row.vector_blob, row.vector_dim)
            raw[row.chunk_id] = _cosine(query_vector, vector)
        values = list(raw.values())
        normalised = {chunk_id: _normalise_score(score, values) for chunk_id, score in raw.items()}
        return dict(sorted(normalised.items(), key=lambda item: item[1], reverse=True)[:48])

    def _persist_budgeted_candidates(
        self,
        manifest: ContextManifest,
        candidates: list[ManifestCandidate],
        coverage: dict[str, Any],
        budget: ContextBudget,
    ) -> None:
        warnings = list(manifest.warnings_json or [])
        selected: list[ManifestCandidate] = []
        used_tokens = 0
        seen_identity: set[tuple[str, str | None, str | None]] = set()

        # Exact tier order implements the governance policy. Within an optional
        # tier final score determines fit order.
        ordered = sorted(
            candidates,
            key=lambda item: (item.tier, 0 if item.required else 1, -item.final_score, item.title),
        )
        for candidate in ordered:
            identity = (candidate.source_type, candidate.source_id, candidate.chunk_id)
            if identity in seen_identity and not candidate.pinned:
                continue
            tokens = candidate.estimated_tokens
            remaining = budget.hard_input_budget_tokens - used_tokens
            if tokens <= remaining:
                selected.append(candidate)
                used_tokens += tokens
                seen_identity.add(identity)
                continue
            if candidate.required:
                category = coverage.setdefault(candidate.category, {"required": True, "status": "missing", "item_count": 0})
                category.update({
                    "required": True,
                    "status": "missing",
                    "reason": "Required anchor exceeds the remaining context budget. Narrow scope, pin a smaller source, or override with a reason.",
                })
                warnings.append(f"Required context '{candidate.title}' did not fit the input budget.")
            # Optional material is intentionally skipped after recording a
            # warning only when it had a meaningful retrieval score.
            elif candidate.category == "hybrid_retrieval" and candidate.final_score >= 0.6:
                warnings.append(f"Relevant retrieved source '{candidate.title}' was omitted by the budget.")

        # Count selected items and convert previously covered candidates that
        # did not fit into an honest missing/partial coverage report.
        selected_counts: dict[str, int] = {}
        for candidate in selected:
            selected_counts[candidate.category] = selected_counts.get(candidate.category, 0) + 1
        for category, entry in coverage.items():
            if entry.get("status") == "covered":
                actual = selected_counts.get(category, 0)
                entry["item_count"] = actual
                if actual == 0:
                    entry["status"] = "missing" if entry.get("required") else "not_selected"

        for category in manifest.contract_json.get("required_categories", []):
            entry = coverage.setdefault(category, {"required": True, "status": "missing", "item_count": 0})
            if entry.get("status") not in {"covered", "not_applicable"}:
                entry["required"] = True

        missing = [
            category
            for category, entry in coverage.items()
            if entry.get("required") and entry.get("status") not in {"covered", "not_applicable"}
        ]
        manifest.status = "needs_confirmation" if missing else "ready"
        if missing:
            warnings.append("Required context is missing: " + ", ".join(missing))
        if not manifest.contract_json.get("model_profile_known"):
            warnings.append("Unknown model context profile: conservative 16K window was used.")

        for order, candidate in enumerate(selected):
            item = ContextManifestItem(
                manifest_id=manifest.id,
                project_id=manifest.project_id,
                category=candidate.category,
                source_type=candidate.source_type,
                source_id=candidate.source_id,
                chunk_id=candidate.chunk_id,
                source_hash=candidate.source_hash,
                title=_clean_text(candidate.title, 300),
                content_excerpt=candidate.content,
                required=candidate.required,
                pinned=candidate.pinned,
                tier=candidate.tier,
                lexical_score=candidate.lexical_score,
                semantic_score=candidate.semantic_score,
                recency_score=candidate.recency_score,
                structural_score=candidate.structural_score,
                final_score=candidate.final_score,
                selection_reason=candidate.selection_reason,
                estimated_tokens=candidate.estimated_tokens,
                sort_order=order,
            )
            self.db.add(item)

        manifest.coverage_json = coverage
        manifest.warnings_json = list(dict.fromkeys(warnings))[:100]
        manifest.estimated_input_tokens = used_tokens
        manifest.estimated_input_chars = sum(len(candidate.content) for candidate in selected)
        manifest.rendered_context = self.render_candidates(selected)

    @staticmethod
    def render_candidates(candidates: Iterable[ManifestCandidate]) -> str:
        groups: dict[str, list[ManifestCandidate]] = {}
        for candidate in candidates:
            groups.setdefault(candidate.category, []).append(candidate)
        parts = ["# Governed Task Context"]
        for category, values in groups.items():
            parts.append(f"\n## {category}")
            for value in values:
                parts.append(f"### {value.title}\n{value.content}")
        return "\n\n".join(parts).strip()

    def _chapter(self, project_id: str, chapter_id: str) -> Chapter | None:
        return self.db.query(Chapter).filter(Chapter.project_id == project_id, Chapter.id == chapter_id).first()

    # ------------------------------------------------------------------
    # Read, explain, override and validation
    # ------------------------------------------------------------------
    def get_manifest(self, manifest_id: str, project_id: str | None = None) -> ContextManifest | None:
        query = self.db.query(ContextManifest).filter(ContextManifest.id == manifest_id)
        if project_id is not None:
            query = query.filter(ContextManifest.project_id == project_id)
        return query.first()

    def manifest_payload(self, manifest: ContextManifest, *, include_content: bool = True) -> dict[str, Any]:
        input_budget_tokens = int(manifest.input_budget_tokens or 0)
        estimated_input_tokens = int(manifest.estimated_input_tokens or 0)
        estimated_input_chars = int(manifest.estimated_input_chars or 0)
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
                "input_budget_tokens": input_budget_tokens,
                "output_reserve_tokens": int(manifest.output_reserve_tokens or 0),
                "safety_margin_tokens": int(manifest.safety_margin_tokens or 0),
                "estimated_input_tokens": estimated_input_tokens,
                "estimated_input_chars": estimated_input_chars,
                "remaining_input_tokens": max(0, input_budget_tokens - estimated_input_tokens),
            },
            "coverage": manifest.coverage_json or {},
            "warnings": manifest.warnings_json or [],
            "contract": manifest.contract_json or {},
            "override": {
                "reason": manifest.override_reason,
                "actor": manifest.override_actor,
                "at": _iso(manifest.overridden_at),
            },
            "stale_reason": manifest.stale_reason,
            "items": [_manifest_item_payload(item, include_content=include_content) for item in manifest.items],
            "rendered_context": manifest.rendered_context if include_content else "",
            "created_at": _iso(manifest.created_at),
            "updated_at": _iso(manifest.updated_at),
            "last_validated_at": _iso(manifest.last_validated_at),
        }

    def explain(self, manifest: ContextManifest) -> dict[str, Any]:
        payload = self.manifest_payload(manifest, include_content=False)
        payload["explanation"] = {
            "selection_order": [
                "required structural anchors",
                "author-pinned sources",
                "current state and relationships",
                "hybrid retrieval",
                "memory and skills",
            ],
            "semantic_status": LocalSemanticRuntime.status(),
            "weights": {
                "hybrid": {"lexical": 0.45, "semantic": 0.35, "recency": 0.15, "structural": 0.05},
                "lexical_fallback": {"lexical": 0.70, "recency": 0.20, "structural": 0.10},
            },
        }
        return payload

    def override(self, manifest: ContextManifest, *, reason: str, actor: str = "author") -> ContextManifest:
        reason = reason.strip()
        if not reason:
            raise ValueError("An override reason is required.")
        if manifest.status == "blocked_rebuild":
            raise ValueError("A rebuild-blocked project cannot be overridden.")
        if manifest.status == "stale":
            raise ValueError("A stale manifest must be prepared again; it cannot be overridden.")
        manifest.status = "overridden"
        manifest.override_reason = reason[:4000]
        manifest.override_actor = (actor or "author")[:100]
        manifest.overridden_at = datetime.utcnow()
        manifest.last_validated_at = datetime.utcnow()
        self.db.flush()
        return manifest

    def validate(self, manifest: ContextManifest, *, require_external_evidence: bool = False) -> tuple[bool, str]:
        if manifest.status == "blocked_rebuild":
            return False, "Context rebuild is still in progress for this project."
        if manifest.status == "needs_confirmation":
            return False, "Required context is missing; confirm an override or narrow the task."
        if manifest.status == "stale":
            return False, manifest.stale_reason or "The context sources have changed."
        if manifest.status not in {"ready", "overridden"}:
            return False, f"Manifest is not usable: {manifest.status}."

        for item in manifest.items:
            expected = item.source_hash
            current = _current_source_hash(
                self.db,
                manifest.project_id,
                item.source_type,
                item.source_id,
                item.content_excerpt,
            )
            if expected and current and expected != current:
                manifest.status = "stale"
                manifest.stale_reason = f"Source changed: {item.title}"
                manifest.last_validated_at = datetime.utcnow()
                self.db.flush()
                return False, manifest.stale_reason
            if expected and current is None:
                manifest.status = "stale"
                manifest.stale_reason = f"Source is unavailable: {item.title}"
                manifest.last_validated_at = datetime.utcnow()
                self.db.flush()
                return False, manifest.stale_reason

        if require_external_evidence:
            required_items = [item for item in manifest.items if item.required]
            missing_evidence = [item.title for item in required_items if item.evidence_submitted_at is None]
            if missing_evidence:
                return (
                    False,
                    "External Agent must submit verified evidence for every required context anchor: "
                    + ", ".join(missing_evidence[:6]),
                )
        manifest.last_validated_at = datetime.utcnow()
        self.db.flush()
        return True, ""

    def mark_consumed(self, manifest: ContextManifest) -> None:
        manifest.consumed_at = datetime.utcnow()
        self.db.flush()

    def submit_evidence(self, manifest: ContextManifest, sources: Sequence[dict[str, Any]]) -> dict[str, Any]:
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        by_chunk = {item.chunk_id: item for item in manifest.items if item.chunk_id}
        by_source: dict[tuple[str, str], list[ContextManifestItem]] = {}
        for item in manifest.items:
            if item.source_id:
                by_source.setdefault((item.source_type, item.source_id), []).append(item)
        for source in sources[:50]:
            if not isinstance(source, dict):
                rejected.append({"source": source, "reason": "Evidence must be an object."})
                continue
            chunk_id = str(source.get("chunk_id") or "").strip()
            source_type = str(source.get("source_type") or "").strip()
            source_id = str(source.get("source_id") or "").strip()
            source_hash = str(source.get("source_hash") or "").strip()
            item = by_chunk.get(chunk_id) if chunk_id else None
            if item is None and source_type and source_id:
                options = by_source.get((source_type, source_id), [])
                item = next((candidate for candidate in options if not source_hash or candidate.source_hash == source_hash), None)
            if item is None:
                rejected.append({"chunk_id": chunk_id or None, "source_id": source_id or None, "reason": "Source is not in the baseline manifest or verified task search result."})
                continue
            if source_hash and item.source_hash and source_hash != item.source_hash:
                rejected.append({"chunk_id": item.chunk_id, "source_id": item.source_id, "reason": "Source hash does not match the manifest."})
                continue
            current = _current_source_hash(self.db, manifest.project_id, item.source_type, item.source_id, item.content_excerpt)
            if item.source_hash and current and current != item.source_hash:
                manifest.status = "stale"
                manifest.stale_reason = f"Source changed: {item.title}"
                rejected.append({"chunk_id": item.chunk_id, "source_id": item.source_id, "reason": "Source is stale."})
                continue
            item.evidence_submitted_at = datetime.utcnow()
            accepted.append({"chunk_id": item.chunk_id, "source_type": item.source_type, "source_id": item.source_id, "source_hash": item.source_hash})
        self.db.flush()
        return {"accepted": accepted, "rejected": rejected, "accepted_count": len(accepted)}

    def search_task_context(self, manifest: ContextManifest, *, query: str, limit: int = 12) -> list[dict[str, Any]]:
        if not manifest.project_id:
            return []
        results = self._hybrid_candidates(manifest.project_id, query, {})[: max(1, min(limit, 40))]
        # Append verified search results to the manifest. They retain tier 4 and
        # do not mutate the already-rendered baseline, but they become valid
        # evidence targets for an external Agent.
        existing = {item.chunk_id for item in manifest.items if item.chunk_id}
        next_order = max((item.sort_order for item in manifest.items), default=-1) + 1
        payload: list[dict[str, Any]] = []
        for candidate in results:
            if candidate.chunk_id in existing:
                item = next(item for item in manifest.items if item.chunk_id == candidate.chunk_id)
            else:
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
                    selection_reason="Verified external Agent task-context search result. " + candidate.selection_reason,
                    estimated_tokens=candidate.estimated_tokens,
                    sort_order=next_order,
                )
                next_order += 1
                self.db.add(item)
                self.db.flush()
            payload.append(_manifest_item_payload(item, include_content=True))
        return payload

    # ------------------------------------------------------------------
    # Semantic index and full rebuild jobs
    # ------------------------------------------------------------------
    def semantic_status(self) -> dict[str, Any]:
        status = LocalSemanticRuntime.status()
        status["index_version"] = CONTEXT_INDEX_VERSION
        return status

    def build_semantic_embeddings(self, project_id: str) -> dict[str, Any]:
        status = self.semantic_status()
        if not status["available"]:
            return {"available": False, "indexed": 0, "reason": status["reason"]}
        chunks = self.db.query(RagChunk).filter(RagChunk.project_id == project_id).all()
        if not chunks:
            return {"available": True, "indexed": 0, "reason": "No RAG chunks."}
        current_by_chunk = {
            row.chunk_id: row
            for row in self.db.query(RagChunkEmbedding)
            .filter(
                RagChunkEmbedding.project_id == project_id,
                RagChunkEmbedding.embedding_model == LOCAL_EMBEDDING_MODEL,
                RagChunkEmbedding.index_version == CONTEXT_INDEX_VERSION,
            )
            .all()
        }
        pending = [chunk for chunk in chunks if current_by_chunk.get(chunk.id) is None or current_by_chunk[chunk.id].source_hash != _sha256(chunk.content)]
        if not pending:
            self._write_hnsw_sidecar(project_id)
            return {"available": True, "indexed": 0, "reason": "Already current."}
        vectors = LocalSemanticRuntime.embed([f"passage: {chunk.content}" for chunk in pending])
        for chunk, vector in zip(pending, vectors):
            row = current_by_chunk.get(chunk.id)
            if row is None:
                row = RagChunkEmbedding(
                    chunk_id=chunk.id,
                    project_id=project_id,
                    embedding_model=LOCAL_EMBEDDING_MODEL,
                    index_version=CONTEXT_INDEX_VERSION,
                    vector_dim=len(vector),
                    vector_blob=_pack_float32(vector),
                    source_hash=_sha256(chunk.content),
                )
                self.db.add(row)
            else:
                row.vector_dim = len(vector)
                row.vector_blob = _pack_float32(vector)
                row.source_hash = _sha256(chunk.content)
                row.updated_at = datetime.utcnow()
        self.db.flush()
        self._write_hnsw_sidecar(project_id)
        return {"available": True, "indexed": len(pending), "reason": ""}

    def _write_hnsw_sidecar(self, project_id: str) -> None:
        """Best-effort HNSW sidecar; SQLite remains the source of truth."""
        try:
            import hnswlib  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return
        rows = (
            self.db.query(RagChunkEmbedding)
            .filter(
                RagChunkEmbedding.project_id == project_id,
                RagChunkEmbedding.embedding_model == LOCAL_EMBEDDING_MODEL,
                RagChunkEmbedding.index_version == CONTEXT_INDEX_VERSION,
            )
            .all()
        )
        if not rows:
            return
        vectors = [_unpack_float32(row.vector_blob, row.vector_dim) for row in rows]
        if not vectors or not vectors[0]:
            return
        project = self.db.query(Project).filter(Project.id == project_id).first()
        root = Path(project.folder_path) if project and project.folder_path else Path(os.environ.get("SIMING_HOME") or os.environ.get("MOSHU_HOME") or ".") / "siming-projects" / project_id
        directory = root / ".siming" / "indexes"
        directory.mkdir(parents=True, exist_ok=True)
        index = hnswlib.Index(space="cosine", dim=len(vectors[0]))
        index.init_index(max_elements=len(vectors), ef_construction=100, M=16)
        index.add_items(np.asarray(vectors, dtype=np.float32), np.arange(len(vectors)))
        index.set_ef(min(64, len(vectors)))
        index.save_index(str(directory / f"semantic-{CONTEXT_INDEX_VERSION}.hnsw"))
        (directory / f"semantic-{CONTEXT_INDEX_VERSION}.json").write_text(
            json.dumps({"model": LOCAL_EMBEDDING_MODEL, "chunk_ids": [row.chunk_id for row in rows]}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _sync_rebuild_operation(self, job: ContextRebuildJob, *, message: str | None = None) -> None:
        from .operation_runtime import ensure_operation, update_operation

        lifecycle = {
            "queued": "queued",
            "running": "running",
            "completed": "completed",
            "failed": "failed",
        }.get(job.status, "running")
        processed = int(job.completed_projects or 0) + int(job.failed_projects or 0)
        result = None
        outcome = None
        if lifecycle == "completed":
            failed = int(job.failed_projects or 0)
            result = {
                "summary": (
                    f"上下文索引重建完成：{job.completed_projects or 0} 个成功，{failed} 个失败"
                    if failed
                    else f"上下文索引重建完成：{job.completed_projects or 0} 个作品"
                ),
                "completed": [f"{job.completed_projects or 0} 个作品已完成"],
                "incomplete": [f"{failed} 个作品失败"] if failed else [],
            }
            outcome = "partial_success" if failed else "completed_with_tools"
        elif lifecycle == "failed":
            result = {
                "summary": job.error or "上下文索引重建失败",
                "completed": [f"{job.completed_projects or 0} 个作品已完成"] if job.completed_projects else [],
                "incomplete": [job.error or "重建任务未完成"],
            }
            outcome = "partial_success" if job.completed_projects else "failed"
        operation = ensure_operation(
            self.db,
            source_kind="context_rebuild",
            source_id=job.id,
            title=f"上下文索引重建 · {job.total_projects or 0} 个作品",
            status=lifecycle,
            phase=job.status,
            message=message or "正在重建作品上下文索引",
            tool_mode="context_indexer",
            resume_url="/dashboard",
            can_pause=False,
            can_cancel=False,
            can_retry=False,
            progress_mode="determinate" if job.total_projects else "indeterminate",
            progress_current=processed,
            progress_total=int(job.total_projects) if job.total_projects else None,
            result=result,
            outcome=outcome,
        )
        job.operation_id = operation.id
        update_operation(
            self.db,
            operation,
            status=lifecycle,
            health_status="active",
            phase=job.status,
            message=message or operation.current_message,
            progress_mode="determinate" if job.total_projects else "indeterminate",
            progress_current=processed,
            progress_total=int(job.total_projects) if job.total_projects else None,
            failure_class="context_rebuild_error" if lifecycle == "failed" else None,
            next_action="返回上下文治理页面重试失败作品" if lifecycle == "failed" else None,
            attention={},
            result=result,
            outcome=outcome,
        )
        self.db.flush()

    def create_rebuild_job(self, *, requested_by: str = "system", project_ids: Sequence[str] | None = None) -> ContextRebuildJob:
        active = (
            self.db.query(ContextRebuildJob)
            .filter(
                ContextRebuildJob.policy_version == CONTEXT_POLICY_VERSION,
                ContextRebuildJob.status.in_(["queued", "running"]),
            )
            .order_by(ContextRebuildJob.created_at.desc())
            .first()
        )
        if active:
            self._sync_rebuild_operation(active, message="上下文索引重建仍在运行")
            return active

        requested_ids = [str(value) for value in (project_ids or []) if str(value)]
        query = self.db.query(Project.id)
        if project_ids is not None:
            query = query.filter(Project.id.in_(requested_ids))
        candidate_ids = [row[0] for row in query.all()]

        if project_ids is None:
            # Automatic startup recovery only rebuilds projects that have not
            # reached the current policy/index version. Re-running every
            # completed project on every application start would repeatedly
            # lock normal writing behind a needless maintenance state.
            rows = (
                self.db.query(ContextRebuildProject)
                .join(ContextRebuildJob)
                .filter(ContextRebuildJob.policy_version == CONTEXT_POLICY_VERSION)
                .order_by(ContextRebuildProject.project_id, ContextRebuildProject.created_at.desc())
                .all()
            )
            latest_by_project: dict[str, ContextRebuildProject] = {}
            for row in rows:
                latest_by_project.setdefault(row.project_id, row)
            ids = [
                project_id
                for project_id in candidate_ids
                if (
                    project_id not in latest_by_project
                    or latest_by_project[project_id].status != "completed"
                    or int(latest_by_project[project_id].index_version or 0) < CONTEXT_INDEX_VERSION
                )
            ]
            if not ids:
                latest_completed = (
                    self.db.query(ContextRebuildJob)
                    .filter(
                        ContextRebuildJob.policy_version == CONTEXT_POLICY_VERSION,
                        ContextRebuildJob.status == "completed",
                    )
                    .order_by(ContextRebuildJob.completed_at.desc(), ContextRebuildJob.created_at.desc())
                    .first()
                )
                if latest_completed:
                    return latest_completed
        else:
            # An author-requested rebuild is deliberate even when the current
            # version has already completed, for example after repairing an
            # imported project's RAG source files.
            ids = candidate_ids
        semantic = bool(self.semantic_status().get("available"))
        job = ContextRebuildJob(
            policy_version=CONTEXT_POLICY_VERSION,
            status="queued" if ids else "completed",
            requested_by=(requested_by or "system")[:100],
            total_projects=len(ids),
            semantic_available=semantic,
            started_at=None if ids else datetime.utcnow(),
            completed_at=None if ids else datetime.utcnow(),
        )
        self.db.add(job)
        self.db.flush()
        for project_id in ids:
            self.db.add(ContextRebuildProject(
                job_id=job.id,
                project_id=project_id,
                status="queued",
                index_version=CONTEXT_INDEX_VERSION,
            ))
        self._sync_rebuild_operation(
            job,
            message="上下文索引重建已排队" if ids else "当前上下文索引已经是最新版本",
        )
        self.db.flush()
        return job

    def run_rebuild_job(self, job: ContextRebuildJob) -> ContextRebuildJob:
        if job.status == "completed":
            self._sync_rebuild_operation(job, message="上下文索引重建已完成")
            return job
        job.status = "running"
        job.started_at = job.started_at or datetime.utcnow()
        self._sync_rebuild_operation(job, message="正在准备上下文索引重建")
        self.db.commit()
        rows = (
            self.db.query(ContextRebuildProject)
            .filter(ContextRebuildProject.job_id == job.id)
            .order_by(ContextRebuildProject.created_at.asc())
            .all()
        )
        for row in rows:
            if row.status == "completed":
                continue
            row.status = "running"
            row.started_at = row.started_at or datetime.utcnow()
            row.error = None
            self.db.flush()
            try:
                row.current_source_type = "lexical"
                self._sync_rebuild_operation(job, message=f"正在重建作品 {row.project_id} 的关键词索引")
                self.db.commit()
                lexical = reindex_project(self.db, row.project_id)
                row.indexed_chunks = int(lexical.get("total_chunks") or 0)
                row.current_source_type = "semantic"
                semantic = self.build_semantic_embeddings(row.project_id)
                row.semantic_chunks = int(semantic.get("indexed") or 0)
                row.status = "completed"
                row.current_source_type = None
                row.completed_at = datetime.utcnow()
                job.completed_projects = int(job.completed_projects or 0) + 1
            except Exception as exc:
                row.status = "failed"
                row.error = str(exc)[:8000]
                row.completed_at = datetime.utcnow()
                job.failed_projects = int(job.failed_projects or 0) + 1
            self._sync_rebuild_operation(
                job,
                message=(f"作品 {row.project_id} 索引重建完成" if row.status == "completed" else f"作品 {row.project_id} 索引重建失败"),
            )
            if job.operation_id:
                from ..database.models import OperationRun
                from .operation_runtime import update_operation

                operation = self.db.query(OperationRun).filter(OperationRun.id == job.operation_id).first()
                if operation:
                    update_operation(
                        self.db,
                        operation,
                        event_type="checkpoint",
                        payload={"project_id": row.project_id, "status": row.status},
                        checkpoint=True,
                    )
            self.db.commit()
        remaining = self.db.query(ContextRebuildProject).filter(
            ContextRebuildProject.job_id == job.id,
            ContextRebuildProject.status.in_(["queued", "running"]),
        ).count()
        if not remaining:
            job.status = "completed" if not job.failed_projects else "failed"
            job.completed_at = datetime.utcnow()
        self._sync_rebuild_operation(
            job,
            message="上下文索引重建完成" if job.status == "completed" else "部分作品的上下文索引重建失败",
        )
        self.db.commit()
        return job

    def retry_rebuild_job(self, job: ContextRebuildJob) -> ContextRebuildJob:
        failed = (
            self.db.query(ContextRebuildProject)
            .filter(ContextRebuildProject.job_id == job.id, ContextRebuildProject.status == "failed")
            .all()
        )
        for row in failed:
            row.status = "queued"
            row.error = None
            row.started_at = None
            row.completed_at = None
        if failed:
            job.status = "queued"
            job.error = None
            job.failed_projects = max(0, int(job.failed_projects or 0) - len(failed))
            job.completed_at = None
            self._sync_rebuild_operation(job, message="失败作品已重新排队")
        self.db.flush()
        return job

    def project_rebuild_block_reason(self, project_id: str | None) -> str:
        if not project_id:
            return ""
        rows = (
            self.db.query(ContextRebuildProject)
            .filter(ContextRebuildProject.project_id == project_id)
            .order_by(ContextRebuildProject.created_at.desc())
            .all()
        )
        # SQLAlchemy Query.all() returns a list. Treat a lightweight/read-only
        # mock session as unavailable rebuild metadata rather than falsely
        # locking compatibility-only prompt rendering behind maintenance.
        if not isinstance(rows, list) or not rows:
            return ""
        project_row = next(
            (row for row in rows if row.job and row.job.policy_version == CONTEXT_POLICY_VERSION),
            None,
        )
        if project_row is None:
            return "Context indexes are not at the current policy version."
        job = project_row.job
        if project_row.status == "completed" and project_row.index_version >= CONTEXT_INDEX_VERSION:
            return ""
        if job.status in {"queued", "running"} or project_row.status in {"queued", "running"}:
            return "Context indexes are rebuilding for this project."
        if project_row.status == "failed":
            return "Context rebuild failed for this project; retry it before generating."
        return "Context indexes are not at the current policy version."


def prepare_task_context(
    db: Session,
    *,
    project_id: str | None,
    task_type: str,
    model: str | None = None,
    execution_route: str = "internal_api",
    arguments: dict[str, Any] | None = None,
    session_id: str | None = None,
    pinned_chunk_ids: Sequence[str] | None = None,
    pinned_source_ids: Sequence[str] | None = None,
) -> ContextManifest:
    return ContextOrchestrator(db).prepare(
        project_id=project_id,
        task_type=task_type,
        model=model,
        execution_route=execution_route,
        arguments=arguments,
        session_id=session_id,
        pinned_chunk_ids=pinned_chunk_ids,
        pinned_source_ids=pinned_source_ids,
    )


def manifest_is_usable(
    db: Session,
    manifest_id: str | None,
    *,
    project_id: str | None = None,
    require_external_evidence: bool = False,
) -> tuple[bool, str, ContextManifest | None]:
    if not manifest_id:
        return False, "No context manifest was provided.", None
    orchestrator = ContextOrchestrator(db)
    manifest = orchestrator.get_manifest(str(manifest_id), project_id)
    if not manifest:
        return False, "Context manifest was not found.", None
    valid, detail = orchestrator.validate(manifest, require_external_evidence=require_external_evidence)
    return valid, detail, manifest


def run_context_rebuild_job(job_id: str) -> None:
    """Run a persistent rebuild job in an isolated database session.

    FastAPI background tasks and startup recovery cannot safely reuse the
    request session that created a job, so this helper owns a short-lived
    session and leaves the job state resumable after a process restart.
    """
    from ..database.session import SessionLocal

    db = SessionLocal()
    try:
        job = db.query(ContextRebuildJob).filter(ContextRebuildJob.id == job_id).first()
        if not job:
            return
        ContextOrchestrator(db).run_rebuild_job(job)
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(ContextRebuildJob).filter(ContextRebuildJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)[:8000]
            job.completed_at = datetime.utcnow()
            ContextOrchestrator(db)._sync_rebuild_operation(job, message=f"上下文索引重建失败：{exc}")
            db.commit()
        raise
    finally:
        db.close()
