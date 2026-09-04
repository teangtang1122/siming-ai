"""Deterministic execution receipts derived from durable Agent run steps."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from .canonical import canonical_sha256
from .contracts import ExecutionLedgerEntry, ProjectReference, ResourceReference
from .tool_transactions import ToolExecutionReceipt

_SUCCESS_STEP_STATUSES = frozenset({"ok", "completed", "success", "succeeded"})
_OPEN_STEP_STATUSES = frozenset({"pending", "queued", "running", "in_progress"})
_CLOSED_NON_ERROR_STEP_STATUSES = frozenset({
    "aborted",
    "cancelled",
    "canceled",
    "skipped",
    "superseded",
})
_PARTIAL_COMMIT_STEP_STATUSES = {"create_outline_nodes": frozenset({"error"})}


def _allows_committed_refs(tool: str, status: str) -> bool:
    normalized = status.strip().lower()
    return normalized in _SUCCESS_STEP_STATUSES or normalized in _PARTIAL_COMMIT_STEP_STATUSES.get(
        tool,
        (),
    )


def _json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def resource_references_from_run_step(
    step: Any,
    *,
    revision_resolver: Callable[[str, str], int | str | None] | None = None,
) -> tuple[ResourceReference, ...]:
    """Read only the server-authored ``output_refs`` field.

    Tool result prose is deliberately not mined for IDs: doing so would turn a
    model/tool display payload into a second authority path.  A reference may
    be persisted as ``{"outline": "id"}`` or as
    ``{"outline": {"id": "id", "revision": 3}}``.
    """

    tool = str(getattr(step, "tool", "") or "").strip()
    status = str(getattr(step, "status", "") or "").strip()
    if not _allows_committed_refs(tool, status):
        return ()
    raw_refs = _json_mapping(getattr(step, "output_refs", None))
    refs: list[ResourceReference] = []
    seen: set[tuple[str, str]] = set()
    for raw_type in sorted(raw_refs):
        resource_type = str(raw_type or "").strip()
        raw_value = raw_refs[raw_type]
        raw_items = raw_value if isinstance(raw_value, list) else [raw_value]
        for raw_item in raw_items:
            revision: int | str | None = None
            if isinstance(raw_item, Mapping):
                resource_id = str(raw_item.get("id") or "").strip()
                raw_revision = raw_item.get("revision")
                if isinstance(raw_revision, (int, str)) and not isinstance(raw_revision, bool):
                    revision = raw_revision
            else:
                resource_id = str(raw_item or "").strip()
            if not resource_type or not resource_id or (resource_type, resource_id) in seen:
                continue
            refs.append(ResourceReference(resource_type, resource_id, revision))
            seen.add((resource_type, resource_id))
    # Kept as a compatibility parameter for callers that already expose it.
    # A current-state lookup cannot prove the revision committed by this step.
    _ = revision_resolver
    return tuple(refs)


def execution_ledger_from_run_steps(
    steps: Sequence[Any],
    *,
    revision_resolver: Callable[[str, str], int | str | None] | None = None,
) -> tuple[ExecutionLedgerEntry, ...]:
    """Build auditable ledger entries without asking a model what happened."""

    entries: list[ExecutionLedgerEntry] = []
    seen_step_ids: set[str] = set()
    for step in steps:
        step_id = str(getattr(step, "id", "") or "").strip()
        run_id = str(getattr(step, "run_id", "") or "").strip()
        tool = str(getattr(step, "tool", "") or "").strip()
        status = str(getattr(step, "status", "") or "").strip()
        if not step_id or not run_id or not tool or not status or step_id in seen_step_ids:
            continue
        # Operator diagnostics may contain provider payloads, arguments, or
        # credentials. Active context needs only the coarse durable fact that
        # the step failed; it must never copy the raw diagnostic.
        has_error = bool(str(getattr(step, "error", "") or "").strip())
        entries.append(
            ExecutionLedgerEntry(
                run_id=run_id,
                step_id=step_id,
                tool=tool,
                status=status,
                resource_refs=resource_references_from_run_step(
                    step,
                    revision_resolver=revision_resolver,
                ),
                error_code=("assistant_run_step_error" if has_error else None),
            )
        )
        seen_step_ids.add(step_id)
    return tuple(entries)


def execution_source_hashes_from_run_steps(steps: Sequence[Any]) -> dict[str, str]:
    """Hash retry/resolution state that is intentionally absent from the model ledger."""

    result: dict[str, str] = {}
    for step in steps:
        step_id = str(getattr(step, "id", "") or "").strip()
        if not step_id:
            continue
        result[step_id] = canonical_sha256(
            {
                "id": step_id,
                "run_id": str(getattr(step, "run_id", "") or ""),
                "tool": str(getattr(step, "tool", "") or ""),
                "status": str(getattr(step, "status", "") or ""),
                "output_refs": _json_mapping(getattr(step, "output_refs", None)),
                "error": str(getattr(step, "error", "") or ""),
                "retry_of_step_id": str(getattr(step, "retry_of_step_id", "") or ""),
                "resolved_step_id": str(getattr(step, "resolved_step_id", "") or ""),
                "attempt_no": int(getattr(step, "attempt_no", 0) or 0),
            }
        )
    return result


def fold_execution_ledger(
    entries: Iterable[ExecutionLedgerEntry],
) -> tuple[ExecutionLedgerEntry, ...]:
    """Converge active receipts without deleting the durable RunStep audit.

    The input order is the durable step order.  Later receipts for the same
    resource replace earlier committed receipts.  Successful reads/searches
    without a committed resource are intentionally absent from cross-turn
    context.  Every still-open operation remains mandatory, while closed
    unresolved failures converge to only the most recent receipt per tool.

    Retry/resolution families are removed before this function by the
    owner-aware persistence adapter.  This function therefore never guesses
    whether an error was resolved from prose or tool arguments.
    """

    ordered = tuple(entries)
    latest_by_resource: dict[tuple[str, str], int] = {}
    latest_error_by_tool: dict[str, int] = {}
    keep: set[int] = set()
    for index, entry in enumerate(ordered):
        status = entry.status.strip().lower()
        if status in _OPEN_STEP_STATUSES:
            keep.add(index)
        elif (
            status not in _SUCCESS_STEP_STATUSES
            and status not in _CLOSED_NON_ERROR_STEP_STATUSES
        ):
            latest_error_by_tool[entry.tool] = index
        for reference in entry.resource_refs:
            latest_by_resource[(reference.type, reference.id)] = index
    keep.update(latest_by_resource.values())
    for index in latest_error_by_tool.values():
        entry = ordered[index]
        # A partial-commit error is useful only while at least one of its
        # committed resources is still the current receipt.  A later receipt
        # for every resource deterministically supersedes it.
        if not entry.resource_refs or any(
            latest_by_resource[(reference.type, reference.id)] == index
            for reference in entry.resource_refs
        ):
            keep.add(index)
    return tuple(entry for index, entry in enumerate(ordered) if index in keep)


def project_references_from_execution_ledger(
    entries: Iterable[ExecutionLedgerEntry],
) -> tuple[ProjectReference, ...]:
    refs: list[ProjectReference] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        for reference in entry.resource_refs:
            identity = (reference.type, reference.id)
            if identity in seen:
                continue
            refs.append(
                ProjectReference(
                    type=reference.type,
                    id=reference.id,
                    reason="会话曾操作该资源；使用前必须重新读取当前版本",
                )
            )
            seen.add(identity)
    return tuple(refs)


def tool_receipts_from_run_steps(
    steps: Sequence[Any],
    *,
    write_tools: Iterable[str] = (),
    reread_for_tool: Callable[[str], str | None] | None = None,
) -> tuple[ToolExecutionReceipt, ...]:
    """Create the compact same-turn replacement for consumed transactions.

    A RunStep ``detail``/``error`` may contain provider diagnostics or an
    exception string.  Those fields remain outside model-visible context and
    must never be copied into a compact receipt.  The receipt is derived only
    from the server-owned tool/status/resource-reference state.
    """

    writes = {str(item) for item in write_tools}
    receipts: list[ToolExecutionReceipt] = []
    for step in steps:
        step_id = str(getattr(step, "id", "") or "").strip()
        tool = str(getattr(step, "tool", "") or "").strip()
        status = str(getattr(step, "status", "") or "").strip()
        if not step_id or not tool or not status:
            continue
        refs = resource_references_from_run_step(step)
        normalized_status = status.lower()
        if normalized_status in _SUCCESS_STEP_STATUSES:
            summary = f"{tool} 已完成"
        elif normalized_status in {"cancelled", "canceled", "aborted"}:
            summary = f"{tool} 已取消"
        elif normalized_status in {"skipped", "superseded"}:
            summary = f"{tool} 未执行"
        else:
            summary = f"{tool} 执行失败"
        if refs:
            summary += f"；已记录 {len(refs)} 个持久资源引用，使用前必须重新读取"
        receipts.append(
            ToolExecutionReceipt(
                step_id=step_id,
                tool=tool,
                status=status,
                summary=summary,
                resource_ids=tuple(reference.id for reference in refs),
                result_ref=f"assistant_run_step:{step_id}",
                reread=(reread_for_tool(tool) if reread_for_tool is not None else None),
                write_committed=(
                    tool in writes
                    and (
                        status.lower() in {"ok", "completed", "success", "succeeded"} or bool(refs)
                    )
                ),
            )
        )
    return tuple(receipts)


__all__ = [
    "execution_ledger_from_run_steps",
    "execution_source_hashes_from_run_steps",
    "fold_execution_ledger",
    "project_references_from_execution_ledger",
    "resource_references_from_run_step",
    "tool_receipts_from_run_steps",
]
