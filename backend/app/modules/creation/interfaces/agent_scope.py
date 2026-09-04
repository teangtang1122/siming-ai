"""Narrow tool scope used by the conversational creation Agent.

This module is intentionally dependency-free so both the in-process assistant
and the MCP permission registry can share one allowlist without creating an
import cycle.
"""

from __future__ import annotations

CREATION_AGENT_TOOL_NAMES = frozenset({
    "get_creation_session",
    "get_creation_snapshot",
    "get_creation_operation",
    "get_creation_artifact",
    "list_creation_artifacts",
    "get_creation_dependencies",
    "get_creation_dependency_graph",
    "validate_creation_consistency",
    "patch_creation_session",
    "patch_creation_artifact",
    "lock_creation_fields",
    "unlock_creation_fields",
    "undo_creation_artifact",
    "list_creation_entities",
    "get_creation_entity",
    "patch_creation_entity",
    "delete_creation_entity",
    "list_creation_artifact_versions",
    "get_creation_artifact_diff",
    "restore_creation_artifact_version",
    "confirm_creation_artifact",
    "generate_creation_artifact",
    "refine_creation_artifact",
    "regenerate_creation_artifact",
    "cancel_creation_operation",
    "pause_creation_operation",
    "resume_creation_operation",
    "retry_creation_operation",
    "validate_creation_session",
    "finalize_creation_session",
    "preview_creation_import",
    "apply_creation_import",
})

# Android's standalone creation workspace has no durable background-operation,
# artifact-version, or file-import runtime. Keep that platform difference in
# the generated contract so those tools are never advertised as executable.
MOBILE_CREATION_UNSUPPORTED_TOOL_NAMES = frozenset({
    "get_creation_operation",
    "cancel_creation_operation",
    "pause_creation_operation",
    "resume_creation_operation",
    "retry_creation_operation",
    "undo_creation_artifact",
    "list_creation_artifact_versions",
    "get_creation_artifact_diff",
    "restore_creation_artifact_version",
    "preview_creation_import",
    "apply_creation_import",
})
MOBILE_CREATION_AGENT_TOOL_NAMES = (
    CREATION_AGENT_TOOL_NAMES - MOBILE_CREATION_UNSUPPORTED_TOOL_NAMES
)

CREATION_MODEL_SPAWNING_TOOL_NAMES = frozenset({
    "generate_creation_artifact",
    "refine_creation_artifact",
    "regenerate_creation_artifact",
})

# A conversational creation turn is deliberately incremental. One user
# message may commit one atomic business mutation (which can carry all facts
# for that one target), but it may not advance, confirm, and generate several
# downstream artifacts on the author's behalf. Failed mutations are bounded
# separately so a malformed payload cannot produce an unbounded retry loop.
CREATION_TURN_MAX_SUCCESSFUL_WRITES = 1
CREATION_TURN_MAX_FAILED_WRITES = 3
CREATION_WRITE_SUCCESS_STATUSES = frozenset({"ok", "running"})

CREATION_AGENT_REVISION_TOOL_NAMES = frozenset({
    "patch_creation_session",
    "patch_creation_artifact",
    "lock_creation_fields",
    "unlock_creation_fields",
    "undo_creation_artifact",
    "patch_creation_entity",
    "delete_creation_entity",
    "restore_creation_artifact_version",
    "confirm_creation_artifact",
    "generate_creation_artifact",
    "refine_creation_artifact",
    "regenerate_creation_artifact",
    "apply_creation_import",
})

CREATION_AGENT_WRITE_TOOL_NAMES = CREATION_AGENT_REVISION_TOOL_NAMES | frozenset({
    "cancel_creation_operation",
    "pause_creation_operation",
    "resume_creation_operation",
    "retry_creation_operation",
    "finalize_creation_session",
})


def creation_turn_write_denial(
    tool_name: str,
    *,
    successful_writes: int,
    failed_writes: int,
) -> dict[str, object] | None:
    """Return the shared deterministic write-boundary result, if closed."""

    if tool_name not in CREATION_AGENT_WRITE_TOOL_NAMES:
        return None
    if successful_writes >= CREATION_TURN_MAX_SUCCESSFUL_WRITES:
        return {
            "tool": tool_name,
            "status": "denied",
            "detail": (
                "本条用户消息已经成功写入一次；本轮不得继续确认、生成或修改其他资料。"
                "请结束回复并等待作者的下一条消息。"
            ),
            "data": {
                "reason": "successful_write_limit",
                "successful_writes": successful_writes,
                "write_limit": CREATION_TURN_MAX_SUCCESSFUL_WRITES,
            },
        }
    if failed_writes >= CREATION_TURN_MAX_FAILED_WRITES:
        return {
            "tool": tool_name,
            "status": "denied",
            "detail": (
                f"本轮写入已失败 {failed_writes} 次；为避免自动重试循环，"
                "本轮写工具已经关闭。请说明错误并等待作者的下一条消息。"
            ),
            "data": {
                "reason": "failed_write_limit",
                "failed_writes": failed_writes,
                "failed_write_limit": CREATION_TURN_MAX_FAILED_WRITES,
            },
        }
    return None


def creation_turn_writes_closed(*, successful_writes: int, failed_writes: int) -> bool:
    return (
        successful_writes >= CREATION_TURN_MAX_SUCCESSFUL_WRITES
        or failed_writes >= CREATION_TURN_MAX_FAILED_WRITES
    )

# A local Agent CLI is already the model producing the requested content. Its
# process-scoped MCP therefore contains only direct reads and writes. Exposing
# any model-spawning tool here would create an outer-CLI -> MCP -> inner-CLI
# recursion and leave the outer request waiting on another copy of itself.
CREATION_DIRECT_MCP_TOOL_NAMES = (
    CREATION_AGENT_TOOL_NAMES - CREATION_MODEL_SPAWNING_TOOL_NAMES
)


__all__ = [
    "CREATION_AGENT_REVISION_TOOL_NAMES",
    "CREATION_AGENT_TOOL_NAMES",
    "CREATION_AGENT_WRITE_TOOL_NAMES",
    "CREATION_DIRECT_MCP_TOOL_NAMES",
    "CREATION_MODEL_SPAWNING_TOOL_NAMES",
    "CREATION_TURN_MAX_FAILED_WRITES",
    "CREATION_TURN_MAX_SUCCESSFUL_WRITES",
    "CREATION_WRITE_SUCCESS_STATUSES",
    "MOBILE_CREATION_AGENT_TOOL_NAMES",
    "MOBILE_CREATION_UNSUPPORTED_TOOL_NAMES",
    "creation_turn_write_denial",
    "creation_turn_writes_closed",
]
