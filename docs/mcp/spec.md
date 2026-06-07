# Moshu MCP Architecture Specification

> Version: 0.1.0 (draft)
> Date: 2026-06-07
> Status: Phase 0 — specification

## 1. Scope

This document defines the first MCP version for Moshu (墨枢). In this version:

- Moshu acts as an **MCP Server** only.
- MCP Client integration (connecting Moshu to external MCP servers) is a **later phase** and is out of scope for this spec.
- The server is **readonly by default**. Write and generator tools are gated behind explicit permission tiers and are not exposed in the initial release.

## 2. Transport

| Transport | Status | Notes |
|-----------|--------|-------|
| **stdio** | Supported (v1) | Recommended for local use with MCP clients such as Claude Desktop, Cursor, and other editors. The server reads JSON-RPC from stdin and writes to stdout. |
| Streamable HTTP | Deferred | May be added in a later version for remote access. Requires authentication and is not part of v1. |

The v1 entrypoint is a standalone Python script (`scripts/moshu-mcp-server.py`) that loads the Moshu database, registers tools, and serves over stdio.

## 3. Resource URI Scheme

All Moshu resources use the `moshu://` scheme. URIs are stable, hierarchical, and case-sensitive.

### 3.1 URI Patterns

| URI | Description |
|-----|-------------|
| `moshu://projects` | Index of all projects |
| `moshu://projects/{project_id}` | Single project metadata |
| `moshu://projects/{project_id}/chapters` | Chapter list for a project |
| `moshu://projects/{project_id}/chapters/{chapter_id}` | Single chapter with content |
| `moshu://projects/{project_id}/characters` | Character list for a project |
| `moshu://projects/{project_id}/characters/{character_id}` | Single character card |
| `moshu://projects/{project_id}/worldbuilding` | Worldbuilding entry list |
| `moshu://projects/{project_id}/worldbuilding/{entry_id}` | Single worldbuilding entry |
| `moshu://projects/{project_id}/outline` | Outline tree (titles and hierarchy) |
| `moshu://projects/{project_id}/outline/{node_id}` | Single outline node with summary |
| `moshu://projects/{project_id}/relationships` | Character relationships |

### 3.2 Resource Metadata

Each resource returns:
- `uri`: the canonical `moshu://` URI
- `name`: human-readable label
- `mimeType`: `application/json` for structured data, `text/plain` for prose content
- `contents`: the resource payload (JSON object or plain text)

## 4. Tool Permission Tiers

Every MCP-exposed tool belongs to exactly one tier. The server enforces tier gating independent of any LLM prompt.

### 4.1 Tier Definitions

| Tier | Prefix | Behavior | v1 Status |
|------|--------|----------|-----------|
| **readonly** | `read`, `search`, `list`, `get`, `preview`, `detect`, `evaluate`, `explain`, `suggest`, `design` | Read-only queries and analysis. No database mutations. | **Exposed in v1** |
| **draft** | `*_writer`, `rewrite_*`, `expand_*`, `continue_*`, `roleplay_*`, `dialogue_*` | Generator tools that produce content in-memory but do **not** write to the database. The caller is responsible for persisting results via a separate write call. | Gated in v1; implementation deferred to Phase 4 |
| **write_confirmed** | `create_*`, `update_*`, `delete_*`, `merge_*`, `import_*`, `start_*`, `run_*`, `set_*`, `apply_*`, `pause_*`, `resume_*`, `cancel_*`, `ensure_*`, `reset_*` | Tools that mutate the database. Require an explicit confirmation token issued per-invocation. | Denied in v1 until confirmation layer exists (Phase 4) |

### 4.2 Default Mode

The server starts in **readonly** mode. Only tools in the `readonly` tier are advertised via `tools/list`. Attempting to call a tool in a higher tier returns a permission-denied error.

### 4.3 Tool Type Mapping

The existing `ToolRegistry` uses a `tool_type` field on each `ToolDef`. The MCP adapter maps these to tiers:

| `tool_type` | MCP Tier |
|-------------|----------|
| `read` | readonly |
| `analysis` | readonly |
| `web` | readonly |
| `memory` | readonly (list/recall only; write memory is draft) |
| `generator` | draft |
| `write` | write_confirmed |
| `scheduler` | write_confirmed |

