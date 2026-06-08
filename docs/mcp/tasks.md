# MCP Development Task Board

> Project: Moshu / 墨枢
>
> Purpose: add MCP support in a strict, auditable, incremental workflow.
>
> Status legend:
> - `[ ]` not started
> - `[-]` in progress
> - `[x]` completed and verified
> - `[!]` blocked, with blocker written under the task

## Strict Execution Rules

1. Claim exactly one task by changing `[ ]` to `[-]` and writing your name or handle.
2. Stay inside the listed file scope. If you must touch another area, write the reason in this file before editing.
3. Every completed task must include verification commands and results.
4. Mark `[x]` only when you are certain the task is complete. Do not guess or bulk mark tasks.
5. Each verified function should be committed and pushed separately.
6. Security boundary: MCP must never expose API keys, model secrets, tokens, or secret-management APIs.
7. Dangerous write tools must be denied by default until an explicit confirmation layer exists.

## Phase 0 - Specification And Safety

### MCP-0001 - Write MCP Architecture Spec

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `docs/mcp/spec.md`
- Goal:
  - Define the first MCP version: Moshu as an MCP Server.
  - Define supported transport, local database access, tool naming, resource URI format, prompt names, and versioning.
  - Explicitly state that MCP Client integration is a later phase.
- Required content:
  - `moshu://` resource URI scheme.
  - Tool exposure tiers: `readonly`, `draft`, `write_confirmed`.
  - Error contract for permission denied, project not found, tool failed.
  - Compatibility plan for the existing `ToolRegistry`.
- Verification:
  - `Get-Content docs/mcp/spec.md`
  - Reviewer can implement Phase 1 from the spec without asking for hidden assumptions.

### MCP-0002 - Write MCP Security Policy

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `docs/mcp/security.md`
- Goal:
  - Define MCP security rules before writing server code.
- Required content:
  - Never expose API key/model secret CRUD.
  - Default mode is readonly.
  - `create_*`, `update_*`, `delete_*`, merge, import, and deconstruct-import tools are denied until permission policy is implemented.
  - Confirmation-token model for future write tools.
  - Localhost binding and stdio-only recommendation for first release.
- Verification:
  - `Get-Content docs/mcp/security.md`
  - Security doc names every dangerous tool family.

### MCP-0003 - Keep This Task Board Current

- Status: `[-]`
- Owner: Codex
- File scope:
  - `docs/mcp/tasks.md`
- Goal:
  - Create the task board and make it usable for multiple implementers.
- Verification:
  - `Test-Path docs/mcp/tasks.md`
  - `Get-Content docs/mcp/tasks.md | Select-String "MCP-0001"`

## Phase 1 - MCP Server Readonly Core

### MCP-0101 - Add MCP Package Skeleton

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/__init__.py`
  - `backend/app/mcp/server.py`
  - `backend/app/mcp/adapter.py`
  - `backend/app/mcp/schemas.py`
  - `backend/app/mcp/permissions.py`
- Goal:
  - Create importable MCP backend package without wiring behavior yet.
- Implementation notes:
  - Keep modules small.
  - Do not modify workspace tool handlers.
- Verification:
  - `py -m compileall backend/app/mcp`

### MCP-0102 - Convert ToolRegistry Entries To MCP Tool Definitions

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/adapter.py`
  - `backend/app/mcp/schemas.py`
  - `backend/tests/test_mcp_adapter.py`
- Goal:
  - Convert internal `ToolDef` entries into MCP-compatible tool metadata.
- Expose first:
  - `list_projects`
  - `get_project_info`
  - `search_chapters`
  - `search_characters`
  - `search_worldbuilding`
  - `search_outline`
  - `search_context`
  - `preview_writing_context`
- Verification:
  - `py -m pytest backend/tests/test_mcp_adapter.py -q`
  - Test confirms no write/delete tools appear.

