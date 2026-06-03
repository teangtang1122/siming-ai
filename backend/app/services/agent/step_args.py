"""Resolve {step_key.data.field} references in step args using completed step outputs."""
from __future__ import annotations

import re
from typing import Any

_REF_PATTERN = re.compile(r"\{([^}]+)\}")


def _resolve_path(path: str, outputs: dict[str, dict]) -> Any:
    """Resolve a dotted path like 'chapter_writer.data.draft_id' against outputs.

    Supports dict keys and integer list indices.
    """
    parts = path.split(".")
    if not parts:
        return "{" + path + "}"

    step_key = parts[0]
    if step_key not in outputs:
        return "{" + path + "}"

    current: Any = outputs[step_key]
    for part in parts[1:]:
        if isinstance(current, dict):
            if part not in current:
                return "{" + path + "}"
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return "{" + path + "}"
        else:
            return "{" + path + "}"

    return current


def resolve_step_args(args: Any, completed_outputs: dict[str, dict]) -> Any:
    """Recursively resolve {key.path} references in args.

    Supports: str (direct replace), dict (recurse values), list (recurse items).
    """
    if isinstance(args, str):
        # If the entire string is a single reference, return the raw value (preserves type)
        match = _REF_PATTERN.fullmatch(args)
        if match:
            return _resolve_path(match.group(1), completed_outputs)
        # Otherwise do string substitution
        def _replace(m: re.Match) -> str:
            val = _resolve_path(m.group(1), completed_outputs)
            return str(val) if not isinstance(val, str) else val
        return _REF_PATTERN.sub(_replace, args)

    if isinstance(args, dict):
        return {k: resolve_step_args(v, completed_outputs) for k, v in args.items()}

    if isinstance(args, list):
        return [resolve_step_args(item, completed_outputs) for item in args]

    return args
