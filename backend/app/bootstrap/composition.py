"""Explicit dependency composition for legacy and 3.x modules."""

from __future__ import annotations


def _configure_cataloging_queries() -> None:
    from ..modules.continuity.infrastructure.cataloging_queries import (
        SqlAlchemyCatalogingQueries,
    )
    from ..modules.continuity.interfaces.cataloging_dependencies import (
        configure_cataloging_queries,
    )

    configure_cataloging_queries(SqlAlchemyCatalogingQueries)


def _configure_model_config_crud() -> None:
    from ..modules.model_runtime.infrastructure.config_crud import (
        SqlAlchemyModelConfigCrud,
    )
    from ..modules.model_runtime.interfaces.config_dependencies import (
        configure_model_config_crud,
    )

    configure_model_config_crud(SqlAlchemyModelConfigCrud)


def _configure_local_model_store() -> None:
    from ..modules.model_runtime.infrastructure.local_model_store import (
        SqlAlchemyLocalModelStore,
    )
    from ..modules.model_runtime.interfaces.local_model_dependencies import (
        configure_local_model_store,
    )

    configure_local_model_store(SqlAlchemyLocalModelStore)


def _configure_novel_creation_session_store() -> None:
    from ..modules.creation.infrastructure.session_store import (
        SqlAlchemyNovelCreationSessionStore,
    )
    from ..modules.creation.interfaces.session_dependencies import (
        configure_novel_creation_session_store,
    )

    configure_novel_creation_session_store(SqlAlchemyNovelCreationSessionStore)


def _configure_character_workspace() -> None:
    from ..modules.story.interfaces.character_dependencies import (
        configure_character_workspace,
    )
    from ..services.persistence.character_workspace import SqlAlchemyCharacterWorkspace

    configure_character_workspace(SqlAlchemyCharacterWorkspace)


def _configure_assistant_workspace() -> None:
    from ..modules.assistant.interfaces.workspace_dependencies import (
        configure_assistant_workspace,
    )
    from ..services.persistence.assistant_workspace import SqlAlchemyAssistantWorkspace

    configure_assistant_workspace(SqlAlchemyAssistantWorkspace)


