"""Declarative contracts for model-visible workspace tool results.

The complete tool result belongs to the run/audit record.  These contracts only
describe the projection that may be delivered to a model.  Keeping the policy
on the tool specification prevents individual agent loops from inventing their
own field-name or character based truncation rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ModelResultPolicy(StrEnum):
    """Supported projections for a tool result delivered to a model."""

    INLINE_BOUNDED = "inline_bounded"
    SUMMARY_AND_IDS = "summary_and_ids"
    ARTIFACT_REFERENCE = "artifact_reference"
    STATUS_ONLY = "status_only"


@dataclass(frozen=True)
class ModelResultListProjection:
    """Explicit projection for a list stored under ``result.data``."""

    source_field: str | None
    output_field: str | None
    item_fields: tuple[str, ...]
    max_items: int | None = None


@dataclass(frozen=True)
class ModelResultPreview:
    """Explicit preview of one persisted artifact field.

    String previews have a declared character boundary.  List previews keep
    only declared item fields and reject an unexpected item count instead of
    silently dropping entries.
    """

    source_field: str
    output_field: str
    max_chars: int | None = None
    item_fields: tuple[str, ...] = ()
    max_items: int | None = None


@dataclass(frozen=True)
class ModelResultContract:
    """Authoritative model-visible result contract for one workspace tool."""

    policy: ModelResultPolicy = ModelResultPolicy.INLINE_BOUNDED
    # This is a hard upper bound for one native model-visible result, not an
    # HTTP transport limit.  The executor also sums these declarations before
    # running a multi-call batch.  Large business payloads must therefore use
    # explicit pages/ranges or a durable artifact reference.
    max_json_bytes: int = 16 * 1024
    result_fields: tuple[str, ...] = ()
    data_fields: tuple[str, ...] = ()
    list_projections: tuple[ModelResultListProjection, ...] = ()
    reference_fields: tuple[str, ...] = ()
    preview: ModelResultPreview | None = None

    def __post_init__(self) -> None:
        if self.max_json_bytes <= 0:
            raise ValueError("max_json_bytes must be positive")
        if self.policy is ModelResultPolicy.ARTIFACT_REFERENCE:
            if not self.reference_fields:
                raise ValueError("artifact_reference requires reference_fields")
            if self.preview is None:
                raise ValueError("artifact_reference requires a preview contract")


DEFAULT_MODEL_RESULT_CONTRACT = ModelResultContract()


__all__ = [
    "DEFAULT_MODEL_RESULT_CONTRACT",
    "ModelResultContract",
    "ModelResultListProjection",
    "ModelResultPolicy",
    "ModelResultPreview",
]
