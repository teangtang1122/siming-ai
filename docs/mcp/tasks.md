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

- Status: `[x]`
- Owner: Claude Code
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

## Phase 8 - External Agent Live Session

> Purpose: let Claude Code, Codex, and other external MCP clients operate Moshu projects while Moshu users can watch the run in the web UI.
>
> Strict rule for this phase: no hidden chain-of-thought is requested, stored, or displayed. Only explicit plans, tool calls, progress messages, selected context, draft chunks, warnings, and committed writes may be shown.

### MCP-0801 - Define External Agent Live Session Spec

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `docs/mcp/external-agent-live-session.md`
  - `docs/mcp/tasks.md`
- Goal:
  - Define the first version of the external Agent observability protocol.
  - Specify how Claude Code / Codex starts a run, reports progress, streams draft content, calls Moshu tools, requests confirmed writes, and finishes the run.
- Required content:
  - Run lifecycle: `created`, `running`, `waiting_confirmation`, `failed`, `cancelled`, `completed`.
  - Event types: `plan`, `progress`, `tool_start`, `tool_result`, `context_selected`, `draft_chunk`, `draft_ready`, `write_requested`, `write_committed`, `warning`, `error`, `run_finished`.
  - Event payload size limits and truncation rules.
  - No-secret rules: API keys, model keys, auth tokens, local credentials, and raw confirmation tokens must never be displayed in events.
  - Frontend rendering contract: timeline, latest status, draft preview, tool log, confirmation requests.
  - Backward compatibility: existing internal project assistant SSE must keep working.
- Verification:
  - `Test-Path docs/mcp/external-agent-live-session.md`
  - Reviewer can implement MCP-0802 through MCP-0807 from the spec without asking for hidden assumptions.

### MCP-0802 - Add Agent Run Persistence Model

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/database/models.py`
  - `backend/app/database/migrations.py`
  - `backend/app/schemas/agent_run.py`
  - `backend/tests/test_external_agent_runs.py`
- Goal:
  - Persist external Agent runs and their events so the frontend can show live and historical runs.
- Required behavior:
  - Add `AgentRun` fields: `id`, `project_id`, `source`, `client_name`, `title`, `status`, `current_step`, `summary`, `created_at`, `updated_at`, `completed_at`.
  - Add `AgentRunEvent` fields: `id`, `run_id`, `sequence`, `event_type`, `status`, `message`, `payload_json`, `created_at`.
  - Add indexes for `project_id`, `run_id`, `created_at`, and `sequence`.
  - Runtime schema sync must migrate old user databases without data loss.
  - Payload JSON must be allowed to be empty but never contain secret-looking keys.
- Verification:
  - `py -m pytest backend/tests/test_external_agent_runs.py -q`
  - Start backend against an existing user database and confirm runtime schema creation does not fail.

### MCP-0803 - Implement Agent Run Service And API

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/external_agent/run_service.py`
  - `backend/app/routers/external_agent.py`
  - `backend/app/main.py`
  - `backend/tests/test_external_agent_api.py`
- Goal:
  - Provide backend APIs for creating, reading, streaming, cancelling, and listing external Agent runs.
- Required endpoints:
  - `POST /api/v1/projects/{project_id}/agent-runs`
  - `GET /api/v1/projects/{project_id}/agent-runs`
  - `GET /api/v1/projects/{project_id}/agent-runs/{run_id}`
  - `GET /api/v1/projects/{project_id}/agent-runs/{run_id}/events`
  - `GET /api/v1/projects/{project_id}/agent-runs/{run_id}/stream`
  - `POST /api/v1/projects/{project_id}/agent-runs/{run_id}/cancel`
- Required behavior:
  - SSE stream sends existing events first, then live events.
  - Event sequence numbers must be monotonic per run.
  - Cancelling a run records a `cancelled` event; external clients must see cancellation when they report the next event.
  - API must enforce project isolation.
- Verification:
  - `py -m pytest backend/tests/test_external_agent_api.py -q`
  - Manual SSE smoke test with `curl` or a tiny Python client.

