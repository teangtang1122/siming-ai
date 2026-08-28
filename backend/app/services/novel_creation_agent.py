"""Tool-driven conversational control plane for a creation session."""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.orm import Session

from app.ai.local_cli_adapter import is_local_cli_provider
from app.ai.local_cli_prompt import supports_direct_mcp
from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    TOOL_CATEGORY_METADATA,
    normalize_tool_categories,
    tool_category_controller_schema,
    tool_names_for_categories,
)
from app.architecture.uow import commit_session
from app.core.exceptions import LLMError
from app.database.models import NovelCreationStageRun
from app.modules.creation.interfaces.agent_scope import (
    CREATION_MODEL_SPAWNING_TOOL_NAMES,
)
from app.modules.model_runtime.application.execution import model_executor as LLMGateway
from app.services.agent_tool_stream import collect_tool_turn
from app.services.creation_agent_execution import (
    CREATION_AGENT_TOOLS,
    CreationExecutionBindings,
    CreationTurnState,
    finish_creation_turn,
    run_native_steps,
)
from app.services.creation_agent_turn_records import (
    CREATION_AGENT_TURN_SCHEMA,
    creation_agent_replay_messages,
)
from app.services.creation_agent_turn_records import (
    record_prompt_metric as _record_prompt_metric,
)
from app.services.novel_creation_runs import interrupt_novel_creation_run
from app.services.tool_category_state import (
    activate_tool_categories,
    create_tool_category_state,
    read_tool_category_audits,
    read_tool_category_events,
    read_tool_category_state,
    remove_tool_category_state,
)
from app.services.workspace.registry import registry

CreationProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
def _domain_tool_schemas() -> list[dict[str, Any]]:
    return [
        schema for schema in registry.get_schemas()
        if schema.get("function", {}).get("name") in CREATION_AGENT_TOOLS
    ]