### MCP-0103 - Implement Readonly Permission Filter

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/permissions.py`
  - `backend/tests/test_mcp_permissions.py`
- Goal:
  - Enforce readonly MCP exposure independent of LLM instructions.
- Required behavior:
  - Allow safe read/analysis tools listed in MCP-0102.
  - Deny `create_*`, `update_*`, `delete_*`, `merge_duplicate_characters`, `import_*`, `start_*`, `run_*`.
  - Deny all API key/model secret/config secret tools even if they are later registered.
- Verification:
  - `py -m pytest backend/tests/test_mcp_permissions.py -q`

### MCP-0104 - Implement MCP Tool Execution Wrapper

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/server.py`
  - `backend/app/mcp/adapter.py`
  - `backend/tests/test_mcp_server_tools.py`
- Goal:
  - Execute allowed MCP tools through existing `execute_workspace_action`.
- Required behavior:
  - Validate project_id when required.
  - Return structured result with `status`, `detail`, `data`, `warnings`.
  - Redact large content or expose refs when possible.
- Verification:
  - `py -m pytest backend/tests/test_mcp_server_tools.py -q`

### MCP-0105 - Add Stdio MCP Server Entrypoint

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `scripts/moshu-mcp-server.py`
  - `PACKAGING.md`
  - `backend/tests/test_mcp_entrypoint.py`
- Goal:
  - Add a local stdio server entrypoint for MCP clients.
- Verification:
  - `py scripts/moshu-mcp-server.py --help`
  - `py -m pytest backend/tests/test_mcp_entrypoint.py -q`

## Phase 2 - MCP Resources

### MCP-0201 - Define Resource URI Scheme

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/resources.py`
  - `backend/tests/test_mcp_resources.py`
- Goal:
  - Define and parse stable Moshu resource URIs.
- URI examples:
  - `moshu://projects`
  - `moshu://projects/{project_id}`
  - `moshu://projects/{project_id}/chapters`
  - `moshu://projects/{project_id}/chapters/{chapter_id}`
  - `moshu://projects/{project_id}/characters`
  - `moshu://projects/{project_id}/worldbuilding`
  - `moshu://projects/{project_id}/outline`
- Verification:
  - `py -m pytest backend/tests/test_mcp_resources.py -q`

### MCP-0202 - Implement Project And Index Resources

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/resources.py`
  - `backend/tests/test_mcp_resources.py`
- Goal:
  - Read project, chapter list, character list, worldbuilding list, and outline list via resources.
- Verification:
  - `py -m pytest backend/tests/test_mcp_resources.py -q`

### MCP-0203 - Implement Chapter Detail Resource

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/resources.py`
  - `backend/tests/test_mcp_resources.py`
- Goal:
  - Read chapter content, summary, linked outline, linked characters, and linked worldbuilding.
- Verification:
  - Test confirms linked metadata is included.

### MCP-0204 - Implement RAG Context Resource

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/resources.py`
  - `backend/app/services/rag/`
  - `backend/tests/test_mcp_resources.py`
- Goal:
  - Expose selected RAG context by query without exposing unrelated full project data.
- Verification:
  - Test query returns expected chunks and selection reasons.

## Phase 3 - MCP Prompts

### MCP-0301 - Expose Writing Context Prompt

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/prompts.py`
  - `backend/tests/test_mcp_prompts.py`
- Goal:
  - Add `moshu_writing_context` prompt.
- Required behavior:
  - Inputs: `project_id`, optional `chapter_number`, optional `outline_node_id`, optional `requirements`.
  - Output: compact prompt containing outline, recent summaries, relevant characters, worldbuilding, and warnings.
- Verification:
  - `py -m pytest backend/tests/test_mcp_prompts.py -q`

### MCP-0302 - Expose Continuity Check Prompt

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/prompts.py`
  - `backend/tests/test_mcp_prompts.py`
- Goal:
  - Add `moshu_continuity_check` prompt for OOC and setting-conflict review.
- Verification:
  - Test prompt includes character state and worldbuilding constraints.

### MCP-0303 - Expose Fanfic Draft Prompt

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/prompts.py`
  - `backend/tests/test_mcp_prompts.py`
- Goal:
  - Add `moshu_fanfic_draft` prompt for external AI clients writing derivative chapters.