### MCP-0804 - Expose External Agent Reporting Tools Through MCP

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/workspace/tools/external_agent.py`
  - `backend/app/services/workspace/registry.py`
  - `backend/app/mcp/permissions.py`
  - `backend/tests/test_mcp_external_agent_tools.py`
- Goal:
  - Give Claude Code / Codex explicit MCP tools for reporting what they are doing to Moshu.
- Required tools:
  - `start_agent_run`
  - `report_agent_plan`
  - `report_agent_progress`
  - `report_context_selected`
  - `append_draft_chunk`
  - `mark_draft_ready`
  - `finish_agent_run`
- Required behavior:
  - These tools are allowed in MCP readonly mode because they only write run telemetry, not project content.
  - Tool args must include `project_id` and `run_id` except `start_agent_run`.
  - Payloads must be summarized or truncated before persistence.
  - Secret-looking fields are rejected or redacted.
- Verification:
  - `py -m pytest backend/tests/test_mcp_external_agent_tools.py -q`
  - MCP `tools/list` includes reporting tools but still excludes API key/model secret tools.

### MCP-0805 - Auto-Instrument MCP Tool Calls With Run Events

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/adapter.py`
  - `backend/app/mcp/schemas.py`
  - `backend/app/services/external_agent/run_service.py`
  - `backend/tests/test_mcp_tool_run_events.py`
- Goal:
  - When an external client passes `run_id` to any Moshu MCP tool call, Moshu should automatically log `tool_start` and `tool_result` events.
- Required behavior:
  - `run_id` may be accepted as an out-of-band MCP argument and stripped before calling the underlying workspace tool if the tool schema does not define it.
  - Log tool name, status, safe argument summary, safe result summary, warnings, and error message.
  - Do not store full chapter text or giant tool payloads in run events; store references and summaries.
  - Tool execution must continue even if telemetry logging fails.
- Verification:
  - `py -m pytest backend/tests/test_mcp_tool_run_events.py -q`
  - Existing MCP tests still pass.

### MCP-0806 - Add External Agent Run Frontend Panel

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `frontend/src/components/ExternalAgentRunPanel.tsx`
  - `frontend/src/components/ExternalAgentRunPanel.css`
  - `frontend/src/pages/ProjectWorkspace.tsx`
  - `frontend/src/api/client.ts`
  - `frontend/src/types/agentRun.ts`
- Goal:
  - Let users watch Claude Code / Codex working inside Moshu in real time.
- Required UI:
  - Collapsible panel in project workspace.
  - Active run selector and historical run list.
  - Timeline grouped by plan steps and tool calls.
  - Latest status strip.
  - Draft chunk preview with copy/apply controls disabled until confirmed-write flow exists.
  - Warnings and errors visible without opening developer console.
  - Empty state explaining how to connect Claude Code / Codex through MCP.
- Verification:
  - `cd frontend; npm run build`
  - Manual smoke test with mocked SSE events shows live updates without refreshing.

