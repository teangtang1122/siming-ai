"""Explicit dependency composition for legacy and 3.x modules."""

from __future__ import annotations


def configure_application_services() -> None:
    """Connect application ports to infrastructure implementations."""
    from ..modules.context.application.rebuild import configure_context_rebuild_runner
    from ..modules.context.infrastructure.rebuild import ContextRebuildRunner
    from ..modules.model_runtime.application.runtime import (
        ModelRuntime,
        configure_model_runtime,
    )
    from ..modules.model_runtime.infrastructure.configuration import (
        SqlAlchemyModelConfiguration,
    )
    from ..modules.operations.application.reporting import configure_checkpoint_reporter
    from ..modules.operations.infrastructure.reporting import report_checkpoint
    from ..modules.operations.infrastructure.service import SqlAlchemyOperationService
    from ..modules.operations.interfaces.dependencies import configure_operation_service
    from ..modules.story.application.content_sync import (
        configure_content_sync_runtime,
    )
    from ..modules.story.infrastructure.content_sync import (
        SqlAlchemyContentSyncOutbox,
        SqlAlchemyContentSyncRuntime,
        configure_content_sync_events,
    )
    from ..modules.story.interfaces.dependencies import configure_story_dependencies
    from ..services.context_orchestrator import ContextOrchestrator, reindex_project
    from ..services.scheduler.ports import configure_task_runner
    from ..services.skills.tool_catalog import configure_tool_catalog
    from ..services.workspace.registry import registry
    from ..services.workspace.scheduled_task_runner import (
        run_workspace_scheduled_task,
    )

    configure_task_runner(run_workspace_scheduled_task)
    configure_tool_catalog(registry.list_for_frontend)
    configure_model_runtime(ModelRuntime(SqlAlchemyModelConfiguration()))
    configure_operation_service(SqlAlchemyOperationService())
    configure_checkpoint_reporter(report_checkpoint)
    configure_context_rebuild_runner(
        ContextRebuildRunner(
            orchestrator_factory=ContextOrchestrator,
            lexical_reindexer=reindex_project,
        ).run
    )
    configure_story_dependencies(SqlAlchemyContentSyncOutbox)
    configure_content_sync_runtime(SqlAlchemyContentSyncRuntime())
    configure_content_sync_events()


__all__ = ["configure_application_services"]
