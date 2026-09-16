"""Portable preconditions for applying a phone's already completed plan."""

MOBILE_CATALOGING_GUARD_FIELDS = {
    "character": ["name", "current_version", "ai_config"],
    "world_entry": ["title", "dimension", "content", "status"],
    "outline_node": ["node_type", "parent_id", "title", "summary", "sort_order"],
    "character_relationship": ["from", "to", "relationship_type", "description"],
    "foreshadowing": ["title", "status", "dedupe_key", "evidence"],
    "narrative_debt": ["title", "status", "dedupe_key", "evidence"],
    "causal_edge": ["cause", "effect", "status", "dedupe_key"],
}