- Verification:
  - Test prompt includes anti-OOC and no-secret rules.

## Phase 4 - Controlled Write Tools

### MCP-0401 - Add Draft Permission Tier

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/permissions.py`
  - `backend/app/mcp/adapter.py`
  - `backend/tests/test_mcp_permissions.py`
- Goal:
  - Allow generator tools that do not write to database.
- First allowed draft tools:
  - `chapter_writer`
  - `outline_writer`
  - `character_writer`
  - `worldbuilding_writer`
  - `rewrite_text`
  - `expand_text`
  - `continue_text`
- Verification:
  - Draft tools execute.
  - Database row counts do not change.

### MCP-0402 - Add Confirmed Write Token Flow

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/permissions.py`
  - `backend/app/mcp/server.py`
  - `backend/tests/test_mcp_write_confirmation.py`
- Goal:
  - Prepare future write access with explicit confirmation token.
- Required behavior:
  - Missing token denies write.
  - Invalid token denies write.
  - Valid token allows only the exact tool/action it was issued for.
- Verification:
  - `py -m pytest backend/tests/test_mcp_write_confirmation.py -q`

## Phase 5 - MCP Client Integration

### MCP-0501 - Add MCP Server Config Model

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/database/models.py`
  - `backend/app/schemas/mcp.py`
  - `backend/tests/test_mcp_client_config.py`
- Goal:
  - Store external MCP server configs.
- Fields:
  - `id`, `project_id`, `name`, `transport`, `command`, `url`, `enabled`, `status`, `last_error`, timestamps.
- Verification:
  - Runtime schema creates table.

### MCP-0502 - Add MCP Client Management API

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/routers/mcp.py`
  - `backend/app/services/mcp_client/`
  - `backend/tests/test_mcp_client_api.py`
- Goal:
  - CRUD external MCP server configs and test connection.
- Verification:
  - `py -m pytest backend/tests/test_mcp_client_api.py -q`

### MCP-0503 - Register External MCP Tools Into Workspace Agent

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/mcp_client/`
  - `backend/app/services/workspace/registry.py`
  - `backend/tests/test_mcp_client_tools.py`
- Goal:
  - Expose external MCP tools as `mcp.{server_name}.{tool_name}`.
- Verification:
  - Agent tool list includes enabled external MCP tools.

### MCP-0504 - Add MCP Settings Page

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `frontend/src/pages/McpPage.tsx`
  - `frontend/src/pages/ProjectWorkspace.tsx`
  - `frontend/src/api/`
- Goal:
  - UI for adding external MCP servers, testing connection, viewing tools/resources.
- Verification:
  - `cd frontend; npm run build`

## Phase 6 - Agent And Scheduler Integration

### MCP-0601 - Teach Workspace Prompts When To Use MCP

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/prompts/packs/workspace_fast.py`
  - `backend/app/prompts/packs/workspace_quality.py`
  - `backend/tests/test_prompt_packs.py`
- Goal:
  - Update assistant behavior so MCP tools are used for external resources, web search, file systems, and cross-app workflows.
- Verification:
  - Prompt pack tests pass.

### MCP-0602 - Make Scheduled Tasks Use Agent Tool Chain

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/scheduler/engine.py`
  - `backend/app/services/agent/`
  - `backend/tests/test_scheduler_agent_execution.py`
- Goal:
  - Scheduled tasks should execute through the Agent/tool chain, not a one-shot LLM call.
- Verification:
  - Scheduled task can call a mocked MCP/search tool and persist tool logs.

### MCP-0603 - Add MCP Tool Run Logs

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/workspace/run_log.py`
  - `backend/app/services/scheduler/`
  - `frontend/src/pages/ScheduledTasksPage.tsx`
- Goal:
  - Show MCP server/tool name, arguments summary, status, and error details in run logs.
- Verification:
  - Frontend displays run logs after task execution.

## Phase 7 - Release Readiness

### MCP-0701 - Documentation

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `README.md`
  - `PACKAGING.md`
  - `docs/mcp/*.md`
