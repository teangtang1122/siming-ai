# Permission Packs And Single Tool Source Specification

> Version: 0.1.0 (draft)
> Date: 2026-06-09
> Status: Phase 9 — specification
> Depends on: docs/mcp/spec.md, docs/mcp/security.md, docs/mcp/external-agent-live-session.md

## 1. Overview

This document defines how Siming exposes one canonical workspace tool registry to all consumers: internal project assistant, scheduled tasks, MCP clients, external Agent live sessions, and frontend tool inspection.

### 1.1 Design Goals

1. **Register once, expose everywhere.** A new tool is added to `ToolRegistry` with metadata, and it automatically becomes available to internal Agent, scheduler, MCP, docs, and frontend.
2. **Configurable permissions.** External Agents (Claude Code, Codex) get tools based on permission packs configured per project.
3. **Local-first defaults.** Trusted local desktop clients start with API-free project read/write/management access so Siming works without per-call approval prompts. Secret tools and internal model-spend tools still require explicit opt-in and remain outside trusted local maintenance.
4. **No secret exposure.** API keys, model secrets, and credentials are permanently denied regardless of permission pack.

## 2. Permission Packs

Permission packs are named groups of tools that can be enabled/disabled per project for external Agent access.

### 2.1 Pack Definitions

| Pack | Intent | Default |
|------|--------|---------|
| `readonly_collaboration` | Read/search/context tools. Safe for any external client. | **Enabled** |
| `draft_generation` | Legacy compatibility pack. It must not expose Siming internal LLM tools. | Disabled |
| `project_writing` | API-free create/update tools for chapters, characters, outline, worldbuilding, external drafts, and external cataloging candidates. | **Enabled** |
| `project_management` | API-free project CRUD, import/export, scheduler, skill, MCP-server management. Does not imply internal LLM access. | **Enabled** |
| `internal_llm` | Explicit opt-in pack for tools that spend Siming's configured model API (`chapter_writer`, `start_cataloging_job`, etc.). | Disabled |
| `trusted_local_maintenance` | Dangerous maintenance tools (delete, merge, reset). Enabled by default for trusted local desktop clients. Does not imply internal LLM access. | **Enabled** |

### 2.2 Pack Assignment Rules

Every registered tool must belong to exactly one pack. The assignment is based on the tool's `tool_type` and risk characteristics:

| `tool_type` | Pack |
|-------------|------|
| `read` | `readonly_collaboration` |
| `analysis` | `readonly_collaboration` |
| `web` | `readonly_collaboration` |
| `memory` (read) | `readonly_collaboration` |
| `generator` | `internal_llm` |
| model-backed `analysis` | `internal_llm` |
| internal model job `write` | `internal_llm` |
| `memory` (write) | `project_writing` |
| `write` (create/update content) | `project_writing` |
| `write` (project/import/export/scheduler/skill) | `project_management` |
| `write` (delete/merge/reset) | `trusted_local_maintenance` |
| `scheduler` | `project_management` |

### 2.3 Pack Inclusion

Packs are intentionally non-linear. Internal model access is a separate opt-in
capability, not a side effect of project management or trusted maintenance.

| Selected Pack | Implied Packs |
|---------------|---------------|
| `readonly_collaboration` | `readonly_collaboration` |
| `draft_generation` | `readonly_collaboration`, `draft_generation` |
| `project_writing` | `readonly_collaboration`, `project_writing` |
| `project_management` | `readonly_collaboration`, `project_writing`, `project_management` |
| `internal_llm` | `readonly_collaboration`, `project_writing`, `project_management`, `internal_llm` |
| `trusted_local_maintenance` | `readonly_collaboration`, `project_writing`, `project_management`, `trusted_local_maintenance` |

Default external Agent rule: unless the user explicitly asks to use Siming's
internal API/model quota, tools in `internal_llm` must remain unavailable and
the Agent should use the API-free external workflows.

## 3. Permanent Deny-List

The following tools are **permanently denied** regardless of permission pack, trusted local mode, or any other configuration:

