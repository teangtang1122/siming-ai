"""Validate locked JSON Pointer values before replacing a generated artifact."""
from __future__ import annotations

from typing import Any

from .generation_errors import CreationGenerationError


def validate_artifact_locks(
    stage: str, data: dict[str, Any], baseline: dict[str, Any], paths: list[str]
) -> None:
    missing = object()

    def read(document: Any, path: str) -> Any:
        parts = [] if path in {"", "/"} else path.lstrip("/").split("/")
        try:
            for part in parts:
                key = part.replace("~1", "/").replace("~0", "~")
                document = document[int(key)] if isinstance(document, list) else document[key]
        except (KeyError, ValueError, IndexError, TypeError):
            return missing
        return document

    reason = (
        "creation_opening_locked_changed"
        if stage == "opening_outline"
        else "creation_artifact_locked_changed"
    )
    for path in paths:
        if read(data, path) != read(baseline, path):
            raise CreationGenerationError(reason, path)