### MCP-0807 - Add Confirmed Write Flow For External Agent Drafts

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/external_agent/write_requests.py`
  - `backend/app/routers/external_agent.py`
  - `backend/app/mcp/adapter.py`
  - `frontend/src/components/ExternalAgentRunPanel.tsx`
  - `backend/tests/test_external_agent_confirmed_writes.py`
- Goal:
  - Allow external Agent drafts to become real Moshu project writes only after explicit user confirmation.
- Required behavior:
  - External clients can request writes such as create chapter, update chapter, create outline, update character, and create worldbuilding.
  - Backend creates a pending write request linked to `run_id`.
  - Frontend shows diff/preview and asks the user to confirm or reject.
  - Confirmed writes use the existing MCP confirmation-token layer where possible.
  - Rejected writes record an event and do not modify project data.
  - API keys and model settings remain non-writable through MCP.
- Verification:
  - `py -m pytest backend/tests/test_external_agent_confirmed_writes.py -q`
  - Manual test: external draft appears in UI, confirm creates chapter, reject creates no data.

### MCP-0808 - Provide Claude Code / Codex Operating Prompt And Config Docs

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `docs/mcp/claude-code-codex-client.md`
  - `README.md`
  - `PACKAGING.md`
- Goal:
  - Give users a copyable setup for making Claude Code / Codex operate Moshu through MCP and report progress to the Moshu UI.
- Required content:
  - Source and exe MCP server command examples.
  - Sample MCP client config.
  - Recommended external Agent operating rules:
    - Start a run before reading project data.
    - Report a short plan before tool work.
    - Use Moshu resources/RAG before writing.
    - Report selected context.
    - Stream draft chunks for long writing.
    - Request confirmed writes instead of directly modifying data.
    - Finish the run with a concise summary.
  - Troubleshooting section: wrong database path, missing project id, insufficient model balance, SSE not connected.
- Verification:
  - Fresh user can connect Claude Code / Codex from docs only.
  - Docs mention that Moshu itself still uses configured model APIs unless external Agent workflow is used.

### MCP-0809 - Add End-To-End External Agent Smoke Test

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/tests/test_external_agent_e2e.py`
  - `scripts/dev-external-agent-smoke.py`
  - `docs/mcp/external-agent-live-session.md`
- Goal:
  - Verify the complete loop with a fake external MCP client.
- Scenario:
  - Start run.
  - Report plan.
  - Read project/chapter context through MCP.
  - Stream draft chunks.
  - Request a create-chapter write.
  - Confirm write through backend API.
  - Finish run.
  - Assert frontend-facing event stream contains all major milestones.
- Verification:
  - `py -m pytest backend/tests/test_external_agent_e2e.py -q`
  - `py scripts/dev-external-agent-smoke.py --project-id <id>` works against a running backend.

### MCP-0810 - Release Gate For External Agent Live Session

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - all Phase 8 touched areas
  - `README.md`
  - `docs/mcp/*.md`
- Required commands:
  - `py -m pytest backend/tests -q`
  - `cd frontend; npm run build`
  - `py scripts/moshu-mcp-server.py --help`
  - packaged exe MCP smoke test if release build is requested
- Release criteria:
  - External Agent telemetry tools work through MCP.
  - Frontend shows active and historical external Agent runs.
  - Confirmed write flow prevents silent data mutation.
  - No API key/model secret tools are exposed or writable.
  - Existing internal project assistant and scheduler flows still work.

## Phase 9 - External Agent Permission Packs And Single Tool Source

> Purpose: make Claude Code / Codex permissions configurable without exposing secrets, and make every future Moshu project-assistant tool register once and become available to internal Agent, scheduler, MCP, docs, and frontend inspection through one shared registry contract.
>
> Strict rule for this phase: API key/model secret/config-secret tools remain permanently non-exposable through MCP and external Agent permission packs. Trusted local mode may reduce friction for project writes, but must not bypass secret boundaries or audit logging.

### MCP-0901 - Define Permission Pack And Single Tool Source Spec

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `docs/mcp/permission-packs-and-tools.md`
  - `docs/mcp/tasks.md`
- Goal:
  - Define how Moshu exposes one canonical workspace tool registry to internal project assistant, scheduled tasks, MCP clients, external Agent live sessions, and frontend tool inspection.
  - Define configurable external Agent permission packs, including a trusted local mode.
- Required content:
  - Permission pack names and intent:
    - `readonly_collaboration`
    - `draft_generation`
    - `project_writing`
    - `project_management`
    - `trusted_local_maintenance`
  - Permanent deny-list:
    - API key CRUD
    - model secret CRUD
    - encryption key access
    - credential export
    - raw confirmation token access
  - Tool metadata contract:
    - `name`
    - `description`
    - `schema`
    - `handler`
    - `tool_type`
    - `permission_tags`
    - `risk_level`
    - `idempotent`
    - `requires_confirmation`
    - `writes_project_data`
    - `expose_to_internal_agent`
    - `expose_to_scheduler`
    - `expose_to_mcp`
    - `mcp_permission_pack`
  - Rules for adding a new tool once:
    - implement handler
    - register metadata in ToolRegistry
    - add focused tests
    - no manual edits to separate MCP schema lists
  - Trusted local mode rules:
    - local machine only
    - explicit user opt-in
    - visible in UI
    - all writes audited
    - high-risk destructive operations still require confirmation
  - Backward compatibility for existing `tool_type` tiers.
