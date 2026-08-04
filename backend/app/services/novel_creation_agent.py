"""Tool-driven conversational control plane for a creation session."""
from __future__ import annotations

import json
import inspect
import re
from typing import Any

from sqlalchemy.orm import Session

from app.ai.local_cli_adapter import is_local_cli_provider
from app.core.json_repair import parse_json_object
from app.modules.model_runtime.application.execution import model_executor as LLMGateway
from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry


CREATION_AGENT_TOOLS = {
    "get_creation_session", "get_creation_snapshot", "get_creation_operation",
    "get_creation_artifact", "list_creation_artifacts", "get_creation_dependencies",
    "get_creation_dependency_graph", "validate_creation_consistency",
    "patch_creation_session", "patch_creation_artifact", "lock_creation_fields",
    "unlock_creation_fields", "undo_creation_artifact", "list_creation_entities",
    "get_creation_entity", "patch_creation_entity", "delete_creation_entity",
    "list_creation_artifact_versions", "get_creation_artifact_diff",
    "restore_creation_artifact_version", "confirm_creation_artifact",
    "generate_creation_artifact", "refine_creation_artifact",
    "regenerate_creation_artifact", "cancel_creation_operation",
    "pause_creation_operation", "resume_creation_operation", "retry_creation_operation",
    "validate_creation_session", "finalize_creation_session",
    "preview_creation_import", "apply_creation_import", "list_imported_files",
    "read_imported_file",
}

_SESSION_TOOLS = {
    name for name in CREATION_AGENT_TOOLS
    if name not in {
        "get_creation_operation", "get_creation_entity", "patch_creation_entity",
        "delete_creation_entity", "get_creation_artifact_diff",
        "restore_creation_artifact_version", "cancel_creation_operation",
        "pause_creation_operation", "resume_creation_operation",
        "retry_creation_operation", "read_imported_file",
    }
}

_REVISION_TOOLS = {
    "patch_creation_session", "patch_creation_artifact", "lock_creation_fields",
    "unlock_creation_fields", "undo_creation_artifact", "patch_creation_entity",
    "delete_creation_entity", "restore_creation_artifact_version",
    "confirm_creation_artifact", "generate_creation_artifact",
    "refine_creation_artifact", "regenerate_creation_artifact",
    "apply_creation_import",
}

_WRITE_TOOLS = {
    "patch_creation_session", "patch_creation_artifact", "lock_creation_fields",
    "unlock_creation_fields", "undo_creation_artifact", "patch_creation_entity",
    "delete_creation_entity", "restore_creation_artifact_version",
    "confirm_creation_artifact", "generate_creation_artifact",
    "refine_creation_artifact", "regenerate_creation_artifact",
    "cancel_creation_operation", "pause_creation_operation",
    "resume_creation_operation", "retry_creation_operation",
    "finalize_creation_session", "apply_creation_import",
}
CREATION_AGENT_CLI_TIMEOUT_SECONDS = 600

_WRITE_CLAIM_RE = re.compile(
    r"(?:已|已经|实际|成功).{0,24}(?:写入|保存|修改|更新)|"
    r"(?:写入|保存|修改|更新).{0,12}(?:成功|完成)"
)


def _tool_schemas() -> list[dict[str, Any]]:
    return [
        schema for schema in registry.get_schemas()
        if schema.get("function", {}).get("name") in CREATION_AGENT_TOOLS
    ]


def _system_prompt(session_id: str) -> str:
    return f"""你是司命的对话式立项助手。当前 creation session_id={session_id}。
所有世界观、角色、关系、地点、势力、分卷、章节细纲、场景细纲和创作约束都必须通过工具读取和修改。
你可以按任意顺序工作；不要强迫用户走固定阶段。缺少软依赖时说明影响，但不要阻止用户。
每轮先读取会话及相关 artifact/entity，结合当前数据缺口决定下一步，再自行调用零到多个工具。
如果用户给出了新事实、偏好或回答，先把能确定的内容立即增量写入对应结构化数据，再提出一个最有价值的后续问题；不要把数据积攒到“采访结束”后才生成。
用户对你上一轮问题的简短回答也是有效的新事实。只要含义足够明确，本轮必须至少调用一次 patch_creation_session、patch_creation_artifact、entity 写入或生成工具；不能只反复读取后声称已经保存。
提问必须基于刚读取到的现有数据，避免重复询问已经存在的内容。用户可以随时跳到世界观、角色、地点、势力、分卷或章节细纲，不能在创意阶段结束后停止协作。
用户要求新增对象时，把完整自然语言要求放入 instruction；对象数量由用户语义决定，不要假定固定数量。
局部请求优先使用 entity 工具或带 entity_type/entity_id 的生成工具，不要重写整个 artifact。
写入必须使用刚读取到的 revision；不得改动锁定字段，不得用旧结果覆盖人工新修改。
只有用户明确要求创建正式作品时才调用 finalize_creation_session。
工具返回 running 表示后台任务已经可靠创建，不要重复调用；告诉用户任务已开始即可。
完成工具调用后，用简洁中文说明读取了什么、修改/启动了什么、保留了什么以及可能受影响的数据。"""