- API key CRUD (`*api_key*`)
- Model secret CRUD (`*secret*`, `*credential*`)
- Encryption key access (`*crypto_key*`, `*encryption*`)
- Credential export (`*export*credential*`, `*backup*key*`)
- Raw confirmation token access (`*token*` — only the token generation/validation functions, not tools that accept tokens as arguments)

These patterns are matched against tool names at registration time. If a tool matches a deny pattern, it cannot be exposed through MCP or external Agent permission packs.

## 4. Tool Metadata Contract

Every tool registered in `ToolRegistry` must include the following metadata:

### 4.1 Existing Fields (from Phase 1)

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Tool name (unique identifier) |
| `description` | str | Human-readable description |
| `input_schema` | dict | JSON Schema for input parameters |
| `required` | list[str] | Required parameter names |
| `tool_type` | str | One of: read, analysis, web, memory, generator, write, scheduler |
| `idempotent` | bool | Whether calling twice with same args produces same result |
| `requires_confirmation` | bool | Whether the tool requires explicit user confirmation |
| `estimated_cost` | str | One of: free, low, medium, high |
| `handler` | Callable | The async handler function |

### 4.2 New Fields (Phase 9)

| Field | Type | Description |
|-------|------|-------------|
| `permission_tags` | set[str] | Tags for permission pack assignment (e.g., {"read", "search", "context"}) |
| `risk_level` | str | One of: safe, low, medium, high, destructive |
| `writes_project_data` | bool | Whether the tool modifies project database records |
| `expose_to_internal_agent` | bool | Whether available to the internal project assistant |
| `expose_to_scheduler` | bool | Whether available to scheduled tasks |
| `expose_to_mcp` | bool | Whether available to MCP clients |
| `mcp_permission_pack` | str | The permission pack this tool belongs to for MCP access |

### 4.3 Derived Fields

The following are computed from the above metadata:

- **`mcp_permission_pack`** is derived from `tool_type`, `risk_level`, and `writes_project_data`:
  - `read`/`analysis`/`web`/`memory(read)` → `readonly_collaboration`
  - `generator`/`memory(write)` → `draft_generation`
  - `write` with `writes_project_data=True` and `risk_level` in {safe, low, medium} → `project_writing`
  - `write` with `writes_project_data=True` and `risk_level` in {high, destructive} → `trusted_local_maintenance`
  - `write` for project/import/export/scheduler/skill → `project_management`

## 5. Single Source Of Truth Rules

### 5.1 Adding a New Tool

To add a new tool that works everywhere:

1. **Implement the handler** in `backend/app/services/workspace/tools/`.
2. **Register metadata** in `backend/app/services/workspace/registry.py`:
   ```python
   _r(ToolDef(
       name="my_new_tool",
       description="What it does",
       input_schema={...},
       tool_type="read",  # or analysis, generator, write, etc.
       risk_level="safe",
       writes_project_data=False,
       expose_to_internal_agent=True,
       expose_to_scheduler=True,
       expose_to_mcp=True,
       handler=my_handler,
   ))
   ```
3. **Add focused tests** for the handler.
4. **Done.** The tool automatically appears in:
   - Internal Agent tool list
   - Scheduler tool list
   - MCP `tools/list` (filtered by permission pack)
   - Frontend tool catalog
   - Tool linter

### 5.2 No Manual Schema Lists

The following must always be derived from the registry, never maintained manually:

- `SEARCH_TOOL_SCHEMAS` → derived from `registry.list_for_internal_agent(tool_types={"read", "analysis", "web"})`
- `WRITE_TOOL_SCHEMAS` → derived from `registry.list_for_internal_agent(tool_types={"write", "generator", "scheduler"})`
- `ALL_TOOL_SCHEMAS` → derived from `registry.list_for_internal_agent()`
- `SEARCH_TOOL_NAMES` → derived from `registry.list_for_internal_agent(tool_types={"read", "analysis", "web"})`
- `WRITE_TOOL_NAMES` → derived from `registry.list_for_internal_agent(tool_types={"write", "generator", "scheduler"})`

### 5.3 Backward Compatibility

Existing callers that use `tool_type` for filtering continue to work. The new metadata fields are additive.

## 6. Trusted Local Mode

