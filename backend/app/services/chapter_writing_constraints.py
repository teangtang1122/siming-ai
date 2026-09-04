"""Structured, deterministic chapter-writing constraint enforcement."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.utils import count_han_characters


MINIMUM_HAN_CHARACTERS_FIELD = "minimum_han_characters"
MAXIMUM_SUPPORTED_MINIMUM_HAN_CHARACTERS = 100_000


def normalize_minimum_han_characters(value: Any) -> int | None:
    """Validate a model-structured hard minimum without parsing user prose."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("minimum_han_characters must be an integer")
    try:
        minimum = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("minimum_han_characters must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("minimum_han_characters must be an integer")
    if minimum < 1 or minimum > MAXIMUM_SUPPORTED_MINIMUM_HAN_CHARACTERS:
        raise ValueError(
            "minimum_han_characters must be between 1 and "
            f"{MAXIMUM_SUPPORTED_MINIMUM_HAN_CHARACTERS}"
        )
    return minimum


def normalize_writing_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return arguments with the optional hard minimum in canonical form."""
    normalized = dict(arguments)
    minimum = normalize_minimum_han_characters(
        normalized.get(MINIMUM_HAN_CHARACTERS_FIELD)
    )
    if minimum is None:
        normalized.pop(MINIMUM_HAN_CHARACTERS_FIELD, None)
    else:
        normalized[MINIMUM_HAN_CHARACTERS_FIELD] = minimum
    return normalized


def manifest_minimum_han_characters(manifest: Any) -> int | None:
    """Read the immutable structured minimum recorded on a context manifest."""
    query = manifest.query_json if isinstance(getattr(manifest, "query_json", None), dict) else {}
    arguments = query.get("arguments") if isinstance(query.get("arguments"), dict) else {}
    return normalize_minimum_han_characters(
        arguments.get(MINIMUM_HAN_CHARACTERS_FIELD)
    )


def recommended_han_character_target(minimum: int) -> int:
    """Return a retry target with enough margin to avoid tiny failed rewrites."""

    normalized = normalize_minimum_han_characters(minimum)
    if normalized is None:  # pragma: no cover - guarded by the required argument
        raise ValueError("minimum_han_characters is required")
    buffer = max(10, min(400, (normalized + 9) // 10))
    return normalized + buffer


@dataclass(frozen=True, slots=True)
class ChapterLengthCheck:
    actual_han_characters: int
    minimum_han_characters: int | None

    @property
    def accepted(self) -> bool:
        return (
            self.minimum_han_characters is None
            or self.actual_han_characters >= self.minimum_han_characters
        )


def check_chapter_length(content: str, manifest: Any) -> ChapterLengthCheck:
    return ChapterLengthCheck(
        actual_han_characters=count_han_characters(content),
        minimum_han_characters=manifest_minimum_han_characters(manifest),
    )