def _text_only_system_prompt() -> str:
    return """你是司命的创作立项顾问。
根据用户提供的信息帮助梳理世界观、角色、地点、势力、大纲和创作约束，并提出最有价值的后续问题。
当前通道仅提供文本建议，不能读取或写入结构化项目数据，也不能启动任务。
只输出自然语言建议，不要声称任何内容已经保存、修改或开始生成。请用简洁中文回复。"""


def _cli_mcp_system_prompt(session_id: str) -> str:
    return _system_prompt(session_id) + f"""

你当前运行在本机 Agent CLI 中，不接收 OpenAI function-calling schema。司命的数据工具由 CLI 自己通过已连接的 Siming MCP 获取。
MCP 工具在 CLI 中通常带 siming_ 前缀。当前立项会话是 {session_id}；所有立项工具都必须传入这个 session_id，不要创建另一个会话。
每轮先调用 siming_get_creation_session 或 siming_get_creation_snapshot 获取当前 revision。用户要求写入时，必须调用合适的 siming_patch_creation_artifact、siming_patch_creation_session 或 entity 工具落库。
写入返回成功后必须再次调用读取工具，确认 revision 已增加且目标字段能读到，才能向用户说“已写入”。如果 MCP 调用失败、超时或写后读取不一致，明确报告失败，不得用自然语言冒充成功。
不要直接修改数据库文件或小说镜像文件；结构化数据只能通过 Siming MCP 写入。"""


async def _complete_tool_turn(**kwargs: Any) -> dict[str, Any]:
    """Collect the gateway's streaming tool protocol into one assistant turn."""
    stream = LLMGateway.stream_chat_completion_with_tools(**kwargs)
    if inspect.isawaitable(stream):
        completed = await stream
        if isinstance(completed, dict):
            return completed

    content: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    async for event in stream:
        event_type = event.get("type")
        if event_type == "content_delta":
            content.append(str(event.get("delta") or ""))
        elif event_type == "tool_call_delta":
            index = int(event.get("index") or 0)
            call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if event.get("id"):
                call["id"] = str(event["id"])
            if event.get("name"):
                call["name"] = str(event["name"])
            if event.get("arguments_delta"):
                call["arguments"] += str(event["arguments_delta"])
    tool_calls = [
        {
            "id": call["id"] or f"creation-tool-{index}",
            "type": "function",
            "function": {"name": call["name"], "arguments": call["arguments"] or "{}"},
        }
        for index, call in sorted(calls.items())
        if call["name"]
    ]
    return {"content": "".join(content), "tool_calls": tool_calls}


def _prepare_agent_request(
    session: Any,
    message: str,
    model: str | None,
    history: list[dict[str, str]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, dict[str, Any] | None, bool]:
    native_tool_calls = LLMGateway.supports_tool_calling(model)
    try:
        provider = LLMGateway.provider_for_model(model)
    except Exception:
        provider = ""
    local_cli_mcp = is_local_cli_provider(provider)
    prompt = (
        _system_prompt(session.id)
        if native_tool_calls
        else _cli_mcp_system_prompt(session.id) if local_cli_mcp else _text_only_system_prompt()
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompt}]
    for item in (history or [])[-12:]:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content[:80_000]})
    messages.append({"role": "user", "content": message})
    extra_body = None
    if local_cli_mcp:
        from app.services.content_store import content_root

        extra_body = LLMGateway.local_cli_extra_body(
            model,
            cwd=str(content_root()),
            base={
                "moshu_task_type": "planning",
                "local_cli_allow_mcp": True,
                "local_cli_timeout_seconds": CREATION_AGENT_CLI_TIMEOUT_SECONDS,
            },
        )
    schemas = _tool_schemas() if native_tool_calls else []
    return messages, schemas, int(session.revision or 0), extra_body, local_cli_mcp


def _record_verified_mcp_write(
    db: Session,
    session: Any,
    baseline_revision: int,
    tool_results: list[dict[str, Any]],
    write_results: list[dict[str, Any]],
) -> None:
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


