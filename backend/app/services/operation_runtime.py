"""Compatibility exports for the 3.0 operation runtime."""

from ..modules.operations.application.context import (
    activate_operation,
    current_operation_id,
    iterate_with_operation,
)
from ..modules.operations.infrastructure.runtime import (
    add_operation_event,
    ensure_operation,
    fail_operation,
    finish_operation,
    heartbeat_loop,
    heartbeat_operation,
    input_snapshot_hash,
    invoke_operation_action,
    mark_interrupted_operations,
    record_operation_signal,
    register_operation_actions,
    serialize_operation,
    stall_seconds_from_env,
    unregister_operation_actions,
    update_operation,
    utcnow,
)

__all__ = [
    "activate_operation",
    "add_operation_event",
    "current_operation_id",
    "ensure_operation",
    "fail_operation",
    "finish_operation",
    "heartbeat_loop",
    "heartbeat_operation",
    "input_snapshot_hash",
    "invoke_operation_action",
    "iterate_with_operation",
    "mark_interrupted_operations",
    "record_operation_signal",
    "register_operation_actions",
    "serialize_operation",
    "stall_seconds_from_env",
    "unregister_operation_actions",
    "update_operation",
    "utcnow",
]
