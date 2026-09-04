"""Static public-safe contracts for identities exposed outside durable ledgers.

Run-step output references and checkpoint ledgers are durable audit inputs.
Older databases may therefore contain values written before the current
server-authored contracts existed.  Public serializers must recognize both
the resource type and the exact identifier grammar instead of reflecting
arbitrary bounded strings from those blobs.
"""

from __future__ import annotations

import uuid
from types import MappingProxyType
from typing import Any, Final, Literal

ResourceIdGrammar = Literal["uuid"]

# Native writers emit canonical UUIDs for every type below. Gateway sync can
# preserve older bounded non-UUID IDs for character/outline/worldbuilding
# domain rows; those rare identities remain usable internally but are omitted
# from public receipts because their broad grammar can also carry paths or
# credential-shaped text. Keep this public-safe subset explicit: adding a new
# output-reference type requires proving its publishable ID grammar here first.
PUBLIC_RESOURCE_REFERENCE_ID_GRAMMARS: Final = MappingProxyType({
    "cataloging_job": "uuid",
    "chapter_draft": "uuid",
    "character": "uuid",
    "creation_entity": "uuid",
    "creation_import": "uuid",
    "creation_session": "uuid",
    "deconstruct_report": "uuid",
    "outline": "uuid",
    "outline_draft": "uuid",
    "project": "uuid",
    "scheduled_task": "uuid",
    "worldbuilding": "uuid",
})
PUBLIC_RESOURCE_REFERENCE_TYPES: Final = frozenset(PUBLIC_RESOURCE_REFERENCE_ID_GRAMMARS)


def public_resource_identity(resource_type: Any, resource_id: Any) -> tuple[str, str] | None:
    """Return an allowlisted canonical resource identity or fail closed."""

    if not isinstance(resource_type, str) or not isinstance(resource_id, str):
        return None
    if PUBLIC_RESOURCE_REFERENCE_ID_GRAMMARS.get(resource_type) != "uuid":
        return None
    try:
        parsed = uuid.UUID(resource_id)
    except (AttributeError, ValueError):
        return None
    # ``UUID`` accepts braces, upper case and compact forms.  Production IDs
    # use the canonical lower-case 36-character representation only.
    if str(parsed) != resource_id:
        return None
    return resource_type, resource_id


def public_resource_reference(
    resource_type: Any,
    resource_id: Any,
    revision: Any = None,
) -> dict[str, Any] | None:
    """Project one public-safe identity and its optional integer revision."""

    identity = public_resource_identity(resource_type, resource_id)
    if identity is None:
        return None
    safe_type, safe_id = identity
    result: dict[str, Any] = {"type": safe_type, "id": safe_id}
    if type(revision) is int and revision >= 0:
        result["revision"] = revision
    return result


__all__ = [
    "PUBLIC_RESOURCE_REFERENCE_ID_GRAMMARS",
    "PUBLIC_RESOURCE_REFERENCE_TYPES",
    "public_resource_identity",
    "public_resource_reference",
]
