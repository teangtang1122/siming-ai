"""Workspace implementation of the scheduler's task-runner port."""
from __future__ import annotations

import asyncio
import json

from sqlalchemy.orm import Session

from ...database.models import ScheduledTask
from ...modules.model_runtime.application.execution import model_executor as LLMGateway
from . import executor as workspace_executor
from .registry import registry


def run_workspace_scheduled_task(db: Session, task: ScheduledTask) -> str:
    """Run one scheduled prompt through the normal workspace tool chain."""
    system_parts = [
        "你是一个定时任务执行助手。请根据用户的提示完成任务。",
    ]
    if task.tool_policy:
        system_parts.append(f"可用工具：{', '.join(task.tool_policy)}")

    messages = [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": task.prompt},
    ]

    tool_policy_set = set(task.tool_policy) if task.tool_policy else None
    tool_schemas = registry.get_schemas()
    if tool_policy_set:
        tool_schemas = [
            tool
            for tool in tool_schemas
            if tool["function"]["name"] in tool_policy_set
        ]

    async def run_agent_loop() -> str:
        final_content = ""
        for _turn in range(10):
            result = await LLMGateway.stream_chat_completion_with_tools(
                messages=messages,
                tools=tool_schemas,
                model=None,
                temperature=0.3,
                max_tokens=4000,
                timeout=120,
            )
            content = result.get("content", "")
            tool_calls = result.get("tool_calls", [])
            if not tool_calls:
                final_content = content
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                }
            )
            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "")
                try:
                    arguments = json.loads(
                        tool_call.get("function", {}).get("arguments", "{}")
                    )
                except json.JSONDecodeError:
                    arguments = {}

                tool_result = await workspace_executor.execute_workspace_action(
                    db,
                    task.project_id,
                    {"tool": tool_name, "arguments": arguments},
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": json.dumps(
                            tool_result,
                            ensure_ascii=False,
                        )[:4000],
                    }
                )
        return final_content or "任务执行完成"

    try:
        return asyncio.run(run_agent_loop())
    except Exception as exc:
        raise RuntimeError(f"Agent execution failed: {exc}") from exc


__all__ = ["run_workspace_scheduled_task"]
