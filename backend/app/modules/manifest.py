"""Canonical module ownership map."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModuleDescriptor:
    name: str
    purpose: str
    owns: tuple[str, ...]


MODULES = (
    ModuleDescriptor(
        "story",
        "Authoritative novel content and version history.",
        ("projects", "chapters", "outlines", "characters", "worldbuilding"),
    ),
    ModuleDescriptor(
        "creation",
        "New-novel interviews, staged drafts, and blueprint application.",
        ("novel_creation_sessions", "concepts", "blueprints"),
    ),
    ModuleDescriptor(
        "continuity",
        "Cataloging, narrative ledger, granularity, and checkpoints.",
        ("cataloging", "narrative_governance", "story_granularity"),
    ),
    ModuleDescriptor(
        "assistant",
        "Conversation intent, plans, tools, and author-facing outcomes.",
        ("assistant_conversations", "agent_plans", "workspace_tools"),
    ),
    ModuleDescriptor(
        "operations",
        "Durable task lifecycle, progress, health, and recovery.",
        ("operation_runs", "operation_events", "scheduling"),
    ),
    ModuleDescriptor(
        "model_runtime",
        "Model selection, readiness, API adapters, and local CLI execution.",
        ("api_configs", "llm_gateway", "local_cli", "local_models"),
    ),
    ModuleDescriptor(
        "context",
        "RAG, context budgets, manifests, retrieval, and rendering.",
        ("rag", "context_manifests", "context_rebuild"),
    ),
    ModuleDescriptor(
        "integrations",
        "MCP, external agents, filesystem mirror, import, and export.",
        ("mcp", "external_agents", "content_mirror", "import_export"),
    ),
)
