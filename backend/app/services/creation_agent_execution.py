"""Single-turn execution state machine for the conversational Creation Agent."""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.architecture.tool_categories import (
    TOOL_CATEGORY_CONTROLLER,
    tool_names_for_categories,
)
from app.core.exceptions import LLMError
from app.core.json_repair import parse_json_object
from app.database.models import NovelCreationStageRun
from app.modules.creation.interfaces.agent_progress import (
    creation_tool_completed_event,
    creation_tool_started_event,
)
from app.modules.creation.interfaces.agent_scope import (
    CREATION_AGENT_REVISION_TOOL_NAMES,
    CREATION_AGENT_TOOL_NAMES,
    CREATION_AGENT_WRITE_TOOL_NAMES,
    CREATION_TURN_MAX_FAILED_WRITES,
    CREATION_WRITE_SUCCESS_STATUSES,
    creation_turn_write_denial,
    creation_turn_writes_closed,
)
from app.services.creation_agent_turn_records import (
    CREATION_AGENT_TURN_SCHEMA,
    canonical_tool_call,
    record_prompt_metric,
)
from app.services.workspace.executor import execute_workspace_action

CREATION_AGENT_TOOLS = set(CREATION_AGENT_TOOL_NAMES)
SESSION_TOOLS = CREATION_AGENT_TOOLS - {
    "get_creation_operation", "get_creation_entity", "patch_creation_entity",
    "delete_creation_entity", "get_creation_artifact_diff",
    "restore_creation_artifact_version", "cancel_creation_operation",
    "pause_creation_operation", "resume_creation_operation",
    "retry_creation_operation", "read_imported_file",
}
REVISION_TOOLS = set(CREATION_AGENT_REVISION_TOOL_NAMES)
WRITE_TOOLS = set(CREATION_AGENT_WRITE_TOOL_NAMES)
READ_TOOLS = CREATION_AGENT_TOOLS - WRITE_TOOLS
_MAX_TOOL_MESSAGE_CHARS = 90_000

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
CompleteTurn = Callable[..., Awaitable[dict[str, Any]]]
EmitProgress = Callable[..., Awaitable[None]]


@dataclass
class CreationTurnState:
    db: Session
    session: Any
    message: str
    model: str | None
    tool_mode: str
    messages: list[dict[str, Any]]
    schemas: list[dict[str, Any]]
    baseline_revision: int
    extra_body: dict[str, Any] | None
    on_event: ProgressCallback | None
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    write_results: list[dict[str, Any]] = field(default_factory=list)
    protocol_messages: list[dict[str, Any]] = field(default_factory=list)
    seen_calls: set[str] = field(default_factory=set)
    final_reply: str = ""
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    prompt_metrics: list[dict[str, Any]] = field(default_factory=list)
    direct_mcp_calls: list[dict[str, Any]] = field(default_factory=list)
    active_categories: tuple[str, ...] = ()
    successful_write_count: int = 0
    failed_write_count: int = 0
    successful_read_count: int = 0


@dataclass(frozen=True)
class CreationExecutionBindings:
    complete_tool_turn: CompleteTurn
    emit_progress: EmitProgress
    tool_schemas: Callable[[tuple[str, ...]], list[dict[str, Any]]]
    category_tool_result: Callable[
        [dict[str, Any]],
        tuple[dict[str, Any], tuple[str, ...] | None],
    ]