## 5. First Version: Exposed Readonly Tools

The following tools are exposed in v1. They are a strict subset of the existing `ToolRegistry`, filtered to `tool_type` values `read`, `analysis`, and `web`.

### 5.1 Project & System

| Tool | Description |
|------|-------------|
| `list_projects` | List all projects, optional search by title |
| `get_project_info` | Read project metadata and settings |
| `get_export_word_count` | Chapter word counts for a project |

### 5.2 Search & Catalog

| Tool | Description |
|------|-------------|
| `search_chapters` | Search chapters by title, with content preview |
| `search_characters` | Search characters by name fragment |
| `search_worldbuilding` | Search worldbuilding entries by title/dimension |
| `search_outline` | Search outline nodes by title or subtree |
| `search_outline_tree` | Get full outline tree structure |
| `search_relationships` | Query character relationships |
| `search_context` | Full-text RAG search across all indexed content |
| `list_characters` | Quick character name/ID overview |
| `list_chapters` | Quick chapter title/ID overview |
| `list_worldbuilding` | Quick worldbuilding title/ID/dimension overview |

### 5.3 Analysis & Preview

| Tool | Description |
|------|-------------|
| `preview_writing_context` | Pre-write context check: outline, summaries, characters, worldbuilding |
| `preview_rag_context` | RAG-aware context packing preview |
| `explain_context_selection` | Explain why sources were included/excluded from context |
| `evaluate_chapter` | 8-dimension chapter quality evaluation |
| `detect_character_changes` | Detect character state changes in text |
| `detect_new_worldbuilding` | Detect unrecorded worldbuilding in text |
| `detect_worldbuilding_conflicts` | Detect contradictions across worldbuilding entries |
| `detect_forbidden_patterns` | Rule-based forbidden phrase detection |
| `suggest_conflicts` | Generate plot conflict suggestions |
| `design_plot` | Design chapter plot with multi-dimensional analysis |

### 5.4 Cataloging & Deconstruct (Read-only parts)

| Tool | Description |
|------|-------------|
| `list_cataloging_jobs` | List cataloging jobs and progress |
| `get_cataloging_job` | Read a cataloging job with chapter runs |
| `list_cataloging_candidates` | List cataloging candidates for review |
| `list_cataloging_facts` | List saved cataloging facts |
| `preview_deconstruct_source` | Preview chapters before deconstruct |
| `list_deconstruct_reports` | List persisted deconstruct reports |
| `get_deconstruct_report` | Read a deconstruct report |

### 5.5 Skills & Stats (Read-only parts)

| Tool | Description |
|------|-------------|
| `list_skills` | List AI skills |
| `list_skill_templates` | List skill templates |
| `list_skill_tools` | List skill tool metadata |
| `list_skill_versions` | List skill version history |
| `preview_skill_match` | Preview skill matching for a message |
| `get_today_writing_stats` | Today's writing statistics |
| `get_writing_stats_history` | Writing stats history |

### 5.6 Memory (Read-only parts)

| Tool | Description |
|------|-------------|
| `recall` | Search saved memories |
| `list_memories` | List saved memories |

### 5.7 Web

| Tool | Description |
|------|-------------|
| `web_search` | Internet search for reference material |

## 6. Adapter: Relationship with Existing ToolRegistry

### 6.1 Architecture

```
MCP Client (Claude Desktop, Cursor, etc.)
        │  JSON-RPC (stdio)
        ▼
  ┌─────────────┐
  │  MCP Server  │  ← backend/app/mcp/server.py
  │  (protocol)  │
  └──────┬───────┘
         │
  ┌──────▼───────┐
  │  MCP Adapter  │  ← backend/app/mcp/adapter.py
  │  (filter +    │     reads from ToolRegistry
  │   transform)  │     applies permission filter
  └──────┬───────┘
         │
  ┌──────▼───────┐
  │  Permissions  │  ← backend/app/mcp/permissions.py
  │  (tier gate)  │     enforces readonly/draft/write_confirmed
  └──────┬───────┘
         │
  ┌──────▼───────┐
  │ ToolRegistry  │  ← backend/app/services/workspace/registry.py
  │  (existing)   │     single source of truth for tool metadata + handlers
  └──────────────┘
```