Trusted local mode allows external Agents to use more powerful tools without per-call confirmation.

### 6.1 Requirements

Trusted local mode requires **all** of:

1. **Desktop default.** New installs enable it by default for local desktop clients; users may disable it from global/project external Agent settings.
2. **Local transport.** The MCP client must connect via stdio or from a localhost-only address.
3. **Client allow-list.** The client name must be in the project's trusted client allow-list (e.g., ["claude-code", "codex"]).

### 6.2 Behavior

When trusted local mode is active:

- `project_writing` tools work without confirmation tokens.
- `project_management` tools become available.
- `trusted_local_maintenance` tools work without confirmation prompts for trusted local clients.
- Every elevated call is logged as an audit event.

### 6.3 Risks

- Trusted local mode bypasses the confirmation-token flow for project writes.
- It should only be enabled on machines the user controls.
- The UI must display a clear warning when enabled.

## 7. Settings Model

Per-project external Agent settings:

```python
class ExternalAgentSettings(Base):
    __tablename__ = "external_agent_settings"

    id = Column(String(36), primary_key=True)
    project_id = Column(String(36), ForeignKey("projects.id"), unique=True)
    enabled_packs = Column(JSON, default=["readonly_collaboration"])
    trusted_local_enabled = Column(Boolean, default=False)
    trusted_local_clients = Column(JSON, default=[])  # ["claude-code", "codex"]
    require_confirmation_for_writes = Column(Boolean, default=True)
    require_confirmation_for_destructive = Column(Boolean, default=True)
    updated_at = Column(DateTime)
```

### 7.1 Defaults

| Setting | Default |
|---------|---------|
| `enabled_packs` | `["readonly_collaboration"]` |
| `trusted_local_enabled` | `False` |
| `trusted_local_clients` | `[]` |
| `require_confirmation_for_writes` | `True` |
| `require_confirmation_for_destructive` | `True` |

## 8. MCP Adapter Integration

### 8.1 tools/list

When a project_id is available:

1. Load project's `ExternalAgentSettings`.
2. Determine enabled packs (including hierarchy).
3. Filter tools by `mcp_permission_pack ∈ enabled_packs`.
4. If trusted local mode is active, add `project_management` tools.
5. Always exclude permanent deny-list tools.

When no project_id is available:

- Only expose globally safe telemetry/read tools.

### 8.2 tools/call

1. Check tool exists in registry.
2. Check tool's `expose_to_mcp` flag.
3. Check tool's `mcp_permission_pack` against project's enabled packs.
4. Check permanent deny-list.
5. If `writes_project_data` and not trusted local, require confirmation token.
6. Execute and log.

## 9. Tool Catalog API

### 9.1 Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/tools/catalog` | List all registered tools with metadata |
| `GET` | `/api/v1/projects/{project_id}/tools/exposed` | List tools exposed to external Agent for this project |

### 9.2 Response Fields

```json
{
  "name": "search_chapters",
  "description": "Search chapters by title",
  "tool_type": "read",
  "permission_tags": ["read", "search"],
  "risk_level": "safe",
  "writes_project_data": false,
  "expose_to_internal_agent": true,
  "expose_to_scheduler": true,
  "expose_to_mcp": true,
  "mcp_permission_pack": "readonly_collaboration",
  "requires_confirmation": false,
  "denied_reason": null
}
```

## 10. Tool Linter

A linter script (`scripts/check-tool-registry.py`) verifies:

- Every registered tool has all required metadata fields.
- No secret-looking tool is exposed to MCP.
- Tools that are internal-only have an explicit reason.
- Permission pack assignments are valid.

## 11. Backward Compatibility

- Existing `tool_type` field continues to work for all existing callers.
- New metadata fields have sensible defaults derived from `tool_type`.
- Existing MCP permission filter (`is_allowed`) continues to work.
- Existing external Agent tools (Phase 8) continue to work.
- Existing confirmation-token flow continues to work.

## 12. Out of Scope

- Dynamic permission changes at runtime (settings are loaded at tool-call time).
- Per-user permission packs (permissions are per-project).
- Tool versioning or hot-reloading.
- Cross-project tool access.