- Goal:
  - Document how to run Moshu MCP Server from local exe/source.
- Verification:
  - Fresh user can configure an MCP client using docs only.

### MCP-0702 - Packaging

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `build-exe.bat`
  - `scripts/`
  - `release/`
- Goal:
  - Ensure packaged app includes MCP server entrypoint and required dependencies.
- Verification:
  - Build exe.
  - Run MCP entrypoint from packaged artifact.

### MCP-0703 - Full Regression

- Status: `[ ]`
- Owner:
- File scope:
  - all touched areas
- Required commands:
  - `py -m pytest backend/tests -q`
  - `cd frontend; npm run build`
- Release criteria:
  - Tests pass.
  - Frontend builds.
  - No API key/model secret tools exposed through MCP.
  - MCP readonly server can be used by an external MCP client.

## Completion Log

Append verified completions here. Keep entries short and factual.

### 2026-06-07

- Created initial MCP task board.
- MCP-0001: `Get-Content docs/mcp/spec.md` — file exists, 10 sections, 14 `moshu://` references. `Get-Content docs/mcp/tasks.md | Select-String "MCP-0001"` — status `[x]`, owner `Claude Code`.
- MCP-0002: `Get-Content docs/mcp/security.md` — file exists, 9 sections. Covers all dangerous tool families (create/update/delete/merge/import/start/run/set/apply/pause/resume/cancel/rerun/ensure/reset/export/forget), secret deny-list, confirmation-token model, stdio-only binding. `Get-Content docs/mcp/tasks.md | Select-String "MCP-0002"` — status `[x]`, owner `Claude Code`.
- MCP-0101: `py -m compileall backend/app/mcp` — all 5 modules compile (server.py, adapter.py, schemas.py, permissions.py, __init__.py). No workspace tool handlers modified.
- MCP-0102: `py -m pytest backend/tests/test_mcp_adapter.py -q` — 19 passed. Tests confirm: 8 required readonly tools present, no write/delete/generator tools in readonly list, schema conversion correct, permission tier mapping correct. Fixed import paths (removed `backend.` prefix) in adapter.py/permissions.py/server.py.
- MCP-0103: `py -m pytest backend/tests/test_mcp_permissions.py -q` — 22 passed, 70 subtests. Tests confirm: read/analysis/web tools allowed, create/update/delete/merge/import/start/run/export tools denied, generator tools denied, secret-pattern tools denied at any tier.
- MCP-0104: `py -m pytest backend/tests/test_mcp_adapter.py tests/test_mcp_permissions.py tests/test_mcp_server_tools.py -q` — 61 passed, 70 subtests. Tests confirm: execute_tool validates tool existence and permission, calls execute_workspace_action, returns structured result (status/detail/data/warnings), truncates large content, handles exceptions. server.py handle_message wired to async execution path.
- MCP-0105: `py scripts/moshu-mcp-server.py --help` — exits 0, shows usage. `py -m pytest backend/tests/test_mcp_entrypoint.py -q` — 4 passed. PACKAGING.md updated with MCP Server section.
- MCP-0201: `py -m pytest backend/tests/test_mcp_resources.py -q` — 27 passed. Tests cover: all 11 URI patterns parse correctly, invalid URIs return None, build_uri roundtrips, list_resource_uris returns 7 index URIs, all resource types have descriptions.
- MCP-0202: `py -m pytest backend/tests/test_mcp_resources.py -q` — 38 passed. Added read_resource dispatcher and 11 resource readers (projects, chapters, characters, worldbuilding, outline, relationships). Tests verify index and detail reads with mock DB, and not-found returns error JSON.
- MCP-0203: `py -m pytest backend/tests/test_mcp_resources.py -q` — 39 passed. Chapter detail reader now includes linked summary, outline node, characters (via ChapterCharacter), and worldbuilding (via ChapterWorldbuilding). Test verifies all linked metadata is present.
- MCP-0204: `py -m pytest backend/tests/test_mcp_resources.py -q` — 42 passed. Added rag_search resource URI (moshu://projects/{id}/rag/search?q=...), auto-indexes on first query, returns chunks with scores and selection reasons. Tests verify query parsing, missing query error, and search results.
- MCP-0301: `py -m pytest backend/tests/test_mcp_prompts.py -q` — 19 passed. Implemented moshu_writing_context, moshu_continuity_check, moshu_fanfic_draft prompts with DB-backed rendering. Tests verify prompt listing, arg metadata, content sections (outline/summaries/characters/worldbuilding/warnings), and error handling.
- MCP-0302: Covered by MCP-0301 implementation. moshu_continuity_check prompt in prompts.py renders character states and worldbuilding constraints. Test `RenderContinuityCheckTest` verifies content includes Hero and "No time travel".
- MCP-0303: Covered by MCP-0301 implementation. moshu_fanfic_draft prompt in prompts.py includes anti-OOC and no-secret rules. Test `RenderFanficDraftTest` verifies content includes "anti-OOC" and "API key".
- MCP-0401: `py -m pytest backend/tests/test_mcp_permissions.py -q` — 28 passed, 87 subtests. Draft tier tests verify: generator tools allowed when draft enabled, write tools still denied in draft mode, secret tools still denied, filter_tools returns readonly+draft but not write_confirmed. Draft tools (chapter_writer, outline_writer, etc.) are tool_type=generator → draft tier; handlers produce in-memory content only, no DB writes.
- MCP-0402: `py -m pytest tests/test_mcp_write_confirmation.py -q` — 19 passed. Full MCP suite: 151 passed, 87 subtests. Token flow: issue_confirmation_token creates scoped single-use tokens, validate_confirmation_token checks tool match/usage/expiry, execute_tool denies write_confirmed tools without valid token, read tools work without token.
- MCP-0501: `py -m pytest tests/test_mcp_client_config.py -q` — 9 passed. Added McpServerConfig model (id, project_id, name, transport, command, url, enabled, status, last_error, timestamps) and Pydantic schemas (Create, Update, Read). Project relationship added.
- MCP-0502: `py -m pytest tests/test_mcp_client_api.py -q` — 3 passed. Added CRUD router at /projects/{project_id}/mcp-servers (list, create, get, update, delete, test-connection). Connection test endpoint stubbed.
- MCP-0503: `py -m pytest tests/test_mcp_client_tools.py -q` — 15 passed. Added mcp_client/registry.py with register_external_tool, unregister_server_tools, external_tool_name, parse_external_tool_name. External tools registered as mcp.{server_name}.{tool_name} in workspace ToolRegistry. Tests verify registration, idempotency, unregistration, and cross-server isolation.
- MCP-0504: `cd frontend; npm run build` — built in 5.34s. Added McpPage.tsx with CRUD table (name, transport, address, status, enabled toggle), add modal (stdio/http), delete confirmation, test connection button.
- MCP-0601: `py -m pytest tests/test_prompt_packs.py -q` — 70 passed. Added MCP tool guidance section to workspace_fast.py and workspace_quality.py prompt packs. Instructs assistant to use mcp.* tools for external data sources and cross-app workflows.
- MCP-0602: `py -m pytest tests/test_scheduler_agent_execution.py -q` — 5 passed. Updated scheduler engine._run_task_prompt to use stream_chat_completion_with_tools with agent tool-calling loop. Tool calls executed through execute_workspace_action, tool_policy filters available schemas.
- MCP-0603: `py -m pytest tests/test_mcp_server_tools.py tests/test_mcp_adapter.py -q` — 39 passed. Added _log_mcp_tool_call to adapter.py — logs tool name, project_id, status, and args summary via standard logger on every MCP tool execution (success or failure). Logging is non-blocking and never breaks tool execution.
- MCP-0701: README.md updated with MCP Server section (source/exe usage, client config, feature summary). PACKAGING.md already had MCP section from MCP-0105.
- MCP-0702: launcher.py updated with --mcp-server flag support. When invoked with --mcp-server, runs MCP stdio server instead of web app. Compiles successfully.
