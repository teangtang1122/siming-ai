"""Canonical JSON helpers used by Python and Android golden contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


def canonical_value(value: Any) -> Any:
    """Return a JSON-safe value without dropping nulls or empty collections.

    Cross-platform hashes use UTF-8 JSON, lexicographically sorted object keys,
    no insignificant whitespace, and unescaped Unicode.  Dataclass field names
    are part of the wire contract, so serializers must not apply aliases.
    """

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["canonical_json", "canonical_sha256", "canonical_value", "text_sha256"]