async def run_creation_agent(
    db: Session,
    *,
    session: Any,
    message: str,
    model: str | None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    messages, schemas, baseline_revision, extra_body, local_cli_mcp = _prepare_agent_request(
        session, message, model, history,
    )
    tool_results: list[dict[str, Any]] = []
    write_results: list[dict[str, Any]] = []
    seen_calls: set[str] = set()
    final_reply = ""
    for _iteration in range(6):
        result = await _complete_tool_turn(
            messages=messages,
            tools=schemas,
            model=model,
            temperature=0.25,
            # Do not impose a second fixed cap here. The selected provider and
            # configured model capability remain the source of truth.
            max_tokens=None,
            timeout=0,
            extra_body=extra_body,
        )
        content = str(result.get("content") or "")
        calls = result.get("tool_calls") if isinstance(result.get("tool_calls"), list) else []
        if not calls:
            final_reply = content.strip()
            break
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for call in calls[:12]:
            function = call.get("function") if isinstance(call, dict) else {}
            name = str((function or {}).get("name") or "")
            raw_arguments = (function or {}).get("arguments") or "{}"
            try:
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
            except (json.JSONDecodeError, TypeError, ValueError):
                arguments = parse_json_object(str(raw_arguments)) or {}
            if name not in CREATION_AGENT_TOOLS:
                tool_result = {"tool": name, "status": "skipped", "detail": "该工具不属于立项会话"}
            else:
                if name in _SESSION_TOOLS:
                    arguments["session_id"] = session.id
                if name in _REVISION_TOOLS and not arguments.get("expected_revision"):
                    db.refresh(session)
                    arguments["expected_revision"] = int(session.revision or 0)
                if name in {"generate_creation_artifact", "refine_creation_artifact", "regenerate_creation_artifact"}:
                    arguments.setdefault("model", model)
                signature = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False, sort_keys=True, default=str)
                if signature in seen_calls:
                    tool_result = {"tool": name, "status": "skipped", "detail": "相同工具调用已执行，本轮不重复提交"}
                else:
                    seen_calls.add(signature)
                    tool_result = await execute_workspace_action(
                        db, "", {"tool": name, "arguments": arguments},
                    )
            tool_results.append(tool_result)
            if name in _WRITE_TOOLS and tool_result.get("status") in {"ok", "running"}:
                write_results.append(tool_result)
            messages.append({
                "role": "tool",
                "tool_call_id": str(call.get("id") or ""),
                "content": json.dumps(tool_result, ensure_ascii=False, default=str)[:120_000],
            })

    if not final_reply and tool_results:
        # Some providers finish a tool round without producing the required
        # user-facing summary. Give the same model one text-only turn grounded
        # in the real tool results; it may summarize but cannot invent writes.
        messages.append({
            "role": "user",
            "content": (
                "请根据以上真实工具返回，用两到四句中文说明本轮实际修改了什么、"
                "哪些内容没有修改，并提出一个基于当前立项数据的后续问题。"
                "不得声称未成功的写入已经保存。"
            ),
        })
        try:
            summary = await _complete_tool_turn(
                messages=messages,
                tools=[],
                model=model,
                temperature=0.2,
                max_tokens=None,
                timeout=0,
            )
            final_reply = str(summary.get("content") or "").strip()
        except Exception:
            final_reply = ""

    if local_cli_mcp:
        # MCP runs in a sibling process with its own SQLAlchemy session. Expire
        # this request's identity map before checking whether the CLI truly
        # committed a write; model prose alone is never accepted as evidence.
        _record_verified_mcp_write(db, session, baseline_revision, tool_results, write_results)

    if not write_results and final_reply and _WRITE_CLAIM_RE.search(final_reply):
        failures = [
            str(item.get("detail") or "工具未完成写入")
            for item in tool_results
            if item.get("status") not in {"ok", "running"}
        ]
        reason = failures[-1] if failures else "本轮只读取了数据，没有执行写入"
        final_reply = f"我读取了当前立项数据，但本轮没有保存任何修改：{reason}。这句话要作为创意核心、世界观、角色还是大纲内容？你确认后我会立即写入。"

    if not final_reply:
        if write_results:
            details = [str(item.get("detail") or item.get("tool") or "已更新立项数据") for item in write_results[:3]]
            final_reply = f"本轮已完成：{'；'.join(details)}。接下来你最想补充哪一部分？"
        elif tool_results:
            failures = [
                str(item.get("detail") or "工具未完成写入")
                for item in tool_results
                if item.get("status") not in {"ok", "running"}
            ]
            reason = failures[-1] if failures else "本轮只读取了数据，没有执行写入"
            final_reply = f"我读取了当前立项数据，但本轮没有保存任何修改：{reason}。请再说明这句话要作为创意核心、世界观、角色还是大纲内容，我会立即写入。"
        else:
            final_reply = "我已读取当前立项上下文，但这一轮没有执行数据修改。你希望先补充创意核心、世界观、角色还是大纲？"

    active_run = None
    for item in reversed(tool_results):
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        candidate = data.get("run") if isinstance(data.get("run"), dict) else None
        if candidate:
            active_run = candidate
            break
    return {
        "reply": final_reply,
        "tool_results": tool_results,
        "write_count": len(write_results),
        "run": active_run,
    }


__all__ = ["CREATION_AGENT_TOOLS", "run_creation_agent"]