def _tool_schemas(
    active_categories: tuple[str, ...] = (),
    *,
    excluded_tools: set[str] | frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Build the exact bootstrap/scoped schema set for one native model step."""

    allowed = (
        set(tool_names_for_categories(active_categories))
        & CREATION_AGENT_TOOLS
    ) - set(excluded_tools)
    return [
        tool_category_controller_schema(),
        *[
            schema for schema in _domain_tool_schemas()
            if schema.get("function", {}).get("name") in allowed
        ],
    ]


async def _emit_progress(
    callback: CreationProgressCallback | None,
    captured: list[dict[str, Any]],
    event_type: str,
    message: str,
    data: dict[str, Any] | None = None,
) -> None:
    event = {
        "type": event_type,
        "message": str(message or "")[:500],
        "data": dict(data or {}),
    }
    captured.append(event)
    if callback is None:
        return
    emitted = callback(event)
    if inspect.isawaitable(emitted):
        await emitted


def _category_tool_result(
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    try:
        active_categories = normalize_tool_categories(arguments.get("enabled_categories"))
    except ValueError as exc:
        return {
            "tool": TOOL_CATEGORY_CONTROLLER,
            "status": "error",
            "detail": str(exc),
            "data": None,
        }, None
    labels = [TOOL_CATEGORY_METADATA[category]["label"] for category in active_categories]
    tool_count = len(set(tool_names_for_categories(active_categories)) & CREATION_AGENT_TOOLS)
    detail = f"已准备{'、'.join(labels)}能力，共 {tool_count} 项立项工具" if labels else "已关闭全部业务工具"
    return {
        "tool": TOOL_CATEGORY_CONTROLLER,
        "status": "ok",
        "detail": detail,
        "data": {
            "enabled_categories": list(active_categories),
            "labels": labels,
            "tool_count": tool_count,
        },
    }, active_categories


def _system_prompt(session_id: str) -> str:
    return f"""你是司命的对话式立项助手。当前 creation session_id={session_id}。
所有立项资料必须通过工具读取和修改。可按任意顺序工作；软依赖缺失时说明影响但不阻断。
每个用户回合的第一模型步骤只开放 set_tool_categories，必须先调用它选择完成最新消息所需的类别；在控制工具返回前不得直接回答、等待或声称工具不可用。类别从下一模型步骤生效，调用控制工具后当前步骤立即结束。立项资料通常使用 creation_data，生成、确认、版本、导入或正式建书通常使用 creation_flow。
快照只是 revision、状态、锁和数据规模索引，不包含阶段正文；不得把省略内容当成空值或自行补写。以最新用户消息决定查询目标：先读取目标 artifact；角色、关系、地点、势力、分卷、章节或场景先用 list_creation_entities 的 artifact/entity_type/query/limit 召回摘要，再对候选 ID 调用 get_creation_entity 复核精确事实。
读取结果必须先返回给你，再由下一模型步骤决定写工具；不得在同一个模型步骤并列发出读取和写入。不要用对话历史中的旧工具结果代替本轮数据库读取。
用户给出明确的新事实、偏好或简短回答时，立即增量写入，再基于现有缺口提一个最有价值的问题；不得积攒到采访结束，也不得只读取后声称保存。
每条用户消息最多完成一次成功的写工具调用；一次原子写入可以包含用户对同一个目标明确给出的全部事实。写入成功后立即停止本轮的确认、生成和下游推进，简要报告结果并只提一个问题，等待作者下一条消息后才能继续写。
“继续”“下一步”等简短回复只能由你结合最新消息和真实快照判断当前一个待处理动作，不能据此连续确认多个阶段或自动生成后续阶段。confirm_creation_artifact 仅在最新用户消息确实表达了对当前版本的确认时使用；不得确认本轮刚生成或刚修改的内容。
写入参数失败时可根据真实错误修正，但最多尝试三次；达到上限后如实说明错误并结束，不得循环重试。
用户可随时跳到任意资料。新增对象时将完整要求放入 instruction，数量服从用户语义。
局部请求必须优先使用 entity 工具或带 entity_type/entity_id 的生成工具，不要重写整个 artifact。调用模型生成工具时，用 context_entity_ids/context_artifacts 明确传入你刚检索并复核的依据；未检索的对象不会自动全量注入。
写入必须使用刚读取到的 revision；不得改动锁定字段，不得用旧结果覆盖人工新修改。
只有用户明确要求创建正式作品时才调用 finalize_creation_session。成功后请用户通过界面按钮进入正式作品，并说明项目助手会自动展开，不再邀请其在立项会话写正文。
工具返回 running 表示任务已可靠创建，不要重复调用。最后简洁说明实际读取、修改或启动的内容及影响。"""


def _cli_mcp_system_prompt(
    session_id: str,
    *,
    model: str | None = None,
    active_categories: tuple[str, ...] = (),
) -> str:
    model_label = str(model or "").strip() or "未显式解析"
    if active_categories:
        category_instruction = (
            "当前已开放工具类别："
            + "、".join(TOOL_CATEGORY_METADATA[category]["label"] for category in active_categories)
            + "。直接完成原始用户任务；需要其他类别时调用 set_tool_categories，调用后立即结束本次响应，等待司命按新类别重启临时 MCP。"
        )
    else:
        category_instruction = (
            "当前只开放 set_tool_categories，必须立即调用它选择完成最新消息所需的类别；"
            "在控制工具返回前不得直接回复、等待或声称工具不可用。调用后立即结束本次响应，"
            "等待司命按所选类别重启临时 MCP。"
        )
    return f"""你是司命的对话式立项助手，也是本轮唯一负责生成内容的模型。
当前 creation session_id={session_id}，当前模型身份={model_label}。
用户已授权本条消息连接进程级临时 Siming MCP；MCP 只提供当前会话的直接读取和写入，不会替你再启动模型。

{category_instruction}
处理业务步骤时先调用 siming_turn 的 get_creation_snapshot 读取最新 revision、状态、锁和数据规模索引；快照不含阶段正文，不得猜测省略事实。随后按最新消息读取一个目标 artifact；角色、关系、地点、势力、分卷、章节或场景使用 list_creation_entities 的 artifact/entity_type/query/limit 查摘要，并对候选 ID 调用 get_creation_entity 复核。不要使用 Shell、编辑文件、扫描项目目录或访问其他会话。
会话基本字段使用 patch_creation_session；完整阶段使用 patch_creation_artifact；单个已有角色、地点、势力、卷、章节或场景必须优先使用 entity 工具。完整阶段可用 path=/、action=set 一次写入根对象。不得为了写一个对象而读取或回写整个集合。
创意方向 artifact=concepts 的根对象必须包含 options 和 selected_concept_id。每个 option 至少包含 id、title、logline、protagonist_seed（identity、goal、lack）、world_hook、core_conflict、opening_hook；story_engine、subtitle、differentiators、risks 可按内容补充。方案数量完全服从用户语义：用户未指定数量时只生成一套；只有用户明确要求多个、候选或对比时才生成对应数量，绝不擅自补成多套。
其他阶段保持快照中的结构；若尚无数据：world_style 使用 writing_style/world_tone/story_structure/pacing/style_rules/forbidden_patterns/worldbuilding/display_groups；characters 使用 characters/relationships；locations 使用 entries/relations；macro_outline 使用 story_overview/core_conflict/ending_direction/target_chapters/volumes/stage_plan；opening_outline 只规划三章，使用顶层 chapters/sections，每章 2 至 6 个场景。

每次写入都必须使用刚读取到的 expected_revision；写工具成功返回的新 revision 就是提交凭据，不要为了确认而再次读取。只有工具报 revision conflict 时才重新读取一次后按用户原意重做，不能覆盖锁定字段。
每条用户消息最多允许一次成功的写工具调用；一次原子写入可包含用户对同一个目标明确给出的全部事实。首次写入成功后，本轮必须立即停止所有确认、生成、修改、导入、任务控制和正式建书调用，报告该次结果并等待作者下一条消息。不得在一轮内“生成→确认→继续下游”，也不得连续确认多个阶段。
“继续”“下一步”等简短回复只用于由你结合最新消息和真实快照选定当前一个动作，不能当作批量推进授权。confirm_creation_artifact 只在最新用户消息确实确认当前版本时使用；绝不确认本轮刚写入的内容。
写入失败后只可依据真实错误修正，累计三次失败就停止调用写工具、说明最后错误并结束，不能无限重试。
用户给出明确事实时本轮立即写入，不要只提问；只有用户明确要求创建正式作品时才调用 finalize_creation_session。
本轮是同步的一次模型交互：不要创建或宣称创建后台生成任务，不要轮询任务状态，不要说“正在后台运行”。只有 MCP 写工具明确返回成功，才能说已经保存。
临时 MCP 和授权会在本条消息结束时销毁，不要修改任何 CLI 全局配置。完成本轮唯一的写入或必要读取后立即结束，不要继续分析或重复读取；最后说明实际写入内容，并提出至多一个最有价值的后续问题。"""


def _resolve_effective_model(model: str | None) -> str | None:
    """Resolve one stable model identity for this assistant turn."""
    requested = str(model or "").strip()
    if requested.casefold() in {"siming", "default", "auto", "openai"}:
        requested = ""
    try:
        selection = LLMGateway.select_model_for_task(
            task_type="planning",
            model_override=requested or None,
        )
        resolved = str(getattr(selection, "model", "") or "").strip()
        if resolved:
            return resolved
    except Exception:
        pass
    return requested or None


def _prepare_agent_request(
    session: Any,
    message: str,
    model: str | None,
    replay_messages: list[dict[str, Any]] | None,
    *,
    local_cli_read_paths: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, dict[str, Any] | None, str]:
    native_tool_calls = LLMGateway.supports_tool_calling(model)
    try:
        provider = LLMGateway.provider_for_model(model)
    except Exception:
        provider = ""
    local_cli_selected = is_local_cli_provider(provider)
    direct_transient_mcp = local_cli_selected and supports_direct_mcp(provider)
    if local_cli_selected and not direct_transient_mcp:
        raise LLMError(
            "自定义 CLI 没有可验证的 MCP 启动协议，不能用于立项写入；"
            "请选择已支持的 Agent CLI。"
        )
    if not local_cli_selected and not native_tool_calls:
        raise LLMError("当前模型既不支持原生工具调用，也不是支持直接 MCP 的 Agent CLI")
    tool_mode = "direct_mcp" if direct_transient_mcp else "native"
    prompt = (
        _cli_mcp_system_prompt(session.id, model=model)
        if direct_transient_mcp
        else _system_prompt(session.id)
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
    messages.extend(replay_messages or [])
    messages.append({"role": "user", "content": message})
    extra_body = None
    if local_cli_selected:
        extra_body = LLMGateway.local_cli_extra_body(
            model,
            base={
                "moshu_task_type": "planning",
                # Every known Agent CLI receives one process-scoped MCP for
                # this creation session.
                "local_cli_isolated": True,
                "local_cli_mcp_authorized": True,
                "local_cli_allow_mcp": True,
                "local_cli_read_permission_granted": (
                    provider == "opencode_cli" and bool(local_cli_read_paths)
                ),
                "local_cli_read_paths": (
                    list(local_cli_read_paths or []) if provider == "opencode_cli" else []
                ),
                "local_cli_mcp_permission_pack": "creation_session",
                "local_cli_mcp_creation_session_id": session.id,
                # Agent work is bounded by meaningful activity, not wall-clock
                # duration. Large generation and multi-tool turns may validly
                # exceed three minutes.
                "local_cli_timeout_seconds": 0,
                "local_cli_quiet_seconds": 120,
                "local_cli_suspected_stall_seconds": 300,
                "local_cli_stalled_seconds": 600,
                "local_cli_retry_attempts": 1,
                # OpenCode occasionally exits a provider stream with
                # ``finish=unknown`` while retaining the unfinished session.
                # Continue that exact session once; never restart the whole
                # model turn and duplicate an MCP write.
                "local_cli_resume_incomplete_opencode": provider == "opencode_cli",
            },
        )
    schemas = _tool_schemas() if native_tool_calls else []
    return messages, schemas, int(session.revision or 0), extra_body, tool_mode


def _record_verified_mcp_write(
    db: Session,
    session: Any,
    baseline_revision: int,
    tool_results: list[dict[str, Any]],
    write_results: list[dict[str, Any]],
) -> None:
    if any(item.get("tool") == "mcp_verified_write" for item in tool_results):
        return
    db.expire_all()
    refreshed_session = db.get(type(session), session.id)
    current_revision = int(getattr(refreshed_session, "revision", baseline_revision) or 0)
    if current_revision <= baseline_revision:
        return
    verified_write = {
        "tool": "mcp_verified_write",
        "status": "ok",
        "detail": f"MCP 写入已验证，立项 revision {baseline_revision}→{current_revision}",
        "data": {
            "session_id": session.id,
            "revision_before": baseline_revision,
            "revision_after": current_revision,
        },
    }
    tool_results.append(verified_write)
    write_results.append(verified_write)


def _active_stage_run_ids(db: Session, session_id: str) -> set[str]:
    return {
        str(run_id)
        for (run_id,) in (
            db.query(NovelCreationStageRun.id)
            .filter(
                NovelCreationStageRun.session_id == session_id,
                NovelCreationStageRun.status.in_(["queued", "running"]),
            )
            .all()
        )
    }


def _interrupt_new_cli_stage_runs(
    db: Session,
    session_id: str,
    baseline_run_ids: set[str],
) -> None:
    """Defensively settle runs created by an old or stale MCP tool surface."""

    try:
        db.rollback()
        runs = (
            db.query(NovelCreationStageRun)
            .filter(
                NovelCreationStageRun.session_id == session_id,
                NovelCreationStageRun.status.in_(["queued", "running"]),
            )
            .all()
        )
        changed = False
        for run in runs:
            if run.id in baseline_run_ids:
                continue
            changed = interrupt_novel_creation_run(
                db,
                run,
                message="立项 CLI 连接已中断，本次生成没有完成",
            ) or changed
        if changed:
            commit_session(db)
        else:
            db.rollback()
    except Exception:
        # Preserve the model/transport exception that caused this cleanup.
        db.rollback()


async def _drain_direct_mcp_events(
    state_file: str,
    event_offset: int,
    *,
    on_event: CreationProgressCallback | None,
    progress_events: list[dict[str, Any]],
) -> tuple[int, bool, dict[str, Any] | None]:
    """Forward new MCP events and project turn-boundary signals."""

    events, next_offset = read_tool_category_events(state_file, event_offset)
    category_changed = False
    write_boundary_event: dict[str, Any] | None = None
    for event in events:
        await _emit_progress(
            on_event,
            progress_events,
            str(event.get("type") or "tool_completed"),
            str(event.get("message") or ""),
            dict(event.get("data") or {}),
        )
        category_changed = category_changed or event.get("type") == "tool_categories_changed"
        event_data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event_data.get("turn_boundary") in {
            "successful_write_limit",
            "failed_write_limit",
        }:
            write_boundary_event = event
    return next_offset, category_changed, write_boundary_event


def _direct_mcp_boundary_reply(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if data.get("turn_boundary") == "successful_write_limit":
        return (
            "本轮已完成一次写入，后续自动写入已被系统拦截。"
            "请先确认本次结果；下一步要处理哪个单一对象？"
        )
    return (
        "本轮没有继续自动重试：写入连续失败已达三次，系统已关闭本轮写工具。"
        "请检查当前资料结构；下一次要先修改哪个单一对象？"
    )


async def _run_direct_mcp_steps(
    db: Session,
    *,
    session: Any,
    messages: list[dict[str, Any]],
    model: str | None,
    extra_body: dict[str, Any] | None,
    baseline_revision: int,
    baseline_active_run_ids: set[str],
    tool_results: list[dict[str, Any]],
    write_results: list[dict[str, Any]],
    on_event: CreationProgressCallback | None,
    progress_events: list[dict[str, Any]],
    prompt_metrics: list[dict[str, Any]],
    direct_mcp_calls: list[dict[str, Any]],
) -> tuple[str, tuple[str, ...]]:
    """Run bounded CLI control/business steps with a freshly scoped MCP each time."""

    state_file = create_tool_category_state()
    runtime_body = {**dict(extra_body or {}), "local_cli_mcp_tool_category_state_file": state_file}
    active_categories: tuple[str, ...] = ()
    event_offset = 0
    observed_version = 0
    final_reply = ""
    try:
        for iteration in range(6):
            messages[0] = {
                "role": "system",
                "content": _cli_mcp_system_prompt(
                    str(session.id),
                    model=model,
                    active_categories=active_categories,
                ),
            }
            await _emit_progress(
                on_event,
                progress_events,
                "model_step_started",
                (
                    "正在判断需要哪些立项能力…"
                    if not active_categories
                    else "正在使用已准备的能力处理立项资料…"
                ),
                {"iteration": iteration + 1, "active_categories": list(active_categories)},
            )
            scoped_schemas = _tool_schemas(
                active_categories, excluded_tools=CREATION_MODEL_SPAWNING_TOOL_NAMES,
            )
            task = asyncio.create_task(collect_tool_turn(
                LLMGateway,
                messages=messages,
                tools=[],
                model=model,
                temperature=0.25,
                max_tokens=None,
                timeout=0,
                retry=0,
                resume=0,
                extra_body=runtime_body,
                tool_choice=None,
            ))
            controller_finished_step = False
            write_boundary_event: dict[str, Any] | None = None
            while not task.done():
                await asyncio.wait({task}, timeout=0.2)
                event_offset, changed, boundary = await _drain_direct_mcp_events(
                    state_file,
                    event_offset,
                    on_event=on_event,
                    progress_events=progress_events,
                )
                controller_finished_step = controller_finished_step or changed
                write_boundary_event = boundary or write_boundary_event
                if (controller_finished_step or write_boundary_event is not None) and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    break
            result = (
                {"content": "", "tool_calls": []}
                if controller_finished_step or write_boundary_event is not None
                else await task
            )
            _record_prompt_metric(
                prompt_metrics,
                iteration=iteration + 1,
                phase="direct_mcp",
                active_categories=active_categories,
                messages=messages,
                schemas=scoped_schemas,
                result=result if not controller_finished_step else None,
            )
            event_offset, _, boundary = await _drain_direct_mcp_events(
                state_file,
                event_offset,
                on_event=on_event,
                progress_events=progress_events,
            )
            write_boundary_event = boundary or write_boundary_event
            if write_boundary_event is not None:
                final_reply = _direct_mcp_boundary_reply(write_boundary_event)
                break
            state = read_tool_category_state(state_file)
            next_version = int(state.get("version") or 0)
            if next_version > observed_version:
                observed_version = next_version
                active_categories = normalize_tool_categories(state.get("requested_categories") or [])
                activate_tool_categories(state_file)
                latest_group_event = next(
                    (
                        event
                        for event in reversed(progress_events)
                        if event.get("type") == "tool_categories_changed"
                    ),
                    None,
                )
                if latest_group_event:
                    tool_results.append({
                        "tool": TOOL_CATEGORY_CONTROLLER,
                        "status": "ok",
                        "detail": latest_group_event.get("message") or "已准备立项能力",
                        "data": latest_group_event.get("data") or {},
                    })
                continue
            if observed_version == 0:
                raise LLMError(
                    "本机 CLI 没有调用临时 MCP 中唯一开放的 set_tool_categories，"
                    "本轮已终止，未接受 CLI 返回的等待或完成文字"
                )
            final_reply = str(result.get("content") or "").strip()
            break
    except asyncio.CancelledError:
        _interrupt_new_cli_stage_runs(db, str(session.id), baseline_active_run_ids)
        raise
    except Exception:
        _record_verified_mcp_write(
            db,
            session,
            baseline_revision,
            tool_results,
            write_results,
        )
        if not write_results:
            _interrupt_new_cli_stage_runs(db, str(session.id), baseline_active_run_ids)
            raise
    finally:
        direct_mcp_calls.extend(read_tool_category_audits(state_file))
        remove_tool_category_state(state_file)
    return final_reply, active_categories


async def run_creation_agent(
    db: Session,
    *,
    session: Any,
    message: str,
    model: str | None,
    replay_messages: list[dict[str, Any]] | None = None,
    local_cli_read_paths: list[str] | None = None,
    on_event: CreationProgressCallback | None = None,
) -> dict[str, Any]:
    effective_model = _resolve_effective_model(model)
    messages, schemas, baseline_revision, extra_body, tool_mode = _prepare_agent_request(
        session,
        message,
        effective_model,
        replay_messages,
        local_cli_read_paths=local_cli_read_paths,
    )
    state = CreationTurnState(
        db=db,
        session=session,
        message=message,
        model=effective_model,
        tool_mode=tool_mode,
        messages=messages,
        schemas=schemas,
        baseline_revision=baseline_revision,
        extra_body=extra_body,
        on_event=on_event,
    )
    bindings = CreationExecutionBindings(
        complete_tool_turn=lambda **kwargs: collect_tool_turn(LLMGateway, **kwargs),
        emit_progress=_emit_progress,
        tool_schemas=_tool_schemas,
        category_tool_result=_category_tool_result,
    )

    if tool_mode == "direct_mcp":
        baseline_active_run_ids = _active_stage_run_ids(db, str(session.id))
        # Never keep even a read transaction open while the CLI process runs.
        db.rollback()
        state.final_reply, state.active_categories = await _run_direct_mcp_steps(
            db,
            session=session,
            messages=state.messages,
            model=effective_model,
            extra_body=extra_body,
            baseline_revision=baseline_revision,
            baseline_active_run_ids=baseline_active_run_ids,
            tool_results=state.tool_results,
            write_results=state.write_results,
            on_event=on_event,
            progress_events=state.progress_events,
            prompt_metrics=state.prompt_metrics,
            direct_mcp_calls=state.direct_mcp_calls,
        )
    await run_native_steps(state, bindings)

    if tool_mode == "direct_mcp":
        _record_verified_mcp_write(
            db,
            session,
            baseline_revision,
            state.tool_results,
            state.write_results,
        )
    return await finish_creation_turn(state, bindings)


__all__ = [
    "CREATION_AGENT_TOOLS",
    "CREATION_AGENT_TURN_SCHEMA",
    "creation_agent_replay_messages",
    "run_creation_agent",
]
