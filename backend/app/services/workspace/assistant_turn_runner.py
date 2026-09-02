"""Production orchestration for one workspace-assistant SSE turn."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from typing import Any

from app.ai.local_cli_adapter import is_local_cli_provider
from app.ai.local_cli_prompt import supports_direct_mcp
from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    tool_category_controller_schema,
    tool_names_for_categories,
)
from app.architecture.uow import commit_session
from app.core.db_helpers import get_project_or_404
from app.core.exceptions import LLMError, NotFoundError, ValidationError
from app.prompts.workspace_assistant import build_workspace_assistant_runtime_system_prompt
from app.services.agent.prompt_builder import build_system_prompt, get_workspace_pack
from app.services.content_store import ensure_project_folder
from app.services.context_orchestrator import ContextOrchestrator
from app.services.conversation_context import (
    ConversationContextError,
    ConversationContextErrorCode,
    ModelToolCapability,
    ToolTransactionState,
)
from app.services.conversation_context.execution_ledger import (
    execution_source_hashes_from_run_steps,
)
from app.services.tool_category_state import (
    bind_tool_category_turn_guard,
    create_tool_category_state,
    remove_tool_category_state,
)
from app.services.workspace.assistant_direct_mcp_turn import WorkspaceDirectMcpTurn
from app.services.workspace.assistant_native_turn import WorkspaceNativeTurn
from app.services.workspace.assistant_public_errors import (
    PublicAssistantFailure,
    public_context_failure,
    public_model_failure,
    public_server_failure,
)
from app.services.workspace.assistant_response import (
    _assistant_conversation_to_dict,
    _assistant_message_to_dict,
    finalize_workspace_assistant_turn,
)
from app.services.workspace.assistant_stream_runtime import assistant_cancel_was_explicit
from app.services.workspace.assistant_turn_state import (
    ContextPreparer,
    SseEncoder,
    WorkspaceActionExecutor,
    WorkspaceAssistantTurnState,
    WorkspaceTurnSuperseded,
)
from app.services.workspace.assistant_turn_support import (
    assistant_title_from_message,
    load_durable_reference_context,
    reference_context_audit,
    reference_context_record,
    workspace_category_instruction,
)
from app.services.workspace.conversation_context_adapter import (
    build_workspace_context_input,
    workspace_checkpoint_source_turns,
    workspace_execution_ledger_from_run_steps,
    workspace_tool_receipts_from_run_steps,
)
from app.services.workspace.run_log import (
    create_assistant_run,
    mark_assistant_run,
    run_payload,
)
from app.services.workspace.tool_result_projection import (
    max_model_visible_result_tokens_for_open_tool_schemas,
    max_native_tool_transaction_wrapper_tokens,
)
from app.services.workspace.tool_schemas import (
    build_workspace_tool_schemas,
    select_workspace_tool_names,
)
from app.services.workspace.transcript_import import (
    ensure_workspace_transcript_from_system_conversation,
)

_SCOPE = "project"
_TIMEOUT_SECONDS = 300
_FINAL_SYNTHESIS_ATTEMPTS = 2
_FINAL_SYNTHESIS_INSTRUCTION = (
    "业务工具阶段已经结束。禁止继续调用或建议调用任何工具；"
    "只依据本轮已验证的工具结果，直接回答作者最新消息。"
    "必须给出具体结论、依据和仍缺少的信息；不能只声称已分析、已完成或让作者等待。"
)
logger = logging.getLogger(__name__)


class WorkspaceAssistantTurnRunner:
    """Coordinate persistence, context preparation and provider steps."""

    def __init__(
        self,
        *,
        project_id: str,
        payload: Any,
        selected_provider: str,
        gateway: Any,
        registry: Any,
        workspace_factory: Any,
        execute_action: WorkspaceActionExecutor,
        prepare_context: ContextPreparer,
        encode_event: SseEncoder,
    ) -> None:
        self.project_id = project_id
        self.payload = payload
        self.selected_provider = selected_provider
        self.gateway = gateway
        self.registry = registry
        self.workspace_factory = workspace_factory
        self.execute_action = execute_action
        self.prepare_context = prepare_context
        self.encode_event = encode_event

    async def events(self, db: Any) -> AsyncGenerator[str, None]:
        supports_native = self._supports_native_tools()
        local_cli = is_local_cli_provider(self.selected_provider)
        direct_mcp = local_cli and supports_direct_mcp(self.selected_provider)
        if not supports_native and not direct_mcp:
            error = ConversationContextError(
                ConversationContextErrorCode.TOOL_CAPABILITY_UNAVAILABLE,
                "当前模型既不支持原生工具调用，也没有经过验证的进程级 Siming MCP；"
                "Agent 本轮未启动。",
                details={
                    "provider": self.selected_provider or None,
                    "model": self.payload.model,
                    "remediation": "请选择支持原生工具调用或 Siming direct MCP 的模型。",
                },
            )
            public_error = public_context_failure(error)
            yield self.encode_event({"type": "error", **public_error.to_dict()})
            yield self.encode_event("[DONE]")
            return
        state = WorkspaceAssistantTurnState(
            db=db,
            project_id=self.project_id,
            payload=self.payload,
            selected_provider=self.selected_provider,
            supports_function_calling=supports_native,
            local_cli_selected=local_cli,
            local_cli_mcp_enabled=direct_mcp,
            encode_event=self.encode_event,
            execute_action=self.execute_action,
            prepare_context=self.prepare_context,
        )
        try:
            self._bootstrap_canonical(state)
            for event in self._setup_records(state):
                yield event
            run_id = getattr(state.assistant_run, "id", None)
            logger.info(
                "Workspace assistant turn start run=%s project=%s provider=%s model=%s mode=%s",
                run_id,
                self.project_id,
                self.selected_provider or "",
                self.payload.model,
                "native" if supports_native else ("direct_mcp" if direct_mcp else "unsupported"),
            )
            for event in self._configure(state):
                yield event
            async for event in self._run_iterations(state):
                yield event
            logger.info(
                "Workspace assistant turn complete run=%s project=%s",
                getattr(state.assistant_run, "id", None),
                self.project_id,
            )
            yield state.event({"type": "complete", "data": self._finalize(state)})
            yield state.event("[DONE]")
        except WorkspaceTurnSuperseded:
            logger.info(
                "Workspace assistant turn superseded run=%s project=%s",
                getattr(state.assistant_run, "id", None),
                self.project_id,
            )
            yield state.event(
                {
                    "type": "superseded",
                    "message": "本轮已被更新的作者消息替换，未继续执行模型或业务工具。",
                }
            )
            yield state.event("[DONE]")
        except (GeneratorExit, asyncio.CancelledError):
            self._handle_cancel(state)
            raise
        except ConversationContextError as exc:
            public_error = public_context_failure(exc)
            logger.warning(
                "Workspace context blocked run=%s code=%s",
                getattr(state.assistant_run, "id", None),
                exc.code.value,
            )
            self._persist_failure(
                state,
                public_error,
                phase="conversation_context_error",
            )
            yield state.event({"type": "error", **public_error.to_dict()})
            yield state.event("[DONE]")
        except LLMError as exc:
            public_error = public_model_failure(exc)
            logger.error(
                "Workspace model request failed run=%s class=%s",
                getattr(state.assistant_run, "id", None),
                public_error.failure_class,
            )
            self._persist_failure(state, public_error, phase="llm_error")
            yield state.event({"type": "error", **public_error.to_dict()})
            yield state.event("[DONE]")
        except Exception as exc:
            error_id = uuid.uuid4().hex
            public_error = public_server_failure(error_id)
            logger.error(
                "Workspace assistant failed error_id=%s run=%s type=%s",
                error_id,
                getattr(state.assistant_run, "id", None),
                type(exc).__name__,
            )
            self._persist_failure(state, public_error, phase="server_error")
            yield state.event({"type": "error", **public_error.to_dict()})
            yield state.event("[DONE]")
        finally:
            if state.tool_category_state_file:
                remove_tool_category_state(state.tool_category_state_file)

    def _supports_native_tools(self) -> bool:
        try:
            return bool(self.gateway.supports_tool_calling(self.payload.model))
        except Exception:
            return False

    def _bootstrap_canonical(self, state: WorkspaceAssistantTurnState) -> None:
        payload = state.payload
        if not payload.canonical_conversation_id or payload.conversation_id:
            return
        imported = ensure_workspace_transcript_from_system_conversation(
            state.db,
            project_id=state.project_id,
            system_conversation_id=payload.canonical_conversation_id,
        )
        commit_session(state.db)
        payload.conversation_id = imported.conversation.id

    def _setup_records(self, state: WorkspaceAssistantTurnState) -> list[str]:
        payload = state.payload
        workspace = self.workspace_factory(state.db)
        state.workspace = workspace
        if payload.conversation_id:
            conversation = workspace.conversation(state.project_id, payload.conversation_id)
            if conversation is None:
                raise NotFoundError("助手对话不存在")
            conversation.scope = _SCOPE
            if (
                payload.canonical_conversation_id
                and conversation.canonical_conversation_id != payload.canonical_conversation_id
            ):
                raise ValidationError(
                    "Canonical project conversation does not match the execution transcript"
                )
        else:
            conversation = workspace.create_conversation(
                project_id=state.project_id,
                title=assistant_title_from_message(payload.message),
                scope=_SCOPE,
            )
            state.db.flush()
        conversation.model = payload.model
        conversation.updated_at = datetime.utcnow()
        state.conversation = conversation
        state.reference_context_record = reference_context_record(payload.reference_context)
        state.reference_context_audit = reference_context_audit(payload.reference_context)
        created_at = datetime.utcnow()
        state.user_message = workspace.create_message(
            conversation_id=conversation.id,
            role="user",
            content=payload.message,
            payload_json=self._user_payload_json(state),
            status="completed",
            created_at=created_at,
            updated_at=created_at,
        )
        state.assistant_message = workspace.create_message(
            conversation_id=conversation.id,
            role="assistant",
            content="正在分析需求...",
            status="running",
            payload_json=self._assistant_payload_json(state),
            created_at=created_at + timedelta(microseconds=1),
            updated_at=created_at + timedelta(microseconds=1),
        )
        commit_session(state.db)
        for record in (conversation, state.user_message, state.assistant_message):
            state.db.refresh(record)
        state.assistant_run = create_assistant_run(
            state.db,
            project_id=state.project_id,
            conversation_id=conversation.id,
            user_message_id=state.user_message.id,
            assistant_message_id=state.assistant_message.id,
            scope=_SCOPE,
            model=payload.model,
        )
        current_sequence = int(state.user_message.sequence_no)
        newer_run_exists = False
        for prior in workspace.conversation_runs(state.project_id, conversation.id):
            if (
                str(prior.id) == str(state.assistant_run.id)
                or str(prior.status or "") not in {"queued", "running"}
            ):
                continue
            prior_user = workspace.message(str(prior.user_message_id or ""))
            prior_sequence = int(getattr(prior_user, "sequence_no", 0) or 0)
            if prior_sequence > current_sequence:
                newer_run_exists = True
                continue
            if 0 < prior_sequence < current_sequence:
                mark_assistant_run(
                    state.db,
                    prior,
                    status="superseded",
                    phase="superseded_by_new_user",
                    error="作品助手任务已被更新的作者消息替换",
                    final_reply="本轮已被更新的作者消息替换，未继续执行业务工具。",
                )
        if newer_run_exists:
            mark_assistant_run(
                state.db,
                state.assistant_run,
                status="superseded",
                phase="superseded_by_new_user",
                error="作品助手任务已被更新的作者消息替换",
                final_reply="本轮已被更新的作者消息替换，未继续执行业务工具。",
            )
        return [
            state.event(
                {
                    "type": "conversation",
                    "conversation": _assistant_conversation_to_dict(conversation),
                    "user_message": _assistant_message_to_dict(state.user_message),
                    "assistant_message": _assistant_message_to_dict(state.assistant_message),
                }
            ),
            state.event({"type": "run", "run": run_payload(state.assistant_run)}),
        ]

    @staticmethod
    def _user_payload_json(state: WorkspaceAssistantTurnState) -> str | None:
        if state.reference_context_record is None:
            return None
        return json.dumps({"reference_context": state.reference_context_record}, ensure_ascii=False)

    @staticmethod
    def _assistant_payload_json(state: WorkspaceAssistantTurnState) -> str:
        payload: dict[str, Any] = {"tool_logs": []}
        if state.reference_context_audit is not None:
            payload["reference_context_audit"] = state.reference_context_audit
        return json.dumps(payload, ensure_ascii=False)

    def _configure(self, state: WorkspaceAssistantTurnState) -> list[str]:
        payload = state.payload
        state.project = get_project_or_404(state.db, state.project_id)
        state.project_folder = str(ensure_project_folder(state.db, state.project))
        commit_session(state.db)
        state.local_cli_extra_body = dict(
            self.gateway.local_cli_extra_body(payload.model, cwd=state.project_folder) or {}
        )
        if state.local_cli_mcp_enabled:
            state.tool_category_state_file = create_tool_category_state()
            bind_tool_category_turn_guard(
                state.tool_category_state_file,
                {
                    "kind": "workspace",
                    "project_id": state.project_id,
                    "conversation_id": str(state.conversation.id),
                    "run_id": str(state.assistant_run.id),
                },
            )
        if state.local_cli_selected:
            self._configure_local_cli(state)
        if state.assistant_run.operation_id:
            state.local_cli_extra_body["operation_id"] = state.assistant_run.operation_id
        state.selected_text = (
            payload.selected_text
            if payload.selected_text and payload.selected_text.strip()
            else None
        )
        if state.selected_text and payload.selected_text_chapter_id:
            chapter = state.workspace.chapter(state.project_id, payload.selected_text_chapter_id)
            if chapter:
                state.selected_text_chapter_title = str(chapter.title or "") or None
        state.authorized_tool_names = set(select_workspace_tool_names())
        if state.local_cli_mcp_enabled:
            state.authorized_tool_names = {
                tool.name
                for tool in self.registry.list_for_workspace_direct_mcp()
            }
        state.workspace_tool_name_set = {TOOL_CATEGORY_CONTROLLER}
        state.workspace_tool_schemas = [tool_category_controller_schema()]
        state.base_system_prompt = build_system_prompt(
            get_workspace_pack(), outline_batch_count=payload.outline_batch_count
        )
        if state.local_cli_mcp_enabled:
            state.base_system_prompt += "\n\n" + self._direct_mcp_contract()
        state.all_write_tool_names = {
            name
            for name in state.authorized_tool_names
            if (definition := self.registry.get(name)) is not None
            and (
                definition.writes_project_data
                or definition.tool_type in {"write", "scheduler"}
            )
        }
        events = [
            state.event(
                {
                    "type": "status",
                    "message": "AI 助手开始分析和检索资料...",
                    "tool": "agent_loop",
                }
            )
        ]
        if not state.supports_function_calling:
            events.append(
                state.event(
                    {
                        "type": "status",
                        "message": (
                            "本机 CLI 已连接当前作品范围的临时 Siming MCP，可自行选择项目读写工具。"
                        ),
                        "tool": "local_cli_mcp_mode",
                    }
                )
            )
        return events

    def _configure_local_cli(self, state: WorkspaceAssistantTurnState) -> None:
        payload = state.payload
        read_granted = (
            state.selected_provider == "opencode_cli"
            and payload.local_cli_read_permission_grant == "read_once"
            and bool(payload.local_cli_read_paths)
        )
        state.local_cli_extra_body.update(
            {
                "local_cli_mcp_authorized": state.local_cli_mcp_enabled,
                "local_cli_allow_mcp": state.local_cli_mcp_enabled,
                "local_cli_read_permission_granted": read_granted,
                "local_cli_read_paths": list(payload.local_cli_read_paths) if read_granted else [],
                "local_cli_isolated": True,
                "local_cli_mcp_permission_pack": "project_management",
                "local_cli_mcp_project_id": state.project_id,
                "local_cli_mcp_tool_category_state_file": state.tool_category_state_file,
                "local_cli_terminal_draft_project_id": state.project_id
                if state.local_cli_mcp_enabled
                else "",
                "local_cli_terminal_draft_run_id": str(state.assistant_run.id)
                if state.local_cli_mcp_enabled
                else "",
                "local_cli_terminal_draft_iteration": 0,
            }
        )
        if state.local_cli_mcp_enabled:
            state.local_cli_extra_body["local_cli_retry_attempts"] = 1

    @staticmethod
    def _direct_mcp_contract() -> str:
        return (
            "当前进程已连接仅限本轮、仅限当前作品的 Siming MCP 服务器 siming_turn。"
            "项目数据的读取和修改必须直接调用该服务器中的工具；"
            "不要输出工具 JSON，不要启动另一个 CLI，不要修改任何全局 MCP 配置。"
            "请依据用户最新消息和真实项目数据自行判断任务、选择目标与工具。"
            "若决定生成章节正文，必须先取得真实章级大纲 ID，再读取写作上下文并保存一份未入库草稿；"
            "草稿保存成功后立即结束，不得继续执行角色、关系、世界观或建档写入。"
        )

    async def _run_iterations(
        self,
        state: WorkspaceAssistantTurnState,
    ) -> AsyncGenerator[str, None]:
        native = WorkspaceNativeTurn(state, self.gateway, self.registry)
        direct = WorkspaceDirectMcpTurn(state, self.gateway)
        iteration = 1
        while True:
            state.loop_action = "next"
            system_prompt, schemas, tool_choice = self._iteration_contract(state)
            yield state.event(
                {
                    "type": "iteration_start",
                    "iteration": iteration,
                    "message": f"第 {iteration} 轮推理",
                }
            )
            context_events: list[dict[str, Any]] = []
            try:
                messages = await self._prepare_model_step(
                    state,
                    system_prompt=system_prompt,
                    tool_schemas=schemas,
                    tool_choice=tool_choice,
                    event_buffer=context_events,
                )
            except Exception:
                for event in context_events:
                    yield state.event(event)
                raise
            for event in context_events:
                yield state.event(event)
            if state.supports_function_calling:
                async for event in native.run(
                    messages=messages,
                    iteration=iteration,
                    tool_schemas=schemas,
                    tool_choice=tool_choice,
                ):
                    yield event
            else:
                async for event in direct.run(messages=messages, iteration=iteration):
                    yield event
            if state.loop_action == "synthesize":
                async for event in self._run_final_synthesis(
                    state,
                    iteration=iteration,
                ):
                    yield event
                return
            if state.loop_action == "break":
                return
            iteration += 1

    async def _run_final_synthesis(
        self,
        state: WorkspaceAssistantTurnState,
        *,
        iteration: int,
    ) -> AsyncGenerator[str, None]:
        """Finish from persisted read results without replaying business tools."""

        for attempt in range(1, _FINAL_SYNTHESIS_ATTEMPTS + 1):
            state.require_current_run()
            yield state.event(
                {
                    "type": "status",
                    "message": (
                        "模型未返回可用答复，正在进行无工具补偿…"
                        if attempt == 1
                        else "模型未返回结论，正在进行一次无工具补偿重试…"
                    ),
                    "tool": "final_synthesis",
                }
            )
            system_prompt, _, _ = self._iteration_contract(state)
            context_events: list[dict[str, Any]] = []
            messages = await self._prepare_model_step(
                state,
                system_prompt=system_prompt,
                tool_schemas=[],
                tool_choice="none",
                event_buffer=context_events,
                extra_runtime_instruction=(
                    _FINAL_SYNTHESIS_INSTRUCTION
                    if attempt == 1
                    else _FINAL_SYNTHESIS_INSTRUCTION
                    + " 上一次无工具总结没有返回文字，本次必须输出可读的最终答复。"
                ),
                final_synthesis=True,
            )
            for event in context_events:
                yield state.event(event)

            chunks: list[str] = []

            def on_resume(info: dict[str, Any]) -> None:
                del info

            try:
                stream = self.gateway.stream_chat_completion(
                    messages=messages,
                    model=state.payload.model,
                    temperature=state.payload.temperature
                    if state.payload.temperature is not None
                    else 0.3,
                    max_tokens=state.payload.max_tokens,
                    timeout=_TIMEOUT_SECONDS,
                    retry=1,
                    resume=2,
                    on_resume=on_resume,
                    extra_body=state.local_cli_extra_body,
                )
                async for chunk in stream:
                    text = str(chunk or "")
                    chunks.append(text)
                    state.turn_telemetry.report_model_activity(state.assistant_run, text)
                    yield state.event({"type": "content_delta", "delta": text})
            except Exception:
                if "".join(chunks).strip() or attempt >= _FINAL_SYNTHESIS_ATTEMPTS:
                    raise
                continue

            reply = "".join(chunks).strip()
            if reply:
                state.final_reply = reply
                state.final_model = state.payload.model or ""
                state.final_usage = None
                state.loop_action = "break"
                yield state.event(
                    {
                        "type": "iteration_end",
                        "iteration": iteration,
                        "message": "已依据本轮工具结果生成最终结论",
                    }
                )
                return

        raise LLMError("没有收到模型的文字回复：无工具补偿重试后仍无最终结论")

    def _iteration_contract(
        self,
        state: WorkspaceAssistantTurnState,
    ) -> tuple[str, list[dict[str, Any]], str]:
        state.db.refresh(state.user_message)
        durable_reference = load_durable_reference_context(
            state.user_message, expected=state.reference_context_record
        )
        scoped = state.authorized_tool_names & set(
            tool_names_for_categories(state.active_categories)
        )
        state.workspace_tool_names = sorted(scoped)
        state.workspace_tool_name_set = {TOOL_CATEGORY_CONTROLLER, *state.workspace_tool_names}
        schemas = [
            tool_category_controller_schema(),
            *build_workspace_tool_schemas(state.workspace_tool_names),
        ]
        state.workspace_tool_schemas = schemas
        system_prompt = build_workspace_assistant_runtime_system_prompt(
            base_system_prompt=state.base_system_prompt,
            category_instruction=workspace_category_instruction(
                state.active_categories, category_selected=state.category_selected
            ),
            project_id=state.project_id,
            project_title=str(state.project.title or ""),
            selected_text=state.selected_text,
            selected_text_chapter_id=state.payload.selected_text_chapter_id,
            selected_text_chapter_title=state.selected_text_chapter_title,
            reference_context=durable_reference,
            outline_batch_count=state.payload.outline_batch_count,
        )
        return system_prompt, schemas, "required" if not state.category_selected else "auto"

    async def _prepare_model_step(
        self,
        state: WorkspaceAssistantTurnState,
        *,
        system_prompt: str,
        tool_schemas: list[dict[str, Any]],
        tool_choice: str,
        event_buffer: list[dict[str, Any]],
        extra_runtime_instruction: str = "",
        final_synthesis: bool = False,
    ) -> list[dict[str, Any]]:
        durable_conversation, context_input = self._load_context_input(state)
        durable_steps, trusted_ledger, source_hashes = self._execution_snapshot(
            state, durable_conversation
        )
        receipts = self._current_receipts(state, durable_conversation, durable_steps)
        delivered = tuple(
            tx for tx in state.tool_transactions if tx.state is ToolTransactionState.DELIVERED
        )

        def reload_turns():
            state.db.expire_all()
            conversation = state.workspace.conversation(
                state.project_id, state.conversation.id
            )
            if conversation is None:
                raise ValidationError("助手对话在 checkpoint 生成期间已不存在")
            messages = tuple(state.workspace.conversation_messages(conversation.id))
            return workspace_checkpoint_source_turns(
                conversation,
                messages,
                project_id=state.project_id,
                before_sequence=context_input.current_user_message.sequence_no,
            )

        async def emit_context_event(event: str, payload: dict[str, Any]) -> None:
            event_buffer.append({"type": event, **payload})

        native = state.supports_function_calling
        # ``current_tools`` is the budget/audit schema set, not an instruction
        # to render native tool calls.  Direct-MCP providers load these exact
        # schemas inside the isolated CLI process, so omitting them here would
        # undercount the real request while leaving its binding hash empty.
        # The direct gateway still receives messages only (see
        # WorkspaceDirectMcpTurn); schemas are neither textualized nor passed
        # as native ``tools``.
        actual_tools = [] if final_synthesis else tool_schemas
        result_reserve = max_model_visible_result_tokens_for_open_tool_schemas(
            actual_tools, resolve_tool=self.registry.get
        )
        prepared = await state.prepare_context(
            store=state.workspace,
            orchestrator=ContextOrchestrator(state.db),
            conversation=context_input.identity,
            owner_id=state.project_id,
            turns=context_input.turns,
            current_user_message=context_input.current_user_message,
            model=state.payload.model,
            task_type="assistant",
            protocol="native" if native else "direct_mcp",
            system_prompt=system_prompt,
            current_tools=tuple(actual_tools),
            reload_turns=reload_turns,
            current_ledger=receipts,
            delivered_transactions=delivered,
            trusted_execution_ledger=trusted_ledger,
            execution_source_hashes=source_hashes,
            provider_wrapper=self._provider_wrapper(
                state,
                native,
                final_synthesis=final_synthesis,
            ),
            provider_protocol_state={
                "tool_choice": tool_choice if native else None,
                "direct_mcp": state.local_cli_mcp_enabled,
                "tool_transport": "native" if native else "process_mcp",
                "mcp_server": None if native else "siming_turn",
            },
            extra_runtime_instruction=extra_runtime_instruction,
            output_reserve_tokens=state.payload.max_tokens,
            max_model_visible_result_tokens_for_open_tools=result_reserve,
            next_step_wrapper=(
                0 if final_synthesis else max_native_tool_transaction_wrapper_tokens()
            ),
            model_capability=ModelToolCapability(
                supports_native_tool_calling=native,
                direct_mcp_validated=not native and state.local_cli_mcp_enabled,
            ),
            event_sink=emit_context_event,
        )
        # ``prepare`` updates the durable budget snapshot on the context state.
        # Persist it before ``require_current_run`` expires the identity map to
        # observe a concurrent superseding turn; otherwise the refresh silently
        # discards the freshly computed budget metrics.
        commit_session(state.db)
        state.require_current_run()
        return prepared.provider_messages

    @staticmethod
    def _provider_wrapper(
        state: WorkspaceAssistantTurnState,
        native: bool,
        *,
        final_synthesis: bool = False,
    ) -> dict[str, Any]:
        return {
            "transport": "stream",
            "temperature": state.payload.temperature
            if state.payload.temperature is not None
            else 0.3,
            "max_tokens": state.payload.max_tokens,
            "timeout": _TIMEOUT_SECONDS,
            "retry": 1 if native else (0 if state.local_cli_mcp_enabled else 1),
            "resume": (
                2 if final_synthesis else 8 if native else (0 if state.local_cli_mcp_enabled else 8)
            ),
            "tool_transport": "none" if final_synthesis else "native" if native else "process_mcp",
            "extra_body": state.local_cli_extra_body or {},
        }

    @staticmethod
    def _load_context_input(state: WorkspaceAssistantTurnState):
        state.db.expire_all()
        conversation = state.workspace.conversation(state.project_id, state.conversation.id)
        if conversation is None:
            raise ValidationError("助手对话在上下文准备期间已不存在")
        messages = tuple(state.workspace.conversation_messages(conversation.id))
        return conversation, build_workspace_context_input(
            conversation,
            messages,
            project_id=state.project_id,
            current_user_message_id=state.user_message.id,
        )

    @staticmethod
    def _execution_snapshot(state: WorkspaceAssistantTurnState, conversation: Any):
        runs = tuple(state.workspace.conversation_runs(state.project_id, conversation.id))
        steps = tuple(step for run in runs for step in state.workspace.run_steps(run.id))
        ledger = workspace_execution_ledger_from_run_steps(
            conversation, runs, steps, project_id=state.project_id
        )
        return steps, ledger, execution_source_hashes_from_run_steps(steps)

    @staticmethod
    def _current_receipts(
        state: WorkspaceAssistantTurnState, conversation: Any, steps: tuple[Any, ...]
    ):
        compactable_ids = {
            str(result.persisted_step_id)
            for transaction in state.tool_transactions
            if transaction.state is ToolTransactionState.COMPACTABLE
            for result in transaction.results
            if result.persisted_step_id
        }
        if not compactable_ids:
            return ()
        receipt_steps = tuple(step for step in steps if str(step.id) in compactable_ids)
        if len(receipt_steps) != len(compactable_ids):
            raise ValidationError("已消费工具事务缺少持久 RunStep")
        return workspace_tool_receipts_from_run_steps(
            conversation,
            state.assistant_run,
            receipt_steps,
            project_id=state.project_id,
            write_tools=state.all_write_tool_names,
        )

    @staticmethod
    def _finalize(state: WorkspaceAssistantTurnState) -> dict[str, Any]:
        return finalize_workspace_assistant_turn(
            state.db,
            assistant_run=state.assistant_run,
            assistant_message=state.assistant_message,
            conversation=state.conversation,
            final_reply=state.final_reply,
            applied_actions=state.applied_actions,
            tool_logs=state.tool_logs,
            searched_context=state.searched_context,
            final_model=state.final_model,
            final_usage=state.final_usage,
        )

    @staticmethod
    def _handle_cancel(state: WorkspaceAssistantTurnState) -> None:
        if not assistant_cancel_was_explicit():
            return
        if state.assistant_message:
            state.assistant_message.content = "任务已取消，本轮不会再写入章节。"
            state.assistant_message.status = "aborted"
            state.assistant_message.updated_at = datetime.utcnow()
            if state.conversation:
                state.conversation.updated_at = datetime.utcnow()
            commit_session(state.db)
        mark_assistant_run(
            state.db,
            state.assistant_run,
            status="cancelled",
            phase="cancelled",
            error="用户取消了任务",
            final_reply="任务已取消，本轮不会再写入章节。",
        )

    @staticmethod
    def _persist_failure(
        state: WorkspaceAssistantTurnState,
        failure: PublicAssistantFailure,
        *,
        phase: str,
    ) -> None:
        if state.assistant_message:
            state.assistant_message.content = failure.message
            state.assistant_message.status = "error"
            payload: dict[str, Any] = {
                "tool_logs": state.tool_logs,
                "assistant_error": failure.to_dict(),
            }
            if failure.failure_class == "conversation_context":
                payload["conversation_context_error"] = failure.to_dict()
            if state.reference_context_audit is not None:
                payload["reference_context_audit"] = state.reference_context_audit
            state.assistant_message.payload_json = json.dumps(payload, ensure_ascii=False)
            commit_session(state.db)
        if state.assistant_run is not None:
            mark_assistant_run(
                state.db,
                state.assistant_run,
                status="error",
                phase=phase,
                error=failure.persisted_error,
            )
