"""Application contract for context profiles, manifests and rebuild administration."""

from __future__ import annotations

from typing import Any, Protocol


class ContextGovernancePort(Protocol):
    def list_profiles(self, session: Any) -> dict: ...

    def save_profile(self, session: Any, values: dict) -> dict: ...

    def list_rebuilds(self, session: Any, limit: int) -> list[dict]: ...

    def create_rebuild(self, session: Any, values: dict) -> dict: ...

    def retry_rebuild(self, session: Any, job_id: str) -> dict | None: ...

    def list_manifests(self, session: Any, project_id: str, limit: int) -> list[dict]: ...


_governance: ContextGovernancePort | None = None


def configure_context_governance(governance: ContextGovernancePort) -> None:
    global _governance
    _governance = governance


def get_context_governance() -> ContextGovernancePort:
    if _governance is None:
        raise RuntimeError("Context governance has not been configured")
    return _governance


__all__ = ["ContextGovernancePort", "configure_context_governance", "get_context_governance"]
