"""Declarative contracts for model-visible workspace tool results.

The complete tool result belongs to the run/audit record.  These contracts only
describe the projection that may be delivered to a model.  Keeping the policy
on the tool specification prevents individual agent loops from inventing their
own field-name or character based truncation rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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
class ModelResultObjectProjection:
    """Select metadata from a nested object without copying its document body.

    A scalar variant is retained only when also declared in ``data_fields``.
    """

    source_field: str
    fields: tuple[str, ...]


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
class ModelResultPageBudget:
    """A page-size-dependent ceiling, enforced again on the complete projection."""

    base_json_bytes: int
    item_json_bytes: int
    max_items: int
    argument: str = "limit"
    text_argument: str | None = None
    default_text_chars: int = 0
    max_text_chars: int = 0
    text_fields_per_item: int = 0
    min_text_fields: int = 0
    default_items: int | None = None

    def bytes_for(self, arguments: Mapping[str, Any]) -> int:
        value = arguments.get(self.argument)
        # Match the handler's declared default and clamping; never infer intent.
        count = (
            (self.default_items or self.max_items)
            if not value else max(1, min(int(value), self.max_items))
        )
        size = self.base_json_bytes + count * self.item_json_bytes
        if self.text_argument:
            chars = max(1, min(
                int(arguments.get(self.text_argument) or self.default_text_chars),
                self.max_text_chars,
            ))
            # A JSON string needs at most six UTF-8 bytes per Unicode scalar.
            size += 6 * chars * max(self.min_text_fields, count * self.text_fields_per_item)
        return size

    @property
    def maximum_bytes(self) -> int:
        arguments = {self.argument: self.max_items}
        if self.text_argument:
            arguments[self.text_argument] = self.max_text_chars
        return self.bytes_for(arguments)


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
    object_projections: tuple[ModelResultObjectProjection, ...] = ()
    reference_fields: tuple[str, ...] = ()
    preview: ModelResultPreview | None = None
    # The author receives the complete editor artifact; a model only needs its
    # durable receipt. Both field lists remain declared on this one contract.
    author_data_fields: tuple[str, ...] = ()
    author_preview: ModelResultPreview | None = None
    page_budget: ModelResultPageBudget | None = None

    def bytes_for_arguments(self, arguments: Mapping[str, Any] | None = None) -> int:
        if self.page_budget is None:
            return self.max_json_bytes
        return self.page_budget.bytes_for(arguments or {})

    def __post_init__(self) -> None:
        if self.max_json_bytes <= 0:
            raise ValueError("max_json_bytes must be positive")
        if self.page_budget is not None:
            page = self.page_budget
            if min(page.base_json_bytes, page.item_json_bytes, page.max_items) <= 0:
                raise ValueError("page budget bounds must be positive")
            if page.default_items is not None and not 1 <= page.default_items <= page.max_items:
                raise ValueError("page budget default_items must fit max_items")
            if page.text_argument and not (
                0 < page.default_text_chars <= page.max_text_chars and page.text_fields_per_item > 0
            ):
                raise ValueError("text page budget bounds must be positive and ordered")
            if page.maximum_bytes != self.max_json_bytes:
                raise ValueError("page budget maximum must equal the declared result ceiling")
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
    "ModelResultObjectProjection",
    "ModelResultPageBudget",
    "ModelResultPolicy",
    "ModelResultPreview",
]
