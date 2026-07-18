"""Markdown/YAML PromptSpec repository."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from ..domain.prompt_spec import PromptSpec


def default_prompt_source_dir() -> Path:
    packaged_root = getattr(sys, "_MEIPASS", None)
    if packaged_root:
        return Path(packaged_root) / "prompt_specs"
    return Path(__file__).resolve().parents[4] / "prompt_specs"


def parse_prompt_spec(text: str, *, source_path: str = "") -> PromptSpec:
    if not text.startswith("---"):
        raise ValueError(f"PromptSpec requires YAML front matter: {source_path}")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"PromptSpec front matter is not closed: {source_path}")
    metadata = yaml.safe_load(parts[1]) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"PromptSpec metadata must be an object: {source_path}")
    payload: dict[str, Any] = dict(metadata)
    payload["body"] = parts[2].strip()
    payload["source_path"] = source_path
    return PromptSpec.model_validate(payload)


class MarkdownPromptRepository:
    """Load all PromptSpecs below a stable source directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_prompt_source_dir()

    def load_all(self) -> list[PromptSpec]:
        if not self.root.exists():
            raise FileNotFoundError(f"PromptSpec directory does not exist: {self.root}")
        specs: list[PromptSpec] = []
        for path in sorted(self.root.rglob("*.md")):
            specs.append(
                parse_prompt_spec(
                    path.read_text(encoding="utf-8"),
                    source_path=path.relative_to(self.root).as_posix(),
                )
            )
        return specs


__all__ = ["MarkdownPromptRepository", "default_prompt_source_dir", "parse_prompt_spec"]
