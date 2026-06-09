# Tool Authoring Guide

> How to add a new tool to Moshu's workspace ToolRegistry so it works everywhere:
> internal project assistant, scheduler, MCP clients, external Agents, and frontend.

## Quick Checklist

When adding a new tool, follow these steps:

### 1. Implement the handler

Create your handler in `backend/app/services/workspace/tools/`:

```python
# backend/app/services/workspace/tools/my_feature.py
from __future__ import annotations
from typing import Any
from sqlalchemy.orm import Session

async def my_new_tool(
    db: Session,
    project_id: str,
    args: dict[str, Any],
) -> dict:
    """What this tool does."""
    # Your implementation here
    return {
        "tool": "my_new_tool",
        "status": "ok",
        "detail": "Done",
        "data": {"result": "value"},
    }
```

### 2. Register metadata in ToolRegistry

Add a `ToolDef` to `backend/app/services/workspace/registry.py`:

```python
_r(ToolDef(
    name="my_new_tool",
    description="Clear description of what this tool does",
    input_schema={
        "query": {"type": "string", "description": "What to search for"},
        "limit": {"type": "integer", "description": "Max results"},
    },
    required=["query"],
    tool_type="read",  # read | analysis | web | memory | generator | write | scheduler
    risk_level="safe",  # safe | low | medium | high | destructive
    writes_project_data=False,
    idempotent=True,
    requires_confirmation=False,
    estimated_cost="free",  # free | low | medium | high
    expose_to_internal_agent=True,
    expose_to_scheduler=True,
    expose_to_mcp=True,
    permission_tags={"read", "search"},
    handler=my_new_tool,
))
```

### 3. Add focused tests

Create tests in `backend/tests/`:

```python
# backend/tests/test_my_feature.py
import unittest
from app.services.workspace.registry import registry

class MyNewToolTest(unittest.TestCase):
    def test_registered(self):
        td = registry.get("my_new_tool")
        self.assertIsNotNone(td)

    def test_metadata_complete(self):
        td = registry.get("my_new_tool")
        self.assertTrue(td.description)
        self.assertTrue(td.input_schema)
        self.assertIn(td.risk_level, ("safe", "low", "medium", "high", "destructive"))

    def test_permission_pack(self):
        td = registry.get("my_new_tool")
        pack = registry._derive_mcp_pack(td)
        self.assertIn(pack, ("readonly_collaboration", "draft_generation",
                             "project_writing", "project_management",
                             "trusted_local_maintenance"))
```

### 4. Run the linter

```bash
python scripts/check-tool-registry.py
```

### 5. Done!

Your tool automatically appears in:
- Internal project assistant tool list
- Scheduler tool list
- MCP `tools/list` (filtered by permission pack)
- Frontend tool catalog (`GET /api/v1/tools/catalog`)
- Tool linter checks

## Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | Yes | Unique tool name |
| `description` | str | Yes | Human-readable description |
| `input_schema` | dict | Yes | JSON Schema properties |
| `required` | list[str] | No | Required parameter names |
| `tool_type` | str | Yes | One of: read, analysis, web, memory, generator, write, scheduler |
| `risk_level` | str | Yes | One of: safe, low, medium, high, destructive |
| `writes_project_data` | bool | Yes | Whether tool modifies project DB |
| `idempotent` | bool | No | Whether calling twice with same args produces same result |
| `requires_confirmation` | bool | No | Whether tool requires explicit user confirmation |
| `estimated_cost` | str | No | One of: free, low, medium, high |
| `permission_tags` | set[str] | No | Tags for filtering (e.g., {"read", "search"}) |
| `expose_to_internal_agent` | bool | No | Available to internal project assistant |
| `expose_to_scheduler` | bool | No | Available to scheduled tasks |
| `expose_to_mcp` | bool | No | Available to MCP clients |
| `handler` | Callable | Yes | The async handler function |

## Permission Pack Assignment

Tools are automatically assigned to a permission pack based on their metadata:

| `tool_type` | `writes_project_data` | `risk_level` | Pack |
|-------------|----------------------|--------------|------|
| read/analysis/web | False | safe | `readonly_collaboration` |
| generator | False | low | `draft_generation` |
| write | True | safe/low/medium | `project_writing` |
| write | True | high | `project_management` |
| write | True | destructive | `trusted_local_maintenance` |
| write | False | any | `project_management` |
| scheduler | any | any | `project_management` |
| memory (read) | False | safe | `readonly_collaboration` |
| memory (write) | True | low | `draft_generation` |

## Common Mistakes

1. **Missing description** — Every tool must have a human-readable description.
2. **Missing handler** — Tools without handlers can't be executed.
3. **Wrong risk_level** — Don't mark destructive tools as "safe".
4. **Secret tools exposed** — Tools matching `*api_key*`, `*secret*`, etc. must have `expose_to_mcp=False`.
5. **Missing permission_tags** — Tags help with filtering and documentation.
6. **writes_project_data not set** — Defaults to False; set True for tools that modify DB.

## Running the Linter

The linter checks all registered tools for required metadata:

```bash
python scripts/check-tool-registry.py
```

Expected output:
```
[linter] Checking tool registry...
[linter] Total tools: 106

[linter] PASS — all tools have required metadata.
```

If there are issues:
```
[linter] FAIL — 2 issue(s) found:

  - my_tool: missing description
  - other_tool: invalid risk_level 'unknown'
```

## See Also

- [Permission Packs and Single Tool Source Spec](permission-packs-and-tools.md)
- [MCP Architecture Spec](spec.md)
- [Security Policy](security.md)