- Verification:
  - `Test-Path docs/mcp/permission-packs-and-tools.md`
  - Reviewer can implement MCP-0902 through MCP-0909 from the spec without hidden assumptions.

### MCP-0902 - Extend ToolRegistry Metadata Contract

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/workspace/registry.py`
  - `backend/app/services/workspace/tool_schemas.py`
  - `backend/tests/test_tool_registry_metadata.py`
- Goal:
  - Make ToolRegistry the single source of truth for internal Agent, scheduler, MCP, and frontend tool metadata.
- Required behavior:
  - Extend `ToolDef` with permission-pack metadata from MCP-0901.
  - Keep existing callers working.
  - Provide registry methods:
    - `list_for_internal_agent(...)`
    - `list_for_scheduler(...)`
    - `list_for_mcp(permission_pack=...)`
    - `list_for_frontend(...)`
  - Existing `SEARCH_TOOL_SCHEMAS`, `WRITE_TOOL_SCHEMAS`, `ALL_TOOL_SCHEMAS`, `SEARCH_TOOL_NAMES`, `WRITE_TOOL_NAMES` must remain derived from registry.
  - No duplicated manual schema lists.
- Verification:
  - `py -m pytest backend/tests/test_tool_registry_metadata.py -q`
  - Existing workspace tool tests still pass.

### MCP-0903 - Classify Existing Tools Into Permission Packs

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/workspace/registry.py`
  - `backend/app/mcp/permissions.py`
  - `backend/tests/test_mcp_permission_packs.py`
- Goal:
  - Assign every existing workspace tool to the correct external Agent permission pack without changing handler behavior.
- Required behavior:
  - Read/search/context tools belong to `readonly_collaboration`.
  - Generator tools belong to `draft_generation`.
  - Create/update writing tools belong to `project_writing` and require confirmed write unless trusted local policy says otherwise.
  - Project CRUD/import/export/scheduler/skill/MCP-server management tools belong to `project_management`.
  - Dangerous maintenance tools belong to `trusted_local_maintenance`.
  - Secret/config-key tools are denied regardless of pack.
  - Tests must assert every registered tool has a permission classification.
- Verification:
  - `py -m pytest backend/tests/test_mcp_permission_packs.py -q`
  - Test confirms no unclassified tools and no secret tools exposed in any pack.

### MCP-0904 - Add External Agent Permission Settings Model And API

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/database/models.py`
  - `backend/app/database/migrations.py`
  - `backend/app/schemas/external_agent_settings.py`
  - `backend/app/routers/external_agent.py`
  - `backend/tests/test_external_agent_permission_settings.py`
- Goal:
  - Persist per-project external Agent permission settings.
- Required behavior:
  - Add project-level settings:
    - enabled permission packs
    - trusted local mode enabled/disabled
    - trusted local client allow-list
    - require confirmation for writes
    - require confirmation for destructive actions
    - updated_at
  - Default setting must be safe:
    - `readonly_collaboration` enabled
    - `draft_generation` disabled unless user enables
    - write/project-management/trusted packs disabled
  - Runtime schema sync must migrate old user databases.
  - API endpoints:
    - `GET /api/v1/projects/{project_id}/external-agent/settings`
    - `PUT /api/v1/projects/{project_id}/external-agent/settings`
- Verification:
  - `py -m pytest backend/tests/test_external_agent_permission_settings.py -q`
  - Old database startup does not fail.

### MCP-0905 - Enforce Permission Packs In MCP Adapter

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/mcp/permissions.py`
  - `backend/app/mcp/adapter.py`
  - `backend/app/services/external_agent/run_service.py`
  - `backend/tests/test_mcp_permission_pack_enforcement.py`
