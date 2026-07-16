"""Explicit dependency composition for legacy and 3.x modules."""
from __future__ import annotations


def configure_application_services() -> None:
    """Connect application ports to infrastructure implementations."""
    from ..services.scheduler.ports import configure_task_runner
    from ..services.skills.tool_catalog import configure_tool_catalog
    from ..services.workspace.registry import registry
    from ..services.workspace.scheduled_task_runner import (
        run_workspace_scheduled_task,
    )

    configure_task_runner(run_workspace_scheduled_task)
    configure_tool_catalog(registry.list_for_frontend)


__all__ = ["configure_application_services"]
