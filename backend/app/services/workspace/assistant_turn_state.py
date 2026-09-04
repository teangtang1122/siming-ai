"""Mutable state shared by the workspace assistant turn executors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.conversation_context import ToolTransaction
from app.services.workspace.assistant_response import WorkspaceTurnTelemetry

SseEncoder = Callable[[Any], str]
WorkspaceActionExecutor = Callable[..., Awaitable[dict[str, Any]]]
ContextPreparer = Callable[..., Awaitable[Any]]


class WorkspaceTurnSuperseded(RuntimeError):
    """A newer durable user turn has replaced this running request."""


@dataclass
class WorkspaceAssistantTurnState:
    """One request's durable and provider-facing state.

    The turn executors mutate this object instead of relying on one giant
    generator closure.  Database records remain the source of truth; this
    object only coordinates the active request.
    """

    db: Any
    project_id: str
    payload: Any
    selected_provider: str
    supports_function_calling: bool
    local_cli_selected: bool
    local_cli_mcp_enabled: bool
    encode_event: SseEncoder
    execute_action: WorkspaceActionExecutor
    prepare_context: ContextPreparer

    conversation: Any = None
    user_message: Any = None
    assistant_message: Any = None
    assistant_run: Any = None
    workspace: Any = None
    project: Any = None
    project_folder: str = ""
    local_cli_extra_body: dict[str, Any] = field(default_factory=dict)
    tool_category_state_file: str = ""
    reference_context_record: dict[str, Any] | None = None
    reference_context_audit: dict[str, Any] | None = None
    selected_text: str | None = None
    selected_text_chapter_title: str | None = None
    active_chapter_draft: dict[str, Any] | None = None
    authorized_tool_names: set[str] = field(default_factory=set)
    active_categories: tuple[str, ...] = ()
    category_selected: bool = False
    observed_category_version: int = 0
    workspace_tool_names: list[str] = field(default_factory=list)
    workspace_tool_name_set: set[str] = field(default_factory=set)
    workspace_tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    base_system_prompt: str = ""
    tool_transactions: list[ToolTransaction] = field(default_factory=list)
    all_write_tool_names: set[str] = field(default_factory=set)

    tool_logs: list[dict[str, Any]] = field(default_factory=list)
    final_reply: str = ""
    applied_actions: list[dict[str, Any]] = field(default_factory=list)
    searched_context: list[dict[str, Any]] = field(default_factory=list)
    final_model: str = ""
    final_usage: Any = None
    turn_terminal_result: dict[str, Any] | None = None
    turn_telemetry: WorkspaceTurnTelemetry = field(default_factory=WorkspaceTurnTelemetry)
    pending_native_events: list[str] = field(default_factory=list)
    loop_action: str = "next"

    def event(self, payload: Any) -> str:
        return self.encode_event(payload)

    def require_current_run(self) -> None:
        """Fail before provider/tool work when a newer turn superseded this run."""

        if self.assistant_run is None or self.workspace is None:
            return
        self.db.expire_all()
        current = self.workspace.run(self.project_id, str(self.assistant_run.id))
        if current is None or str(current.status or "") != "running":
            raise WorkspaceTurnSuperseded("作品助手任务已被更新的作者消息替换")
        self.assistant_run = current