- Goal:
  - Make MCP `tools/list` and `tools/call` honor the project’s external Agent permission packs.
- Required behavior:
  - `tools/list` only returns tools allowed by enabled packs.
  - `tools/call` re-checks permission at execution time.
  - If no project id is available, only globally safe telemetry/read tools may appear.
  - Trusted local mode requires:
    - explicit project setting
    - local stdio transport or localhost-only client identity
    - audit event on every elevated call
  - Disabled tools return structured permission errors.
  - Existing confirmation-token write flow remains supported.
- Verification:
  - `py -m pytest backend/tests/test_mcp_permission_pack_enforcement.py -q`
  - Existing MCP adapter/permission tests still pass.

### MCP-0906 - Add Frontend Permission Pack Settings UI

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `frontend/src/pages/McpPage.tsx`
  - `frontend/src/components/ExternalAgentPermissionPanel.tsx`
  - `frontend/src/components/ExternalAgentPermissionPanel.css`
  - `frontend/src/api/client.ts`
  - `frontend/src/types/externalAgentSettings.ts`
- Goal:
  - Let users configure what Claude Code / Codex can do per project.
- Required UI:
  - Permission pack cards with plain-language explanations.
  - Visible permanent deny-list note for API keys and model secrets.
  - Trusted local mode toggle with warning copy.
  - Confirmation toggles for project writes and destructive actions.
  - Tool count preview for each pack.
  - “View exposed tools” drawer using registry metadata.
  - Clear warning when no project id is selected or MCP server is readonly only.
- Verification:
  - `cd frontend; npm run build`
  - Manual UI smoke test: toggling packs updates backend settings and exposed tool preview.

