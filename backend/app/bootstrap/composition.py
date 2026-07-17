"""Explicit dependency composition for legacy and 3.x modules."""
from __future__ import annotations


def configure_application_services() -> None:
    """Connect application ports to infrastructure implementations."""
    from ..modules.story.application.content_sync import (
        configure_content_sync_runtime,
    )
    from ..modules.story.infrastructure.content_sync import (
        SqlAlchemyContentSyncOutbox,
        SqlAlchemyContentSyncRuntime,
        configure_content_sync_events,
    )
    from ..modules.story.interfaces.dependencies import configure_story_dependencies
    from ..services.scheduler.ports import configure_task_runner
    from ..services.skills.tool_catalog import configure_tool_catalog
    from ..services.workspace.registry import registry
    from ..services.workspace.scheduled_task_runner import (
        run_workspace_scheduled_task,
    )

    configure_task_runner(run_workspace_scheduled_task)
    configure_tool_catalog(registry.list_for_frontend)
    configure_story_dependencies(SqlAlchemyContentSyncOutbox)
    configure_content_sync_runtime(SqlAlchemyContentSyncRuntime())
    configure_content_sync_events()


__all__ = ["configure_application_services"]
