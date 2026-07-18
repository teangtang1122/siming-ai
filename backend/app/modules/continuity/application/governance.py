"""Command boundary for narrative governance persistence."""

from __future__ import annotations

from typing import Any, Protocol


class NarrativeGovernanceCommandPort(Protocol):
    def update_status(
        self,
        session: Any,
        project_id: str,
        item_type: str,
        item_id: str,
        values: dict,
    ) -> bool: ...

_commands: NarrativeGovernanceCommandPort | None = None


def configure_narrative_governance_commands(
    commands: NarrativeGovernanceCommandPort,
) -> None:
    global _commands
    _commands = commands


def get_narrative_governance_commands() -> NarrativeGovernanceCommandPort:
    if _commands is None:
        raise RuntimeError("Narrative governance commands have not been configured")
    return _commands


__all__ = [
    "NarrativeGovernanceCommandPort",
    "configure_narrative_governance_commands",
    "get_narrative_governance_commands",
]
