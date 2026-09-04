"""Declared extraction of durable resource references from tool results.

Only exact, versioned tool contracts are recognized here. Result prose and
arbitrary ``*_id`` keys are deliberately inert. A small set of creation
tools may recover a missing session/import identifier from the corresponding
persisted RunStep request; revisions always come from the returned result.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.architecture.resource_references import PUBLIC_RESOURCE_REFERENCE_TYPES

JsonPath = tuple[str, ...]

_SUCCESS_STATUSES = frozenset({"ok", "completed", "success", "succeeded"})
_PARTIAL_COMMIT_TOOLS = frozenset({"create_outline_nodes"})


@dataclass(frozen=True)
class ToolOutputReferenceRule:
    """One exact result/request path that proves a durable resource write."""

    resource_type: str
    id_path: JsonPath | None = None
    revision_path: JsonPath | None = None
    items_path: JsonPath | None = None
    request_id_path: JsonPath | None = None

    def __post_init__(self) -> None:
        if not self.resource_type or (self.id_path is None and self.request_id_path is None):
            raise ValueError("resource_type and an ID path are required")


def _rule(
    resource_type: str,
    id_path: JsonPath,
    revision_path: JsonPath | None = None,
) -> ToolOutputReferenceRule:
    return ToolOutputReferenceRule(resource_type, id_path, revision_path)


def _request_rule(
    resource_type: str,
    request_id_path: JsonPath,
    revision_path: JsonPath | None = None,
) -> ToolOutputReferenceRule:
    return ToolOutputReferenceRule(
        resource_type,
        revision_path=revision_path,
        request_id_path=request_id_path,
    )


# Every path below is checked against the concrete return value of its handler.
# Do not add a tool merely because its response happens to contain an ``id``.
_ENTITY_RULES: dict[str, tuple[ToolOutputReferenceRule, ...]] = {
    "create_character": (_rule("character", ("data", "id"), ("data", "current_version")),),
    "update_character": (_rule("character", ("data", "id"), ("data", "current_version")),),
    "create_outline_node": (_rule("outline", ("data", "id")),),
    "update_outline_node": (_rule("outline", ("data", "id")),),
    "create_outline_nodes": (
        ToolOutputReferenceRule(
            "outline",
            id_path=("id",),
            items_path=("data", "nodes"),
        ),
    ),
    "create_worldbuilding_entry": (_rule("worldbuilding", ("data", "id")),),
    "update_worldbuilding_entry": (_rule("worldbuilding", ("data", "id")),),
    "chapter_writer": (_rule("chapter_draft", ("data", "draft_id")),),
    "save_external_chapter_draft": (_rule("chapter_draft", ("data", "draft_id")),),
    "outline_writer": (_rule("outline_draft", ("data", "draft_id")),),
    "save_external_outline_draft": (_rule("outline_draft", ("data", "draft_id")),),
    "start_cataloging_job": (_rule("cataloging_job", ("data", "id")),),
    "start_deconstruct_job": (_rule("deconstruct_report", ("data", "id")),),
    "create_scheduled_task": (_rule("scheduled_task", ("data", "id")),),
    "update_scheduled_task": (_rule("scheduled_task", ("data", "id")),),
    # Creation contracts. Request fallbacks are intentionally tool-specific.
    "start_novel_creation_session": (
        _rule(
            "creation_session",
            ("data", "session_id"),
            ("data", "session", "revision"),
        ),
    ),
    "patch_creation_session": (
        _rule("creation_session", ("data", "session_id"), ("data", "revision")),
    ),
    "patch_creation_artifact": (
        _request_rule(
            "creation_session",
            ("session_id",),
            ("data", "artifact", "revision"),
        ),
    ),
    "lock_creation_fields": (
        _request_rule("creation_session", ("session_id",), ("data", "revision")),
    ),
    "unlock_creation_fields": (
        _request_rule("creation_session", ("session_id",), ("data", "revision")),
    ),
    "undo_creation_artifact": (
        _request_rule(
            "creation_session",
            ("session_id",),
            ("data", "artifact", "revision"),
        ),
    ),
    "patch_creation_entity": (
        _rule("creation_entity", ("data", "entity", "id"), ("data", "entity", "revision")),
        _rule(
            "creation_session",
            ("data", "entity", "session_id"),
            ("data", "artifact", "revision"),
        ),
    ),
    "delete_creation_entity": (
        _rule("creation_entity", ("data", "entity", "id"), ("data", "entity", "revision")),
        _rule(
            "creation_session",
            ("data", "entity", "session_id"),
            ("data", "artifact", "revision"),
        ),
    ),
    "restore_creation_artifact_version": (
        _rule(
            "creation_session",
            ("data", "restored_version", "session_id"),
            ("data", "revision"),
        ),
    ),
    "confirm_creation_artifact": (
        _rule("creation_session", ("data", "id"), ("data", "revision")),
    ),
    "generate_creation_artifact": (
        _rule("creation_session", ("data", "session", "id"), ("data", "session", "revision")),
    ),
    "refine_creation_artifact": (
        _rule("creation_session", ("data", "session", "id"), ("data", "session", "revision")),
    ),
    "regenerate_creation_artifact": (
        _rule("creation_session", ("data", "session", "id"), ("data", "session", "revision")),
    ),
    "import_creation_material": (_rule("creation_import", ("data", "id")),),
    "apply_creation_import": (
        _request_rule("creation_import", ("import_id",)),
    ),
    "finalize_creation_session": (
        _rule("project", ("data", "project_id")),
        # Finalize returns the project but not the session revision. The
        # session ID is still a committed target; no revision is invented.
        _request_rule("creation_session", ("session_id",)),
    ),
    # This aggregate receipt is authored only after the server rereads the
    # creation session and proves that a direct-MCP write advanced revision.
    "mcp_verified_write": (
        _rule(
            "creation_session",
            ("data", "session_id"),
            ("data", "revision_after"),
        ),
    ),
}

_DECLARED_RESOURCE_TYPES = frozenset(
    rule.resource_type for rules in _ENTITY_RULES.values() for rule in rules
)
if _DECLARED_RESOURCE_TYPES != PUBLIC_RESOURCE_REFERENCE_TYPES:
    missing_contracts = sorted(_DECLARED_RESOURCE_TYPES - PUBLIC_RESOURCE_REFERENCE_TYPES)
    unused_contracts = sorted(PUBLIC_RESOURCE_REFERENCE_TYPES - _DECLARED_RESOURCE_TYPES)
    raise RuntimeError(
        "tool output reference contract drift: "
        f"missing_id_grammars={missing_contracts}, unused_id_grammars={unused_contracts}"
    )


def _path_value(value: Mapping[str, Any], path: JsonPath | None) -> Any:
    if path is None:
        return None
    current: Any = value
    for part in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _identifier(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    return str(value).strip()


def _revision(value: Any) -> int | str | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _statuses_allow_references(
    tool_name: str,
    result_status: str,
    step_status: str | None,
    *,
    allow_partial_commit_refs: bool,
) -> bool:
    persisted_status = str(step_status or result_status).strip().lower()
    if result_status in _SUCCESS_STATUSES and persisted_status in _SUCCESS_STATUSES:
        return True
    return (
        allow_partial_commit_refs
        and tool_name in _PARTIAL_COMMIT_TOOLS
        and result_status == "error"
        and persisted_status == "error"
    )


def _append_reference(
    references: dict[str, list[dict[str, Any]]],
    seen: set[tuple[str, str]],
    *,
    resource_type: str,
    resource_id: str,
    revision: int | str | None,
) -> None:
    identity = (resource_type, resource_id)
    if not resource_id or identity in seen:
        return
    value: dict[str, Any] = {"id": resource_id}
    if revision is not None:
        value["revision"] = revision
    references.setdefault(resource_type, []).append(value)
    seen.add(identity)


def output_refs_from_tool_result(
    tool_name: str,
    result: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    step_status: str | None = None,
    allow_partial_commit_refs: bool = True,
) -> dict[str, Any]:
    """Return references proven by one declared structured tool contract.

    Unknown tools, skipped results, mismatched RunStep statuses and prose-only
    IDs yield no references. ``create_outline_nodes`` is the sole exception to
    success-only extraction because its handler may commit earlier nodes before
    returning an aggregate error for a later node.
    """

    name = str(tool_name or "").strip()
    rules = _ENTITY_RULES.get(name, ())
    result_status = str(result.get("status") or "").strip().lower()
    if name == "mcp_verified_write":
        revision_after = _path_value(result, ("data", "revision_after"))
        if isinstance(revision_after, bool) or not isinstance(revision_after, int):
            return {}
    if not rules or not _statuses_allow_references(
        name,
        result_status,
        step_status,
        allow_partial_commit_refs=allow_partial_commit_refs,
    ):
        return {}

    request_payload = request if isinstance(request, Mapping) else {}
    references: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for rule in rules:
        if rule.items_path is not None:
            raw_items = _path_value(result, rule.items_path)
            if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
                continue
            for item in raw_items:
                if not isinstance(item, Mapping):
                    continue
                _append_reference(
                    references,
                    seen,
                    resource_type=rule.resource_type,
                    resource_id=_identifier(_path_value(item, rule.id_path)),
                    revision=_revision(_path_value(item, rule.revision_path)),
                )
            continue

        resource_id = _identifier(_path_value(result, rule.id_path))
        if not resource_id and rule.request_id_path is not None:
            resource_id = _identifier(_path_value(request_payload, rule.request_id_path))
        _append_reference(
            references,
            seen,
            resource_type=rule.resource_type,
            resource_id=resource_id,
            # A request may prove which resource was targeted, but never the
            # post-write revision. Only the structured result supplies it.
            revision=_revision(_path_value(result, rule.revision_path)),
        )

    return {
        resource_type: values[0] if len(values) == 1 else values
        for resource_type, values in references.items()
        if values
    }


def serialize_output_refs(
    tool_name: str,
    result: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
    step_status: str | None = None,
    allow_partial_commit_refs: bool = True,
) -> str | None:
    references = output_refs_from_tool_result(
        tool_name,
        result,
        request=request,
        step_status=step_status,
        allow_partial_commit_refs=allow_partial_commit_refs,
    )
    if not references:
        return None
    return json.dumps(references, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "ToolOutputReferenceRule",
    "output_refs_from_tool_result",
    "serialize_output_refs",
]
