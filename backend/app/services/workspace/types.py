"""Shared types for workspace assistant tool execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from ...database.models import Chapter, Project


@dataclass(frozen=True)
class WorkspaceActionDependencies:
    get_project: Callable[[Session, str], Project]
    detect_forbidden_sentence_violations: Callable[[str, Project], list[dict]]
    repair_forbidden_sentence_text: Callable[
        [str, Project, Optional[str], Optional[int]],
        Awaitable[tuple[str, list[dict], list[dict]]],
    ]
    finalize_assistant_chapter: Callable[[Session, Chapter, str, str, str, list[str], Optional[str]], Chapter]
    create_assistant_chapter: Callable[
        [Session, str, str, str, Optional[str], str, list[str], Optional[str]],
        Optional[Chapter],
    ]


ToolHandler = Callable[[Session, str, dict, WorkspaceActionDependencies], Awaitable[dict]]

