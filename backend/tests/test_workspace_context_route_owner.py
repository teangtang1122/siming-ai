"""Only the native server runner can select internal context delivery."""

import asyncio

from app.services.workspace.executor import execute_workspace_action
from app.services.workspace.registry import registry


def test_prepare_route_is_owned_by_executor_not_tool_arguments(monkeypatch):
    assert "execution_route" not in registry.get_spec("prepare_task_context").parameters_schema()["properties"]
    observed = []

    async def prepare(_db, _project_id, args):
        observed.append(dict(args))
        return {"tool": "prepare_task_context", "status": "ok", "data": {}}

    original = registry.get_handler
    monkeypatch.setattr(
        registry,
        "get_handler",
        lambda name: prepare if name == "prepare_task_context" else original(name),
    )
    action = {
        "tool": "prepare_task_context",
        "arguments": {"task_type": "outline_planning"},
    }
    asyncio.run(execute_workspace_action(None, "project-1", action))
    asyncio.run(execute_workspace_action(None, "project-1", action, internal_generator=True))
    assert [args["execution_route"] for args in observed] == ["external_mcp", "internal_api"]
