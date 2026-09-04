"""Stable contracts shared by Siming 3.x modules."""

from .context_runtime import (
    ActiveContextManifest,
    activate_context_manifest,
    active_context_manifest,
)
from .contracts import (
    AttentionRequired,
    ModelEvent,
    ModelMessage,
    ModelRequest,
    ModelResult,
    OperationResult,
)
from .tool_result_policy import (
    ModelResultContract,
    ModelResultListProjection,
    ModelResultPolicy,
    ModelResultPreview,
)
from .tool_spec import ToolSpec
from .uow import SqlAlchemyUnitOfWork, UnitOfWork

__all__ = [
    "AttentionRequired",
    "ActiveContextManifest",
    "ModelEvent",
    "ModelMessage",
    "ModelRequest",
    "ModelResultContract",
    "ModelResultListProjection",
    "ModelResultPolicy",
    "ModelResultPreview",
    "ModelResult",
    "OperationResult",
    "SqlAlchemyUnitOfWork",
    "ToolSpec",
    "UnitOfWork",
    "activate_context_manifest",
    "active_context_manifest",
]