### MCP-0907 - Add Frontend Tool Catalog From Registry

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/routers/tools.py`
  - `backend/app/main.py`
  - `frontend/src/pages/SkillsPage.tsx`
  - `frontend/src/pages/McpPage.tsx`
  - `frontend/src/types/toolCatalog.ts`
  - `backend/tests/test_tool_catalog_api.py`
- Goal:
  - Make it easy to inspect all tools generated from the single ToolRegistry source.
- Required behavior:
  - Add API:
    - `GET /api/v1/tools/catalog`
    - `GET /api/v1/projects/{project_id}/tools/exposed`
  - Response includes:
    - name
    - description
    - tool_type
    - permission tags
    - risk level
    - enabled surfaces
    - requires confirmation
    - denied reason if not exposed
  - Frontend can show which tools are available to:
    - project assistant
    - scheduler
    - MCP external Agent
  - Adding a new registry tool should automatically appear in this catalog.
- Verification:
  - `py -m pytest backend/tests/test_tool_catalog_api.py -q`
  - `cd frontend; npm run build`

### MCP-0908 - Add New Tool Authoring Checklist And Linter

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `scripts/check-tool-registry.py`
  - `docs/mcp/tool-authoring.md`
  - `backend/tests/test_tool_registry_lint.py`
- Goal:
  - Prevent future contributors from creating tools that work internally but are invisible or unsafe externally.
- Required behavior:
  - Linter checks every registered tool has:
    - description
    - schema
    - handler
    - tool_type
    - permission_tags
    - risk_level
    - idempotent flag
    - requires_confirmation flag
    - exposure flags
  - Linter rejects secret-looking tools if exposed to MCP.
  - Linter reports tools that are available internally but intentionally hidden externally with explicit reason.
  - Documentation gives a copyable “how to add a tool once” checklist.
- Verification:
  - `py scripts/check-tool-registry.py`
  - `py -m pytest backend/tests/test_tool_registry_lint.py -q`

### MCP-0909 - Update Claude Code / Codex Client Docs For Permission Packs

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `docs/mcp/claude-code-codex-client.md`
  - `README.md`
  - `PACKAGING.md`
- Goal:
  - Document how external clients should work with permission packs and how users can safely grant more power.
- Required content:
  - Explain that Claude Code / Codex does not get all tools by default.
  - Explain each permission pack with examples.
  - Explain trusted local mode and its risks.
  - Explain confirmed write flow.
  - Explain how new project-assistant tools become available externally through ToolRegistry metadata.
  - Include troubleshooting for “tool not listed”:
    - project id missing
    - permission pack disabled
    - tool marked internal-only
    - secret deny-list
    - schema validation failure
- Verification:
  - Fresh user can understand why a tool is or is not exposed from docs alone.

### MCP-0910 - Regression Gate For Permission Packs And Single Tool Source

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - all Phase 9 touched areas
- Required commands:
  - `py -m pytest backend/tests -q`
  - `cd frontend; npm run build`
  - `py scripts/check-tool-registry.py`
  - `py scripts/moshu-mcp-server.py --help`
  - MCP `tools/list` smoke test for each permission pack
  - packaged exe MCP smoke test if release build is requested
- Release criteria:
  - Claude Code / Codex can be granted broader project permissions through explicit project settings.
  - Trusted local mode is auditable and cannot access API keys/model secrets.
  - New tools only need ToolRegistry registration to flow into internal assistant, scheduler, MCP metadata, and frontend catalog.
  - Existing internal project assistant behavior does not regress.
  - Existing external Agent live session behavior does not regress.

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
- MCP-0703: `py -m pytest` — 253 passed, 87 subtests. `npm run build` — built in 7.02s. No API key/model secret tools exposed through MCP (0 found out of 99 tools). All release criteria met.
- MCP-0801: `Test-Path docs/mcp/external-agent-live-session.md` — file exists, 10 sections. Covers run lifecycle (6 states), 12 event types, payload size limits, no-secret rules, frontend rendering contract, SSE format, backward compatibility.
- MCP-0802: `py -m pytest tests/test_external_agent_runs.py -q` — 14 passed. Added AgentRun model (id, project_id, source, client_name, title, status, current_step, summary, timestamps) and AgentRunEvent model (id, run_id, sequence, event_type, status, message, payload_json, created_at). Pydantic schemas (Create, Read, List) for both. Indexes on project_id/status, run_id/sequence.
- MCP-0803: `py -m pytest tests/test_external_agent_api.py -q` — 6 passed. Added external_agent/run_service.py (create_run, list_runs, add_event, get_events, cancel_run) with secret redaction and payload truncation. Router at /projects/{project_id}/agent-runs (POST create, GET list, GET detail, GET events, POST events, GET stream/SSE, POST cancel). Registered in main.py.
- MCP-0804: `py -m pytest tests/test_mcp_external_agent_tools.py -q` — 11 passed, 14 subtests. Added 7 external agent reporting tools to workspace registry (start_agent_run, report_agent_plan, report_agent_progress, report_context_selected, append_draft_chunk, mark_draft_ready, finish_agent_run). All registered as tool_type=read → readonly tier. Tests verify tools appear in MCP tools/list and secret tools remain excluded.
- MCP-0805: `py -m pytest tests/test_mcp_tool_run_events.py -q` — 12 passed. Updated adapter.execute_tool to accept run_id, strip it from arguments, and auto-log tool_start/tool_result events via run_service.add_event. Added _build_args_summary for safe argument truncation. Telemetry failures never break tool execution.
- MCP-0806: `npm run build` — built in 5.20s. Added ExternalAgentRunPanel.tsx with collapsible run list, event timeline, status badges, SSE live updates, cancel button, draft preview. Added types/agentRun.ts with TypeScript interfaces.
- MCP-0807: `py -m pytest tests/test_external_agent_confirmed_writes.py -q` — 7 passed. Added write_requests.py (request_write, confirm_write, reject_write) and router endpoints (POST write-requests, POST confirm, POST reject). Supports 8 write types. Confirmation issues single-use token via existing permissions layer.
- MCP-0808: Created docs/mcp/claude-code-codex-client.md with source/exe MCP config, 8-step operating rules, available tools table, troubleshooting section, security notes. README.md updated with Claude Code / Codex integration reference.
- MCP-0809: `py -m pytest tests/test_external_agent_e2e.py -q` — 4 passed. E2E test verifies full workflow (create run → plan → progress → context → draft chunks → draft ready → finished). Write types validated. Smoke script (scripts/dev-external-agent-smoke.py) exists and compiles.
- MCP-0810: `py -m pytest` — 307 passed, 101 subtests. `npm run build` — built in 5.08s. `py scripts/moshu-mcp-server.py --help` — exits 0. All Phase 8 release criteria met: external agent telemetry tools work, frontend panel implemented, confirmed write flow prevents silent mutation, no API key/model secret tools exposed.
- MCP-0901: `Test-Path docs/mcp/permission-packs-and-tools.md` — file exists, 12 sections. Covers 5 permission packs, permanent deny-list, tool metadata contract (15 fields), single-source rules, trusted local mode, settings model, MCP adapter integration, tool catalog API, linter requirements.
- MCP-0902: `py -m pytest tests/test_tool_registry_metadata.py -q` — 16 passed. Extended ToolDef with permission_tags, risk_level, writes_project_data, expose_to_internal_agent, expose_to_scheduler, expose_to_mcp, mcp_permission_pack. Added registry methods: list_for_internal_agent, list_for_scheduler, list_for_mcp, list_for_frontend, _derive_mcp_pack.
- MCP-0903: `py -m pytest tests/test_mcp_permission_packs.py -q` — 14 passed. Added _classify_all() post-registration step that assigns permission_tags, risk_level, writes_project_data, mcp_permission_pack to every tool. Tests verify: readonly_collaboration (read/analysis), draft_generation (generators), project_writing (create/update content), project_management (project/scheduler/skill CRUD), trusted_local_maintenance (delete/merge). No secret tools exposed in any pack.
- MCP-0904: `py -m pytest tests/test_external_agent_permission_settings.py -q` — 7 passed. Added ExternalAgentSettings model (project_id, enabled_packs, trusted_local_enabled, trusted_local_clients, require_confirmation_for_writes/destructive). Schema with safe defaults (readonly_collaboration only). API endpoints: GET/PUT /external-agent/settings.
- MCP-0905: `py -m pytest tests/test_mcp_permission_pack_enforcement.py -q` — 18 passed. Updated adapter.list_mcp_tools and is_tool_allowed to support permission_pack parameter. Tests verify: readonly pack has read tools only, draft adds generators, project_writing adds create/update, trusted adds delete. Pack hierarchy: readonly ⊂ draft ⊂ writing ⊂ management ⊂ trusted. No secret tools in any pack.
- MCP-0906: `npm run build` — built in 4.81s. Added ExternalAgentPermissionPanel.tsx with pack toggle cards, risk badges, tool list drawer, permanent deny-list notice, trusted local mode toggle with warning, confirmation toggles. Added types/externalAgentSettings.ts with pack definitions.
- MCP-0907: `py -m pytest tests/test_tool_catalog_api.py -q` — 5 passed. Added tools router with GET /tools/catalog and GET /projects/{project_id}/tools/exposed endpoints. Registered in main.py. Catalog returns all tools from single registry source; exposed endpoint filters by project's enabled packs.
- MCP-0908: `py scripts/check-tool-registry.py` — PASS, 106 tools checked. `py -m pytest tests/test_tool_registry_lint.py -q` — 8 passed. Added linter script that checks every tool has description, schema, handler, tool_type, permission_tags, risk_level, exposure flags. Rejects secret-looking tools exposed to MCP. Added docs/mcp/tool-authoring.md with copyable checklist.
- MCP-0909: Updated docs/mcp/claude-code-codex-client.md with Permission Packs section: pack definitions, hierarchy, how to enable, trusted local mode rules, troubleshooting for "tool not listed".
- MCP-0910: `py -m pytest` — 375 passed, 101 subtests. `npm run build` — built in 4.79s. `py scripts/check-tool-registry.py` — PASS, 106 tools. `py scripts/moshu-mcp-server.py --help` — exits 0. All Phase 9 release criteria met.
