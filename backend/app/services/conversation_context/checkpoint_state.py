"""Checkpoint lifecycle CAS transitions and safe REST/SSE projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.architecture.resource_references import (
    public_resource_identity,
    public_resource_reference,
)

from .contracts import ConversationIdentity, ConversationKind
from .errors import ConversationContextError, ConversationContextErrorCode
from .runtime_types import CONVERSATION_CONTEXT_POLICY_VERSION, ConversationContextStore
from .store_phases import commit_context_phase, refresh_context_phase

_PUBLIC_ERROR_DETAIL = {
    ConversationContextErrorCode.CAPACITY_UNKNOWN.value: (
        "当前模型缺少可验证的容量档案，请先配置模型上下文窗口。"
    ),
    ConversationContextErrorCode.CHECKPOINT_FAILED.value: (
        "对话历史整理失败，本次任务未执行；请重试。"
        "若当前使用本机 Agent CLI，请切换已验证的 API 模型或新建对话。"
    ),
    ConversationContextErrorCode.CHECKPOINT_CANCELLED.value: "对话历史整理已取消。",
    ConversationContextErrorCode.CHECKPOINT_SUPERSEDED.value: (
        "对话历史已被较新的请求更新，请按最新状态重试。"
    ),
    ConversationContextErrorCode.SOURCE_CHANGED.value: (
        "对话或执行来源已变化，需要从完整记录重新整理。"
    ),
    ConversationContextErrorCode.REQUIRED_STATE_OVER_CAPACITY.value: (
        "必须保留的对话或协议状态超过当前模型容量。"
    ),
    ConversationContextErrorCode.FINAL_REQUEST_OVER_CAPACITY.value: (
        "最终模型请求超过当前模型容量。"
    ),
    ConversationContextErrorCode.TOOL_RESULT_OVER_CAPACITY.value: (
        "工具结果及下一步预留超过当前模型容量。"
    ),
}
_PUBLIC_ERROR_CODES = frozenset(code.value for code in ConversationContextErrorCode)


def safe_public_error_code(
    code: str | ConversationContextErrorCode | None,
) -> str | None:
    """Return a stable enum code without reflecting a legacy raw diagnostic."""

    if code is None:
        return None
    value = code.value if isinstance(code, ConversationContextErrorCode) else str(code)
    if value in _PUBLIC_ERROR_CODES:
        return value
    return ConversationContextErrorCode.CHECKPOINT_FAILED.value


def safe_public_error_detail(code: str | ConversationContextErrorCode | None) -> str | None:
    """Return a stable public message without echoing persisted provider diagnostics."""

    if code is None:
        return None
    value = safe_public_error_code(code)
    if value is None:
        return None
    return _PUBLIC_ERROR_DETAIL.get(value, "对话上下文处理失败，本次任务未执行。")


def _public_text(value: Any, *, max_length: int, strip: bool = True) -> str | None:
    """Accept only bounded persisted strings; never stringify opaque JSON."""

    if not isinstance(value, str):
        return None
    result = value.strip() if strip else value
    if not result or len(result) > max_length:
        return None
    return result


def _public_nonnegative_int(value: Any, *, default: int = 0) -> int:
    if type(value) is int and value >= 0:
        return value
    return default


def _public_warnings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        warning
        for item in value[:200]
        if (warning := _public_text(item, max_length=500, strip=False)) is not None
    ]


def _public_capacity_assurance(value: Any) -> str:
    if isinstance(value, str) and value in {"exact", "conservative", "unverified"}:
        return value
    return "unverified"


def _record_model_binding(record: Any | None) -> dict[str, Any] | None:
    value = getattr(record, "model_binding_json", None) if record is not None else None
    if not isinstance(value, Mapping):
        return None
    model = next(
        (
            item
            for item in (
                _public_text(value.get("normalized_model"), max_length=512),
                _public_text(value.get("model_name"), max_length=512),
                _public_text(value.get("model"), max_length=512),
            )
            if item is not None
        ),
        None,
    )
    provider = _public_text(value.get("provider"), max_length=100)
    if not model and not provider:
        return None
    display_name = _public_text(value.get("display_name"), max_length=512)
    return {
        "provider": provider,
        "model": model,
        "display_name": display_name or model or provider,
    }


def _safe_navigation(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return {
        "authority": "non_authoritative_navigation",
        **{
            field: [
                text
                for item in (source.get(field, []) if isinstance(source.get(field), list) else [])[
                    :200
                ]
                if (text := _public_text(item, max_length=20_000, strip=False)) is not None
            ]
            for field in (
                "current_objectives",
                "resolved_decisions",
                "superseded_directions",
                "unresolved_questions",
                "next_context_needed",
            )
        },
    }


def _safe_quotes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:200]:
        if not isinstance(item, Mapping):
            continue
        message_id = _public_text(item.get("message_id"), max_length=128)
        exact_quote = _public_text(item.get("exact_quote"), max_length=1_000_000, strip=False)
        quote_sha256 = _public_text(item.get("quote_sha256"), max_length=64)
        purpose = _public_text(item.get("purpose"), max_length=200)
        start_char = item.get("start_char")
        end_char = item.get("end_char")
        superseded = item.get("superseded", False)
        if (
            message_id is None
            or exact_quote is None
            or quote_sha256 is None
            or len(quote_sha256) != 64
            or any(char not in "0123456789abcdef" for char in quote_sha256.lower())
            or purpose is None
            or type(start_char) is not int
            or type(end_char) is not int
            or start_char < 0
            or end_char <= start_char
            or end_char - start_char != len(exact_quote)
            or not isinstance(superseded, bool)
        ):
            continue
        result.append(
            {
                "message_id": message_id,
                "start_char": start_char,
                "end_char": end_char,
                "exact_quote": exact_quote,
                "quote_sha256": quote_sha256.lower(),
                "purpose": purpose,
                "superseded": superseded,
            }
        )
    return result


def _safe_execution_ledger(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:1_000]:
        if not isinstance(item, Mapping):
            continue
        run_id = _public_text(item.get("run_id"), max_length=128)
        step_id = _public_text(item.get("step_id"), max_length=128)
        tool = _public_text(item.get("tool"), max_length=100)
        status = _public_text(item.get("status"), max_length=30)
        if run_id is None or step_id is None or tool is None or status is None:
            continue
        refs = item.get("resource_refs")
        safe_refs: list[dict[str, Any]] = []
        for ref in refs[:1_000] if isinstance(refs, list) else []:
            if not isinstance(ref, Mapping):
                continue
            safe_ref = public_resource_reference(
                ref.get("type"),
                ref.get("id"),
                ref.get("revision"),
            )
            if safe_ref is None:
                continue
            safe_refs.append(safe_ref)
        result.append(
            {
                "run_id": run_id,
                "step_id": step_id,
                "tool": tool,
                "status": status,
                "resource_refs": safe_refs,
                # Never echo a persisted diagnostic verbatim. A local
                # pre-release database may still contain raw provider/tool
                # text in this legacy-shaped field.
                "error_code": (
                    "assistant_run_step_error" if item.get("error_code") is not None else None
                ),
            }
        )
    return result


def _safe_project_refs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[:1_000]:
        if not isinstance(item, Mapping):
            continue
        identity = public_resource_identity(item.get("type"), item.get("id"))
        reason = _public_text(item.get("reason"), max_length=500)
        if identity is None or reason is None:
            continue
        resource_type, resource_id = identity
        result.append({"type": resource_type, "id": resource_id, "reason": reason})
    return result


def _record_warnings(record: Any | None) -> list[str]:
    validation = getattr(record, "validation_json", None) if record is not None else None
    if not isinstance(validation, Mapping) or not isinstance(validation.get("warnings"), list):
        return []
    return _public_warnings(validation["warnings"])


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def checkpoint_record_payload(
    *,
    store: ConversationContextStore,
    conversation_kind: ConversationKind | str,
    conversation_id: str,
    owner_id: str,
    checkpoint_id: str,
) -> dict[str, Any]:
    """Serialize one owner-checked checkpoint without private diagnostics."""

    kind = ConversationKind(conversation_kind)
    record = store.context_checkpoint(kind.value, conversation_id, checkpoint_id, owner_id=owner_id)
    if record is None:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 不存在或不属于当前会话。",
        )
    source_range = {
        "first_sequence": int(getattr(record, "source_first_sequence", 0) or 0),
        "last_sequence": int(getattr(record, "source_last_sequence", 0) or 0),
        "message_count": int(getattr(record, "source_message_count", 0) or 0),
        "source_hash": str(getattr(record, "source_hash", "") or ""),
    }
    error_code = safe_public_error_code(getattr(record, "error_code", None))
    return {
        "id": str(getattr(record, "id", "") or ""),
        "status": str(getattr(record, "status", "") or ""),
        "policy_version": int(getattr(record, "policy_version", 0) or 0),
        "schema_version": str(getattr(record, "schema_version", "") or ""),
        "source_range": source_range,
        "source_message_count": source_range["message_count"],
        "original_tokens": getattr(record, "original_tokens", None),
        "checkpoint_tokens": getattr(record, "checkpoint_tokens", None),
        "original_history_tokens": getattr(record, "original_tokens", None),
        "semantic_navigation": _safe_navigation(getattr(record, "semantic_navigation_json", None)),
        "author_quotes": _safe_quotes(getattr(record, "author_quotes_json", None)),
        "execution_ledger": _safe_execution_ledger(getattr(record, "execution_ledger_json", None)),
        "project_refs": _safe_project_refs(getattr(record, "project_refs_json", None)),
        "model_binding": _record_model_binding(record),
        "warnings": _record_warnings(record),
        "error_code": error_code,
        "error_detail": safe_public_error_detail(error_code),
        "created_at": _iso(getattr(record, "created_at", None)),
        "completed_at": _iso(getattr(record, "completed_at", None)),
    }


def _empty_state_payload(
    *,
    trigger: str | None,
    recent_exact_turn_count: int | None,
    original_history_tokens: int | None,
    active_history_tokens: int | None,
    warnings: Sequence[str],
    error: ConversationContextError | None,
) -> dict[str, Any]:
    code = error.code.value if error is not None else None
    return {
        "status": "ready",
        "policy_version": CONVERSATION_CONTEXT_POLICY_VERSION,
        "active_checkpoint_id": None,
        "latest_checkpoint_id": None,
        "source_message_count": 0,
        "recent_exact_turn_count": _public_nonnegative_int(recent_exact_turn_count),
        "original_history_tokens": _public_nonnegative_int(original_history_tokens),
        "active_history_tokens": _public_nonnegative_int(active_history_tokens),
        "trigger": _public_text(trigger, max_length=100) or "within_capacity",
        "capacity_assurance": "unverified",
        "provider": None,
        "model": None,
        "model_binding": None,
        "warnings": _public_warnings(warnings),
        "error_code": code,
        "error_detail": safe_public_error_detail(code),
        "retryable": False,
        "updated_at": None,
    }


def _state_status(latest_status: str, active: Any, error: Any) -> str:
    if error is not None or latest_status in {"failed", "cancelled"}:
        return "failed"
    if latest_status in {"pending", "compressing"}:
        return "compressing"
    if latest_status == "superseded" and active is None:
        return "failed"
    return "ready"


def _state_error_code(
    latest: Any,
    latest_status: str,
    active: Any,
    error: ConversationContextError | None,
) -> str | None:
    if error is not None:
        return error.code.value
    if latest is None:
        return None
    stored = getattr(latest, "error_code", None)
    if stored:
        return safe_public_error_code(stored)
    if latest_status == "cancelled":
        return ConversationContextErrorCode.CHECKPOINT_CANCELLED.value
    if latest_status == "superseded" and active is None:
        return ConversationContextErrorCode.CHECKPOINT_SUPERSEDED.value
    return None


def _active_source_count(records: Sequence[Any], active: Any) -> int:
    if active is None:
        return 0
    active_ids = {str(getattr(active, "id", "") or "")}
    validation = getattr(active, "validation_json", None)
    if isinstance(validation, Mapping) and isinstance(validation.get("segment_ids"), list):
        active_ids.update(str(item) for item in validation["segment_ids"])
    return sum(
        int(getattr(record, "source_message_count", 0) or 0)
        for record in records
        if str(getattr(record, "id", "") or "") in active_ids
    )


def context_state_payload(
    *,
    store: ConversationContextStore,
    conversation_kind: ConversationKind | str,
    conversation_id: str,
    owner_id: str,
    trigger: str | None = None,
    recent_exact_turn_count: int | None = None,
    original_history_tokens: int | None = None,
    active_history_tokens: int | None = None,
    warnings: Sequence[str] = (),
    error: ConversationContextError | None = None,
) -> dict[str, Any]:
    """Serialize latest durable state with stable, non-sensitive errors."""

    kind = ConversationKind(conversation_kind)
    state = store.context_state(kind.value, conversation_id, owner_id=owner_id)
    if state is None:
        return _empty_state_payload(
            trigger=trigger,
            recent_exact_turn_count=recent_exact_turn_count,
            original_history_tokens=original_history_tokens,
            active_history_tokens=active_history_tokens,
            warnings=warnings,
            error=error,
        )
    records = tuple(store.context_checkpoints(kind.value, conversation_id, owner_id=owner_id))
    stored_active_id = str(getattr(state, "active_checkpoint_id", "") or "") or None
    active = next(
        (record for record in records if str(getattr(record, "id", "")) == stored_active_id),
        None,
    )
    active_reference_invalid = stored_active_id is not None and active is None
    active_id = str(getattr(active, "id", "") or "") or None
    latest = records[-1] if records else None
    latest_status = str(getattr(latest, "status", "") or "") if latest else ""
    cached_value = getattr(state, "last_budget_json", None)
    cached = dict(cached_value) if isinstance(cached_value, Mapping) else {}
    binding = _record_model_binding(active or latest)
    code = (
        ConversationContextErrorCode.SOURCE_CHANGED.value
        if active_reference_invalid
        else _state_error_code(latest, latest_status, active, error)
    )
    combined_warnings = list(
        dict.fromkeys(
            [
                *_record_warnings(active or latest),
                *_public_warnings(cached.get("warnings")),
                *_public_warnings(warnings),
            ]
        )
    )
    return {
        "status": (
            "failed" if active_reference_invalid else _state_status(latest_status, active, error)
        ),
        "policy_version": CONVERSATION_CONTEXT_POLICY_VERSION,
        "active_checkpoint_id": active_id,
        "latest_checkpoint_id": (
            str(getattr(latest, "id", "") or "") or None if latest is not None else None
        ),
        "source_message_count": _active_source_count(records, active),
        "recent_exact_turn_count": _public_nonnegative_int(
            recent_exact_turn_count
            if recent_exact_turn_count is not None
            else cached.get("recent_exact_turn_count")
        ),
        "original_history_tokens": _public_nonnegative_int(
            original_history_tokens
            if original_history_tokens is not None
            else cached.get("original_history_tokens")
        ),
        "active_history_tokens": _public_nonnegative_int(
            active_history_tokens
            if active_history_tokens is not None
            else cached.get("active_history_tokens")
        ),
        "trigger": (
            _public_text(trigger, max_length=100)
            or _public_text(cached.get("trigger"), max_length=100)
            or "within_capacity"
        ),
        "capacity_assurance": (
            "unverified"
            if active_reference_invalid
            else _public_capacity_assurance(cached.get("capacity_assurance"))
        ),
        "provider": str(binding.get("provider") or "") or None if binding else None,
        "model": str(binding.get("model") or "") or None if binding else None,
        "model_binding": binding,
        "warnings": combined_warnings,
        "error_code": code,
        "error_detail": safe_public_error_detail(code),
        "retryable": code
        in {
            ConversationContextErrorCode.CHECKPOINT_FAILED.value,
            ConversationContextErrorCode.CHECKPOINT_CANCELLED.value,
            ConversationContextErrorCode.CHECKPOINT_SUPERSEDED.value,
            ConversationContextErrorCode.SOURCE_CHANGED.value,
        },
        "updated_at": _iso(getattr(state, "updated_at", None)),
    }


def publish_or_resolve_checkpoint_race(
    *,
    store: ConversationContextStore,
    conversation: ConversationIdentity,
    owner_id: str,
    checkpoint_id: str,
    expected_revision: int,
) -> None:
    """Publish once, or deterministically retire a ready inactive CAS loser."""

    if store.publish_context_checkpoint(
        conversation.kind.value,
        conversation.id,
        checkpoint_id,
        expected_revision,
        owner_id=owner_id,
    ):
        return
    detail = "checkpoint 发布时会话 revision 已变化。"
    for _ in range(3):
        refresh_context_phase(store)
        state = store.context_state(conversation.kind.value, conversation.id, owner_id=owner_id)
        if state is None:
            raise ConversationContextError(
                ConversationContextErrorCode.SOURCE_CHANGED,
                "无法读取当前 owner 的 checkpoint 状态。",
            )
        if str(getattr(state, "active_checkpoint_id", "") or "") == checkpoint_id:
            return
        if store.supersede_inactive_context_checkpoint(
            conversation.kind.value,
            conversation.id,
            checkpoint_id,
            int(getattr(state, "revision", -1)),
            owner_id=owner_id,
            error_code=ConversationContextErrorCode.CHECKPOINT_SUPERSEDED.value,
            error_detail=detail,
        ):
            commit_context_phase(store)
            raise ConversationContextError(
                ConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
                detail,
                details={"checkpoint_id": checkpoint_id},
            )
    refresh_context_phase(store)
    state = store.context_state(conversation.kind.value, conversation.id, owner_id=owner_id)
    if state is not None and str(getattr(state, "active_checkpoint_id", "") or "") == checkpoint_id:
        return
    raise ConversationContextError(
        ConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
        "checkpoint 并发发布状态持续变化，请按最新会话状态重试。",
        details={"checkpoint_id": checkpoint_id},
    )


def mark_task_cancelled_checkpoint(
    *,
    store: ConversationContextStore,
    conversation: ConversationIdentity,
    owner_id: str,
    checkpoint_id: str,
) -> None:
    refresh_context_phase(store)
    current = store.context_checkpoint(
        conversation.kind.value, conversation.id, checkpoint_id, owner_id=owner_id
    )
    status = str(getattr(current, "status", "") or "") if current is not None else ""
    if status not in {"pending", "compressing"}:
        return
    updated = store.update_context_checkpoint_status(
        conversation.kind.value,
        conversation.id,
        checkpoint_id,
        "cancelled",
        owner_id=owner_id,
        expected_statuses=[status],
        error_code=ConversationContextErrorCode.CHECKPOINT_CANCELLED.value,
        error_detail="checkpoint 承载任务已取消。",
        cancel_requested_at=datetime.utcnow(),
    )
    if updated is not None:
        commit_context_phase(store)


def cancel_checkpoint_attempt(
    *,
    store: ConversationContextStore,
    conversation_kind: ConversationKind | str,
    conversation_id: str,
    owner_id: str,
    checkpoint_id: str,
) -> dict[str, Any]:
    """Cancel only an in-flight derived attempt and commit that transition."""

    kind = ConversationKind(conversation_kind)
    record = store.context_checkpoint(kind.value, conversation_id, checkpoint_id, owner_id=owner_id)
    if record is None:
        raise ConversationContextError(
            ConversationContextErrorCode.SOURCE_CHANGED,
            "checkpoint 不存在或不属于当前会话。",
        )
    status = str(getattr(record, "status", "") or "")
    if status == "cancelled":
        return checkpoint_record_payload(
            store=store,
            conversation_kind=kind,
            conversation_id=conversation_id,
            owner_id=owner_id,
            checkpoint_id=checkpoint_id,
        )
    if status not in {"pending", "compressing"}:
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_CANCELLED,
            "只有 pending/compressing checkpoint 可以取消。",
            details={"status": status},
        )
    updated = store.update_context_checkpoint_status(
        kind.value,
        conversation_id,
        checkpoint_id,
        "cancelled",
        owner_id=owner_id,
        expected_statuses=[status],
        error_code=ConversationContextErrorCode.CHECKPOINT_CANCELLED.value,
        error_detail="作者取消了 checkpoint 整理。",
        cancel_requested_at=datetime.utcnow(),
    )
    if updated is None:
        raise ConversationContextError(
            ConversationContextErrorCode.CHECKPOINT_SUPERSEDED,
            "checkpoint 状态已变化，取消未生效。",
        )
    commit_context_phase(store)
    return checkpoint_record_payload(
        store=store,
        conversation_kind=kind,
        conversation_id=conversation_id,
        owner_id=owner_id,
        checkpoint_id=checkpoint_id,
    )


__all__ = [
    "cancel_checkpoint_attempt",
    "checkpoint_record_payload",
    "context_state_payload",
    "mark_task_cancelled_checkpoint",
    "publish_or_resolve_checkpoint_race",
    "safe_public_error_detail",
    "safe_public_error_code",
]
