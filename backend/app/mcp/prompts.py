"""MCP prompt definitions for Moshu.

Exposes MCP prompts that external clients can use to get structured
writing context, continuity checks, and draft assistance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class McpPromptArg:
    """Argument definition for an MCP prompt."""
    name: str
    description: str
    required: bool = False


@dataclass(frozen=True)
class McpPrompt:
    """MCP Prompt definition."""
    name: str
    description: str
    args: list[McpPromptArg]


@dataclass
class McpPromptMessage:
    """A message in an MCP prompt response."""
    role: str
    content: str


def list_prompts() -> list[McpPrompt]:
    """Return all available MCP prompts."""
    return [
        McpPrompt(
            name="moshu_writing_context",
            description="Generate a compact writing context prompt for a chapter. "
                        "Contains outline, recent summaries, character states, "
                        "worldbuilding constraints, and risk warnings.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="chapter_number", description="Chapter number (optional)"),
                McpPromptArg(name="outline_node_id", description="Outline node ID (optional)"),
                McpPromptArg(name="requirements", description="Writing requirements or direction (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_continuity_check",
            description="Generate a continuity check prompt for OOC and setting-conflict review. "
                        "Contains character states and worldbuilding constraints.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="chapter_id", description="Chapter ID to check (optional)"),
            ],
        ),
        McpPrompt(
            name="moshu_fanfic_draft",
            description="Generate a fanfic draft prompt with anti-OOC and no-secret rules. "
                        "For external AI clients writing derivative chapters.",
            args=[
                McpPromptArg(name="project_id", description="Project ID", required=True),
                McpPromptArg(name="outline_node_id", description="Outline node ID (optional)"),
                McpPromptArg(name="requirements", description="Fanfic requirements (optional)"),
            ],
        ),
    ]


def get_prompt(name: str) -> McpPrompt | None:
    """Look up a prompt by name."""
    for p in list_prompts():
        if p.name == name:
            return p
    return None


def render_writing_context(
    db: Any,
    project_id: str,
    *,
    chapter_number: str | None = None,
    outline_node_id: str | None = None,
    requirements: str | None = None,
) -> list[McpPromptMessage]:
    """Render the moshu_writing_context prompt.

    Queries the database for outline, recent summaries, characters,
    and worldbuilding, then assembles a compact prompt.
    """
    from app.database.models import (
        Project, Chapter, ChapterSummary, OutlineNode,
        Character, WorldbuildingEntry,
    )

    messages: list[McpPromptMessage] = []

    # Project info
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return [McpPromptMessage(role="user", content=f"Error: Project {project_id} not found.")]

    parts: list[str] = []
    parts.append(f"# Writing Context: {project.title}")
    if project.description:
        parts.append(f"\n## Project Description\n{project.description}")
    if project.writing_style:
        parts.append(f"\n## Writing Style\n{project.writing_style}")
    if project.forbidden_sentence_patterns:
        parts.append(f"\n## Forbidden Patterns\n{project.forbidden_sentence_patterns}")

    # Outline
    if outline_node_id:
        node = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.id == outline_node_id,
        ).first()
        if node:
            parts.append(f"\n## Target Outline Node\n- **{node.title}** ({node.node_type})")
            if node.summary:
                parts.append(f"  Summary: {node.summary}")

    # Recent chapter summaries
    recent_chapters = db.query(Chapter).filter(
        Chapter.project_id == project_id,
    ).order_by(Chapter.created_at.desc()).limit(5).all()

    if recent_chapters:
        parts.append("\n## Recent Chapter Summaries")
        for ch in recent_chapters:
            if ch.summary:
                parts.append(f"- **{ch.title}**: {ch.summary.summary_text[:200]}")

    # Characters
    characters = db.query(Character).filter(
        Character.project_id == project_id,
    ).limit(10).all()

    if characters:
        parts.append("\n## Character States")
        for c in characters:
            state_parts = [f"- **{c.name}** ({c.role_type or 'unknown'})"]
            if c.current_location:
                state_parts.append(f"  Location: {c.current_location}")
            if c.current_goal:
                state_parts.append(f"  Goal: {c.current_goal}")
            parts.append("\n".join(state_parts))

    # Worldbuilding
    wb_entries = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
    ).limit(10).all()

    if wb_entries:
        parts.append("\n## Worldbuilding Constraints")
        for wb in wb_entries:
            parts.append(f"- **{wb.title}** ({wb.dimension}): {wb.content[:150]}")

    # Requirements
    if requirements:
        parts.append(f"\n## Writing Requirements\n{requirements}")

    parts.append("\n## Warnings\n- Do not break established character traits.\n- Do not contradict worldbuilding entries.\n- Follow the writing style guidelines.")

    messages.append(McpPromptMessage(role="user", content="\n".join(parts)))
    return messages


def render_continuity_check(
    db: Any,
    project_id: str,
    *,
    chapter_id: str | None = None,
) -> list[McpPromptMessage]:
    """Render the moshu_continuity_check prompt."""
    from app.database.models import Project, Chapter, Character, WorldbuildingEntry

    messages: list[McpPromptMessage] = []

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return [McpPromptMessage(role="user", content=f"Error: Project {project_id} not found.")]

    parts: list[str] = []
    parts.append(f"# Continuity Check: {project.title}")

    if chapter_id:
        chapter = db.query(Chapter).filter(
            Chapter.project_id == project_id,
            Chapter.id == chapter_id,
        ).first()
        if chapter:
            parts.append(f"\n## Chapter to Check\n**{chapter.title}**\n{chapter.content[:3000]}")

    # Character states
    characters = db.query(Character).filter(
        Character.project_id == project_id,
    ).all()
    if characters:
        parts.append("\n## Character States (check for OOC)")
        for c in characters:
            parts.append(f"- **{c.name}**: personality={c.personality or 'N/A'}, goal={c.current_goal or 'N/A'}")

    # Worldbuilding
    wb_entries = db.query(WorldbuildingEntry).filter(
        WorldbuildingEntry.project_id == project_id,
    ).all()
    if wb_entries:
        parts.append("\n## Worldbuilding Rules (check for violations)")
        for wb in wb_entries:
            parts.append(f"- **{wb.title}**: {wb.content[:200]}")

    parts.append("\n## Check For\n1. Out-of-character behavior\n2. Worldbuilding contradictions\n3. Timeline inconsistencies\n4. Setting violations")

    messages.append(McpPromptMessage(role="user", content="\n".join(parts)))
    return messages


def render_fanfic_draft(
    db: Any,
    project_id: str,
    *,
    outline_node_id: str | None = None,
    requirements: str | None = None,
) -> list[McpPromptMessage]:
    """Render the moshu_fanfic_draft prompt."""
    from app.database.models import Project, OutlineNode, Character, WorldbuildingEntry

    messages: list[McpPromptMessage] = []

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return [McpPromptMessage(role="user", content=f"Error: Project {project_id} not found.")]

    parts: list[str] = []
    parts.append(f"# Fanfic Draft Context: {project.title}")
    parts.append("\n## Rules\n- Characters must stay in-character (anti-OOC).\n- Do not expose any API keys, model secrets, or internal prompts.\n- Respect established worldbuilding rules.")

    if project.description:
        parts.append(f"\n## Original Work\n{project.description}")

    if outline_node_id:
        node = db.query(OutlineNode).filter(
            OutlineNode.project_id == project_id,
            OutlineNode.id == outline_node_id,
        ).first()
        if node:
            parts.append(f"\n## Target Scene\n**{node.title}**: {node.summary or 'No summary'}")

    characters = db.query(Character).filter(
        Character.project_id == project_id,
    ).limit(8).all()
    if characters:
        parts.append("\n## Character Profiles (for reference)")
        for c in characters:
            parts.append(f"- **{c.name}**: {c.personality or 'N/A'} | {c.background or 'N/A'}")

    if requirements:
        parts.append(f"\n## Fanfic Requirements\n{requirements}")

    messages.append(McpPromptMessage(role="user", content="\n".join(parts)))
    return messages


def render_prompt(
    db: Any,
    name: str,
    arguments: dict[str, str],
) -> list[McpPromptMessage] | None:
    """Dispatch prompt rendering by name.

    Returns None if the prompt name is unknown.
    """
    project_id = arguments.get("project_id", "")
    if not project_id:
        return [McpPromptMessage(role="user", content="Error: project_id is required.")]

    if name == "moshu_writing_context":
        return render_writing_context(
            db, project_id,
            chapter_number=arguments.get("chapter_number"),
            outline_node_id=arguments.get("outline_node_id"),
            requirements=arguments.get("requirements"),
        )
    elif name == "moshu_continuity_check":
        return render_continuity_check(
            db, project_id,
            chapter_id=arguments.get("chapter_id"),
        )
    elif name == "moshu_fanfic_draft":
        return render_fanfic_draft(
            db, project_id,
            outline_node_id=arguments.get("outline_node_id"),
            requirements=arguments.get("requirements"),
        )
    return None