### 6.2 Key Design Decisions

1. **No duplication.** The MCP adapter reads `ToolDef` entries from the existing `ToolRegistry` singleton. It does not maintain a parallel tool list. When a new tool is registered in `registry.py`, it automatically becomes available to the MCP adapter (subject to permission filtering).

2. **Schema conversion.** The adapter converts `ToolDef` fields to MCP `Tool` format:
   - `ToolDef.name` → `Tool.name`
   - `ToolDef.description` → `Tool.description`
   - `ToolDef.input_schema` + `ToolDef.required` → `Tool.inputSchema` (JSON Schema object)

3. **Handler delegation.** The adapter calls the existing `execute_workspace_action` function (or equivalent) to run a tool. It does not import or call handlers directly. This ensures the same validation, error handling, and logging paths apply.

4. **Permission filter.** The `permissions.py` module sits between the adapter and the registry. It maintains a deny-list of tool name patterns and a per-tier allow-list. The filter is applied at `tools/list` time (so denied tools are never advertised) and again at `tools/call` time (defense in depth).

5. **No ToolRegistry modification.** The existing `ToolRegistry` class in `registry.py` is not modified. The MCP adapter is a read-only consumer of its API (`get_schemas`, `get`, `get_handler`, `all_names`).

### 6.3 Adapter Input/Output

When the MCP server receives a `tools/call` request:

1. Validate the tool name against the permission filter.
2. Look up the `ToolDef` in the registry.
3. Convert MCP `arguments` to the format expected by the handler.
4. Call the handler via the existing execution path.
5. Wrap the result in the MCP response format (see Section 7).

## 7. Error Contract

All errors returned by the Moshu MCP server follow a consistent structure.

### 7.1 Error Response Format

MCP errors use the standard JSON-RPC error object:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": <integer>,
    "message": "<human-readable description>",
    "data": {
      "tool": "<tool name, if applicable>",
      "reason": "<machine-readable reason code>"
    }
  }
}
```

### 7.2 Error Codes

| Code | Name | When Used |
|------|------|-----------|
| -32600 | Invalid Request | Malformed JSON-RPC request |
| -32601 | Method Not Found | Unknown MCP method |
| -32602 | Invalid Params | Missing or invalid tool arguments |
| -32603 | Internal Error | Unhandled exception in tool execution |
| -32000 | Tool Not Found | Tool name not in registry |
| -32001 | Permission Denied | Tool exists but is gated by permission tier |
| -32002 | Project Not Found | Required `project_id` does not match any project |
| -32003 | Tool Execution Failed | Handler raised an exception or returned an error |

### 7.3 Tool Result Format

Successful tool calls return:

```json
{
  "content": [
    {
      "type": "text",
      "text": "<JSON-encoded result>"
    }
  ],
  "isError": false
}
```

Failed tool calls return:

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"error\": \"<description>\", \"reason\": \"<code>\"}"
    }
  ],
  "isError": true
}
```

### 7.4 Security: Never Expose

The following must never appear in any MCP response, regardless of tool tier:

- API keys, model secrets, tokens, or credentials
- Database connection strings
- Internal file system paths outside the project scope
- Raw LLM prompts used for internal tool execution

## 8. Versioning

The MCP server identifies itself as:

```
name: "moshu"
version: "<moshu-app-version>"
```

The protocol version follows the MCP specification version supported by the server.

## 9. Implementation Phases

| Phase | Content | Depends On |
|-------|---------|------------|
| 0 | This spec, security policy | — |
| 1 | MCP server skeleton, readonly tools, stdio entrypoint | Phase 0 |
| 2 | Resource URIs (`moshu://`) | Phase 1 |
| 3 | Prompts (`moshu_writing_context`, etc.) | Phase 1 |
| 4 | Draft tier + write confirmation tokens | Phase 1, security policy |
| 5 | MCP Client integration (external servers) | Phase 1 |
| 6 | Agent/scheduler integration | Phase 1, 5 |
| 7 | Release readiness (docs, packaging, regression) | All |

## 10. Out of Scope for v1

- MCP Client mode (connecting to external MCP servers)
- Streamable HTTP transport
- Write tools of any kind
- Resource subscriptions
- Prompt templates (Phase 3)
- Authentication or multi-tenancy