async def _report_stream_resume(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    payload: dict[str, Any],
) -> None:
    checkpoint_chars = max(0, int(payload.get("checkpoint_chars") or 0))
    await bindings.emit_progress(
        state.on_event,
        state.progress_events,
        "model_step_started",
        (
            "模型连接中断，正在从已验证的文字检查点继续…"
            if checkpoint_chars else "模型工具响应中断，正在重新获取完整工具调用…"
        ),
        {
            "resume_attempt": max(1, int(payload.get("resume_attempt") or 1)),
            "checkpoint_chars": checkpoint_chars,
        },
    )


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    try:
        return (
            json.loads(raw_arguments)
            if isinstance(raw_arguments, str)
            else dict(raw_arguments)
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return parse_json_object(str(raw_arguments)) or {}


def _run_message_projection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    projected = {
        key: value.get(key)
        for key in (
            "run_id", "id", "session_id", "stage", "operation", "status",
            "operation_id", "input_revision", "result_mode", "warning",
            "failure_class", "current_message", "next_action",
        )
        if value.get(key) is not None
    }
    result = value.get("result") if isinstance(value.get("result"), dict) else {}
    stages = result.get("stages") if isinstance(result.get("stages"), dict) else {}
    if stages:
        projected["generated_artifacts"] = sorted(
            key for key in stages if key != "entity_change"
        )
    entity_change = stages.get("entity_change")
    if isinstance(entity_change, dict):
        projected["entity_change"] = entity_change
    return projected


def _write_message_projection(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    projected: dict[str, Any] = {}
    for key in (
        "session_id", "revision", "current_revision", "failure_class",
        "artifact", "affected_artifacts", "changed_fields", "operation_id",
        "project_id", "reason",
    ):
        value = data.get(key)
        if value is not None and not isinstance(value, dict):
            projected[key] = value
    artifact = data.get("artifact") if isinstance(data.get("artifact"), dict) else None
    if artifact:
        projected["artifact"] = {
            key: artifact.get(key)
            for key in ("artifact", "status", "source", "revision", "stale_reason")
            if artifact.get(key) is not None
        }
    entity = data.get("entity") if isinstance(data.get("entity"), dict) else None
    if entity:
        projected["entity"] = {
            key: entity.get(key)
            for key in ("id", "artifact", "entity_type", "entity_key", "status", "revision")
            if entity.get(key) is not None
        }
    run = _run_message_projection(data.get("run"))
    if run:
        projected["run"] = run
    session = data.get("session") if isinstance(data.get("session"), dict) else None
    if session:
        projected["session"] = {
            key: session.get(key)
            for key in ("id", "status", "current_stage", "revision", "created_project_id")
            if session.get(key) is not None
        }
    changes = data.get("changes")
    if isinstance(changes, list):
        projected["change_count"] = len(changes)
    return {
        "tool": result.get("tool"),
        "status": result.get("status"),
        "detail": result.get("detail"),
        "data": projected or None,
    }


def _bounded_tool_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 8:
        return {"omitted": True, "reason": "maximum_depth"}
    if isinstance(value, str):
        if len(value) <= 12_000:
            return value
        return value[:12_000] + "…[truncated]"
    if isinstance(value, list):
        selected = value[:40]
        bounded = [_bounded_tool_value(item, depth=depth + 1) for item in selected]
        if len(value) > len(selected):
            bounded.append({"omitted_items": len(value) - len(selected)})
        return bounded
    if isinstance(value, dict):
        items = list(value.items())[:80]
        bounded = {
            str(key): _bounded_tool_value(child, depth=depth + 1)
            for key, child in items
        }
        if len(value) > len(items):
            bounded["_omitted_fields"] = len(value) - len(items)
        return bounded
    return value


def _tool_message_content(name: str, result: dict[str, Any]) -> str:
    """Serialize valid, bounded JSON for the model without altering API results."""

    payload = _write_message_projection(result) if name in WRITE_TOOLS else result
    content = json.dumps(payload, ensure_ascii=False, default=str)
    if len(content) <= _MAX_TOOL_MESSAGE_CHARS:
        return content
    bounded = _bounded_tool_value(payload)
    content = json.dumps(bounded, ensure_ascii=False, default=str)
    if len(content) <= _MAX_TOOL_MESSAGE_CHARS:
        return content
    return json.dumps({
        "tool": result.get("tool") or name,
        "status": "error",
        "detail": (
            "Tool result exceeded the model context boundary. Narrow the artifact/entity "
            "query and fetch exact candidates separately."
        ),
        "data": {
            "reason": "tool_result_too_large",
            "original_chars": len(json.dumps(result, ensure_ascii=False, default=str)),
        },
    }, ensure_ascii=False)


async def _execute_domain_call(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    name: str,
    arguments: dict[str, Any],
    available_tool_names: set[str],
) -> tuple[dict[str, Any], tuple[str, ...] | None]:
    if name == TOOL_CATEGORY_CONTROLLER:
        result, categories = bindings.category_tool_result(arguments)
        if categories is not None:
            await bindings.emit_progress(
                state.on_event,
                state.progress_events,
                "tool_categories_changed",
                str(result.get("detail") or "已准备立项能力"),
                dict(result.get("data") or {}),
            )
        return result, categories
    if name not in available_tool_names or name not in CREATION_AGENT_TOOLS:
        return {
            "tool": name,
            "status": "skipped",
            "detail": "该工具当前未向立项会话开放",
        }, None
    write_denial = creation_turn_write_denial(
        name,
        successful_writes=state.successful_write_count,
        failed_writes=state.failed_write_count,
    )
    if write_denial is not None:
        return write_denial, None
    if name in SESSION_TOOLS:
        arguments["session_id"] = state.session.id
    if name in REVISION_TOOLS and not arguments.get("expected_revision"):
        state.db.refresh(state.session)
        arguments["expected_revision"] = int(state.session.revision or 0)
    if name in {
        "generate_creation_artifact",
        "refine_creation_artifact",
        "regenerate_creation_artifact",
    }:
        if not str(arguments.get("model") or "").strip():
            arguments["model"] = state.model
        if state.model:
            arguments["use_model"] = True
    signature = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    if signature in state.seen_calls:
        return {
            "tool": name,
            "status": "skipped",
            "detail": "相同工具调用已执行，本轮不重复提交",
        }, None
    state.seen_calls.add(signature)
    started = creation_tool_started_event(name, arguments)
    await bindings.emit_progress(
        state.on_event,
        state.progress_events,
        started["type"],
        started["message"],
        started["data"],
    )
    return await execute_workspace_action(
        state.db,
        "",
        {"tool": name, "arguments": arguments},
    ), None


async def _execute_native_calls(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    calls: list[dict[str, Any]],
) -> tuple[str, ...] | None:
    available = set(tool_names_for_categories(state.active_categories)) & CREATION_AGENT_TOOLS
    reads_ready_before_step = state.successful_read_count > 0
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else {}
        name = str((function or {}).get("name") or "")
        arguments = _parse_arguments((function or {}).get("arguments") or "{}")
        if name in WRITE_TOOLS and not reads_ready_before_step:
            tool_result, pending_categories = ({
                "tool": name,
                "status": "denied",
                "detail": (
                    "写入前必须先完成一次真实业务读取，并让读取结果进入下一模型步骤；"
                    "不得在同一个模型步骤并列决定读取和写入。"
                ),
                "data": {
                    "reason": "read_required",
                    "required_next_step": (
                        "Read the exact target, then decide the write "
                        "in the next model step."
                    ),
                },
            }, None)
        else:
            tool_result, pending_categories = await _execute_domain_call(
                state,
                bindings,
                name,
                arguments,
                available,
            )
        state.tool_results.append(tool_result)
        if name != TOOL_CATEGORY_CONTROLLER:
            completed = creation_tool_completed_event(name, arguments, tool_result)
            await bindings.emit_progress(
                state.on_event,
                state.progress_events,
                completed["type"],
                completed["message"],
                completed["data"],
            )
        if name in WRITE_TOOLS:
            status = str(tool_result.get("status") or "")
            result_data = (
                tool_result.get("data")
                if isinstance(tool_result.get("data"), dict)
                else {}
            )
            boundary_reason = str(result_data.get("reason") or "")
            if status in CREATION_WRITE_SUCCESS_STATUSES:
                state.successful_write_count += 1
                state.write_results.append(tool_result)
            elif boundary_reason not in {
                "successful_write_limit", "failed_write_limit", "read_required",
            }:
                state.failed_write_count += 1
                if state.failed_write_count == CREATION_TURN_MAX_FAILED_WRITES:
                    await bindings.emit_progress(
                        state.on_event,
                        state.progress_events,
                        "tool_completed",
                        "写入连续失败已达上限，本轮已停止自动重试",
                        {
                            "tool": name,
                            "status": "denied",
                            "turn_boundary": "failed_write_limit",
                            "failed_writes": state.failed_write_count,
                        },
                    )
        if (
            name in READ_TOOLS
            and str(tool_result.get("status") or "") in {"ok", "warning"}
        ):
            state.successful_read_count += 1
        tool_message = {
            "role": "tool",
            "tool_call_id": str(call.get("id") or ""),
            "content": _tool_message_content(name, tool_result),
        }
        state.messages.append(tool_message)
        state.protocol_messages.append(tool_message)
        if pending_categories is not None:
            return pending_categories
    return None


async def _run_native_step(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    iteration: int,
) -> bool:
    requires_category_selection = not any(
        item.get("tool") == TOOL_CATEGORY_CONTROLLER and item.get("status") == "ok"
        for item in state.tool_results
    )
    await bindings.emit_progress(
        state.on_event,
        state.progress_events,
        "model_step_started",
        "正在判断需要哪些立项能力…" if iteration == 0 else "正在根据真实工具结果继续处理…",
        {"iteration": iteration + 1, "active_categories": list(state.active_categories)},
    )
    writes_closed = creation_turn_writes_closed(
        successful_writes=state.successful_write_count,
        failed_writes=state.failed_write_count,
    )
    # Once the deterministic mutation boundary closes, ask for the final text
    # with no tools. This prevents a compliant model from spending another
    # planning step on reads or downstream writes.
    state.schemas = [] if writes_closed else bindings.tool_schemas(state.active_categories)

    async def report_resume(payload: dict[str, Any]) -> None:
        await _report_stream_resume(state, bindings, payload)

    result = await bindings.complete_tool_turn(
        messages=state.messages,
        tools=state.schemas,
        model=state.model,
        temperature=0.25,
        max_tokens=None,
        timeout=300,
        retry=0,
        resume=8,
        on_resume=report_resume,
        extra_body=state.extra_body,
        tool_choice="required" if requires_category_selection else "auto",
    )
    record_prompt_metric(
        state.prompt_metrics,
        iteration=iteration + 1,
        phase="native",
        active_categories=state.active_categories,
        messages=state.messages,
        schemas=state.schemas,
        result=result,
    )
    content = str(result.get("content") or "")
    raw_calls = result.get("tool_calls") if isinstance(result.get("tool_calls"), list) else []
    calls = [
        call
        for index, raw_call in enumerate(raw_calls)
        if (call := canonical_tool_call(
            raw_call,
            fallback_id=f"creation-tool-{iteration}-{index}",
        )) is not None
    ][:12]
    category_calls = [
        call for call in calls
        if call.get("function", {}).get("name") == TOOL_CATEGORY_CONTROLLER
    ]
    if category_calls:
        calls = category_calls[:1]
    if not calls:
        if requires_category_selection:
            raise LLMError(
                "模型没有调用本步骤唯一开放的 set_tool_categories，"
                "本轮已终止，未接受模型伪造的等待或完成回复"
            )
        state.final_reply = content.strip()
        return False
    assistant_message = {"role": "assistant", "content": content, "tool_calls": calls}
    state.messages.append(assistant_message)
    state.protocol_messages.append(assistant_message)
    pending_categories = await _execute_native_calls(state, bindings, calls)
    if pending_categories is not None:
        state.active_categories = pending_categories
    return True


async def run_native_steps(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
) -> None:
    if state.tool_mode != "native":
        return
    for iteration in range(6):
        if not await _run_native_step(state, bindings, iteration):
            break


def _created_project_id(state: CreationTurnState) -> str | None:
    for item in reversed(state.tool_results):
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if item.get("tool") == "finalize_creation_session" and item.get("status") == "ok":
            candidate = str(data.get("project_id") or "").strip()
            if candidate:
                return candidate
    state.db.expire_all()
    refreshed = state.db.get(type(state.session), state.session.id)
    candidate = str(getattr(refreshed, "created_project_id", "") or "").strip()
    return candidate or None


def truthful_no_write_reply(state: CreationTurnState) -> str:
    failures = [
        str(item.get("detail") or "工具未完成")
        for item in state.tool_results
        if item.get("status") not in {"ok", "running"}
    ]
    reads = [
        item for item in state.tool_results
        if item.get("status") == "ok" and item.get("tool") not in WRITE_TOOLS
    ]
    if failures:
        return f"本轮没有保存任何修改：{failures[-1]}。请调整要求后重试。"
    if reads:
        return "本轮只完成了立项工具读取，没有保存任何修改。请明确要写入的对象和内容后重试。"
    if state.tool_mode == "direct_mcp":
        return "本轮没有获得可验证的 MCP 结果，因此无法确认读取或修改了立项数据。请重试。"
    if state.tool_results:
        return "本轮执行了立项工具，但没有产生可确认的写入。请调整要求后重试。"
    return "本轮未执行任何立项工具，因此没有读取或修改立项数据。请重试。"


async def _complete_reply(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
    created_project_id: str | None,
) -> None:
    if created_project_id:
        state.final_reply = (
            "正式作品已创建并进入作品库。请点击下方按钮进入正式作品；"
            "进入后项目助手会自动展开，后续正文与项目资料都在那里继续。"
        )
        return
    if not state.final_reply and state.tool_results and state.tool_mode != "direct_mcp":
        state.messages.append({
            "role": "user",
            "content": (
                "请根据以上真实工具返回，用两到四句中文说明本轮实际修改了什么、"
                "哪些内容没有修改，并提出一个基于当前立项数据的后续问题。"
                "不得声称未成功的写入已经保存。"
            ),
        })

        async def report_resume(payload: dict[str, Any]) -> None:
            await _report_stream_resume(state, bindings, payload)

        try:
            summary = await bindings.complete_tool_turn(
                messages=state.messages,
                tools=[],
                model=state.model,
                temperature=0.2,
                max_tokens=None,
                timeout=300,
                retry=0,
                resume=8,
                on_resume=report_resume,
            )
            record_prompt_metric(
                state.prompt_metrics,
                iteration=len(state.prompt_metrics) + 1,
                phase="summary",
                active_categories=state.active_categories,
                messages=state.messages,
                schemas=[],
                result=summary,
            )
            state.final_reply = str(summary.get("content") or "").strip()
        except Exception:
            state.final_reply = ""
    if not state.final_reply:
        if state.write_results:
            details = [
                str(item.get("detail") or item.get("tool") or "已更新立项数据")
                for item in state.write_results[:3]
            ]
            state.final_reply = f"本轮已完成：{'；'.join(details)}。接下来你最想补充哪一部分？"
        else:
            state.final_reply = truthful_no_write_reply(state)


async def _present_active_run(state: CreationTurnState) -> dict[str, Any] | None:
    active_run = None
    for item in reversed(state.tool_results):
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        candidate = data.get("run") if isinstance(data.get("run"), dict) else None
        if candidate:
            active_run = candidate
            break
    if not active_run:
        return None
    run_id = str(active_run.get("id") or active_run.get("run_id") or "").strip()
    durable_run = state.db.get(NovelCreationStageRun, run_id) if run_id else None
    if durable_run and durable_run.status in {
        "waiting_user", "waiting_author", "completed", "failed",
        "cancelled", "interrupted", "superseded",
    }:
        from app.services.novel_creation_run_presentation import present_serialized_run

        return await present_serialized_run(
            state.db,
            run=durable_run,
            model=state.model,
            assistant_reply=state.final_reply,
            tool_results=state.tool_results,
        )
    return active_run


async def finish_creation_turn(
    state: CreationTurnState,
    bindings: CreationExecutionBindings,
) -> dict[str, Any]:
    created_project_id = _created_project_id(state)
    await _complete_reply(state, bindings, created_project_id)
    for offset in range(0, len(state.final_reply), 240):
        await bindings.emit_progress(
            state.on_event,
            state.progress_events,
            "reply_delta",
            "",
            {"delta": state.final_reply[offset:offset + 240]},
        )
    active_run = await _present_active_run(state)
    turn_messages: list[dict[str, Any]] = [
        {"role": "user", "content": state.message[:1_000_000]},
        *state.protocol_messages,
        {"role": "assistant", "content": state.final_reply[:80_000]},
    ]
    prompt_tokens = (
        sum(int(item["prompt_tokens"]) for item in state.prompt_metrics if item.get("prompt_tokens") is not None)
        if any(item.get("prompt_tokens") is not None for item in state.prompt_metrics)
        else None
    )
    turn_trace = {
        "schema": CREATION_AGENT_TURN_SCHEMA,
        "session_id": str(state.session.id),
        "model": state.model,
        "tool_mode": state.tool_mode,
        "replayable": state.tool_mode == "native",
        "messages": turn_messages,
        "progress_events": state.progress_events,
        "prompt_metrics": state.prompt_metrics,
        "direct_mcp_calls": state.direct_mcp_calls,
        "outcome": {
            "status": "completed",
            "tool_count": len(state.tool_results),
            "write_count": len(state.write_results),
            "created_project_id": created_project_id,
            "active_categories": list(state.active_categories),
            "prompt_tokens": prompt_tokens,
            "prompt_token_steps_reported": sum(
                1 for item in state.prompt_metrics if item.get("prompt_tokens") is not None
            ),
        },
    }
    return {
        "reply": state.final_reply,
        "tool_results": state.tool_results,
        "write_count": len(state.write_results),
        "run": active_run,
        "created_project_id": created_project_id,
        "_turn_trace": turn_trace,
    }


__all__ = [
    "CREATION_AGENT_TOOLS",
    "CreationExecutionBindings",
    "CreationTurnState",
    "REVISION_TOOLS",
    "SESSION_TOOLS",
    "WRITE_TOOLS",
    "finish_creation_turn",
    "run_native_steps",
]
