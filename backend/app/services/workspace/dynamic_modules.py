"""Workspace tool modules loaded dynamically by the compatibility registry."""

from __future__ import annotations


LEGACY_HANDLER_MODULES = (
    "app.services.workspace.tools.analysis",
    "app.services.workspace.tools.cataloging",
    "app.services.workspace.tools.chapter_writer",
    "app.services.workspace.tools.chapters",
    "app.services.workspace.tools.character_merge",
    "app.services.workspace.tools.character_writer",
    "app.services.workspace.tools.characters",
    "app.services.workspace.tools.context_governance",
    "app.services.workspace.tools.context_preview",
    "app.services.workspace.tools.deconstruct",
    "app.services.workspace.tools.export",
    "app.services.workspace.tools.external_agent",
    "app.services.workspace.tools.external_cataloging",
    "app.services.workspace.tools.external_story_updates",
    "app.services.workspace.tools.external_writing",
    "app.services.workspace.tools.import_tools",
    "app.services.workspace.tools.local_cli_agent",
    "app.services.workspace.tools.mcp_status",
    "app.services.workspace.tools.memory",
    "app.services.workspace.tools.narrative_governance",
    "app.services.workspace.tools.novel_creation",
    "app.services.workspace.tools.novel_creation_v2",
    "app.services.workspace.tools.outline",
    "app.services.workspace.tools.outline_writer",
    "app.services.workspace.tools.plot",
    "app.services.workspace.tools.project_files",
    "app.services.workspace.tools.project_status",
    "app.services.workspace.tools.projects",
    "app.services.workspace.tools.prompt_packs",
    "app.services.workspace.tools.rag_tools",
    "app.services.workspace.tools.relationships",
    "app.services.workspace.tools.roleplay",
    "app.services.workspace.tools.scheduler",
    "app.services.workspace.tools.search",
    "app.services.workspace.tools.skills",
    "app.services.workspace.tools.stats",
    "app.services.workspace.tools.story_granularity",
    "app.services.workspace.tools.text_operations",
    "app.services.workspace.tools.web_search",
    "app.services.workspace.tools.worldbuilding",
    "app.services.workspace.tools.worldbuilding_writer",
)


__all__ = ["LEGACY_HANDLER_MODULES"]