def configure_application_services() -> None:
    """Connect application ports to infrastructure implementations."""
    from ..modules.assistant.application.prompt_compiler import PromptCompiler
    from ..modules.assistant.infrastructure.prompt_files import MarkdownPromptRepository
    from ..modules.assistant.infrastructure.system_conversations import (
        SqlAlchemySystemConversationStore,
    )
    from ..modules.assistant.interfaces.prompts import configure_prompt_compiler
    from ..modules.assistant.interfaces.system_conversation_dependencies import (
        configure_system_conversation_dependencies,
    )
    from ..modules.context.application.governance import configure_context_governance
    from ..modules.context.application.rebuild import configure_context_rebuild_runner
    from ..modules.context.infrastructure.governance import SqlAlchemyContextGovernance
    from ..modules.context.infrastructure.rebuild import ContextRebuildRunner
    from ..modules.continuity.application.governance import (
        configure_narrative_governance_commands,
    )
    from ..modules.continuity.application.prompting import ContinuityPromptService
    from ..modules.continuity.infrastructure.governance import (
        SqlAlchemyNarrativeGovernanceCommands,
    )
    from ..modules.continuity.interfaces.dependencies import (
        configure_continuity_prompt_service,
    )
    from ..modules.creation.application.prompting import NovelCreationPromptService
    from ..modules.creation.interfaces.dependencies import configure_creation_prompt_service
    from ..modules.integrations.application.mcp_servers import (
        configure_mcp_server_configuration,
    )
    from ..modules.integrations.infrastructure.external_agent_settings import (
        SqlAlchemyExternalAgentSettingsStore,
    )
    from ..modules.integrations.infrastructure.mcp_servers import (
        SqlAlchemyMcpServerConfiguration,
    )
    from ..modules.integrations.infrastructure.prompt_packs import (
        SqlAlchemyPromptPackCatalog,
    )
    from ..modules.integrations.interfaces.external_agent_dependencies import (
        configure_external_agent_dependencies,
    )
    from ..modules.integrations.interfaces.prompt_pack_dependencies import (
        configure_prompt_pack_dependencies,
    )
    from ..modules.model_runtime.application.execution import configure_model_executor
    from ..modules.model_runtime.application.getting_started import (
        configure_getting_started_configuration,
    )
    from ..modules.model_runtime.application.runtime import (
        ModelRuntime,
        configure_model_runtime,
    )
    from ..modules.model_runtime.application.verification import configure_model_verification
    from ..modules.model_runtime.infrastructure.configuration import (
        SqlAlchemyModelConfiguration,
    )
    from ..modules.model_runtime.infrastructure.execution import GatewayModelExecutor
    from ..modules.model_runtime.infrastructure.getting_started import (
        SqlAlchemyGettingStartedConfiguration,
    )
    from ..modules.model_runtime.infrastructure.verification import ProviderModelVerification
    from ..modules.operations.application.reporting import configure_checkpoint_reporter
    from ..modules.operations.application.scheduled_tasks import configure_scheduled_tasks
    from ..modules.operations.infrastructure.reporting import report_checkpoint
    from ..modules.operations.infrastructure.scheduled_tasks import SqlAlchemyScheduledTasks
    from ..modules.operations.infrastructure.service import SqlAlchemyOperationService
    from ..modules.operations.interfaces.dependencies import configure_operation_service
    from ..modules.story.application.content_sync import (
        configure_content_sync_runtime,
    )
    from ..modules.story.infrastructure.chapters import SqlAlchemyChapterWorkspace
    from ..modules.story.infrastructure.content_sync import (
        SqlAlchemyContentSyncOutbox,
        SqlAlchemyContentSyncRuntime,
        configure_content_sync_events,
    )
    from ..modules.story.infrastructure.deconstruct import (
        SqlAlchemyDeconstructionReader,
    )
    from ..modules.story.infrastructure.outline import SqlAlchemyOutlineWorkspace
    from ..modules.story.infrastructure.projects import SqlAlchemyProjectWorkspace
    from ..modules.story.infrastructure.statistics import SqlAlchemyStoryStatistics
    from ..modules.story.infrastructure.worldbuilding import (
        SqlAlchemyWorldbuildingWorkspace,
    )
    from ..modules.story.interfaces.chapter_dependencies import configure_chapter_dependencies
    from ..modules.story.interfaces.deconstruct_dependencies import (
        configure_deconstruction_dependencies,
    )
    from ..modules.story.interfaces.dependencies import configure_story_dependencies
    from ..modules.story.interfaces.outline_dependencies import configure_outline_dependencies
    from ..modules.story.interfaces.project_dependencies import configure_project_dependencies
    from ..modules.story.interfaces.statistics_dependencies import (
        configure_statistics_dependencies,
    )
    from ..modules.story.interfaces.worldbuilding_dependencies import (
        configure_worldbuilding_dependencies,
    )
    from ..services.context_orchestrator import ContextOrchestrator, reindex_project
    from ..services.scheduler.ports import configure_task_runner
    from ..services.skills.tool_catalog import configure_tool_catalog
    from ..services.workspace.registry import registry
    from ..services.workspace.scheduled_task_runner import (
        run_workspace_scheduled_task,
    )

    prompt_compiler = PromptCompiler(
        MarkdownPromptRepository(),
        known_tools=registry.all_names(),
    )
    configure_prompt_compiler(prompt_compiler)
    configure_system_conversation_dependencies(SqlAlchemySystemConversationStore)
    configure_creation_prompt_service(NovelCreationPromptService(prompt_compiler))
    configure_prompt_pack_dependencies(SqlAlchemyPromptPackCatalog)
    configure_external_agent_dependencies(SqlAlchemyExternalAgentSettingsStore)
    configure_mcp_server_configuration(SqlAlchemyMcpServerConfiguration())
    configure_continuity_prompt_service(ContinuityPromptService(prompt_compiler))
    _configure_cataloging_queries()
    configure_narrative_governance_commands(SqlAlchemyNarrativeGovernanceCommands())
    configure_task_runner(run_workspace_scheduled_task)
    configure_tool_catalog(registry.list_for_frontend)
    configure_model_runtime(ModelRuntime(SqlAlchemyModelConfiguration()))
    _configure_model_config_crud()
    _configure_local_model_store()
    _configure_novel_creation_session_store()
    _configure_character_workspace()
    _configure_assistant_workspace()
    configure_model_executor(GatewayModelExecutor())
    configure_getting_started_configuration(SqlAlchemyGettingStartedConfiguration())
    configure_model_verification(ProviderModelVerification())
    configure_operation_service(SqlAlchemyOperationService())
    configure_checkpoint_reporter(report_checkpoint)
    configure_scheduled_tasks(SqlAlchemyScheduledTasks())
    configure_context_rebuild_runner(
        ContextRebuildRunner(
            orchestrator_factory=ContextOrchestrator,
            lexical_reindexer=reindex_project,
        ).run
    )
    configure_context_governance(SqlAlchemyContextGovernance())
    configure_story_dependencies(SqlAlchemyContentSyncOutbox)
    configure_chapter_dependencies(SqlAlchemyChapterWorkspace)
    configure_deconstruction_dependencies(SqlAlchemyDeconstructionReader)
    configure_outline_dependencies(SqlAlchemyOutlineWorkspace)
    configure_project_dependencies(SqlAlchemyProjectWorkspace)
    configure_statistics_dependencies(SqlAlchemyStoryStatistics)
    configure_worldbuilding_dependencies(SqlAlchemyWorldbuildingWorkspace)
    configure_content_sync_runtime(SqlAlchemyContentSyncRuntime())
    configure_content_sync_events()


__all__ = ["configure_application_services"]
