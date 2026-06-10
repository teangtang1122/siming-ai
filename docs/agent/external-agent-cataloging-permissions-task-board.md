# External Agent Cataloging And Permission Task Board

> Project: Moshu / 墨枢
>
> Purpose: make Claude Code / Codex and the internal project assistant use the same reliable workflows for importing, cataloging, writing, permission control, and progress visibility.
>
> Triggering incident:
> - A local TXT novel was imported successfully as a Moshu project.
> - The external agent then attempted to "catalog/build archive" without using Moshu's model API.
> - It improvised a shallow manual workflow, reported success, but no outline/character/worldbuilding data was actually saved.
> - External Agent permission UI is currently project-scoped and does not reliably represent the actual MCP stdio permission pack.
>
> Status legend:
> - `[ ]` not started
> - `[-]` in progress
> - `[x]` completed and verified
> - `[!]` blocked, with blocker written under the task

## Strict Execution Rules

1. Claim exactly one task by changing `[ ]` to `[-]` and writing your name or handle.
2. Stay inside the listed file scope. If you must touch another area, write the reason under the task before editing.
3. Every completed task must include verification commands and results.
4. Mark `[x]` only after the code runs and the acceptance checks pass. Do not bulk mark tasks.
5. Each verified feature should be committed and pushed separately.
6. Do not expose API keys, model secrets, token files, or secret-management tools through MCP or external-agent APIs.
7. No-API mode must never call `LLMGateway`, `chapter_writer`, `character_writer`, `outline_writer`, `worldbuilding_writer`, `design_plot`, `evaluate_chapter`, or internal cataloging model steps.
8. External-agent write workflows must do read-after-write verification before reporting success.
9. New tools must be registered once in `ToolRegistry`; internal assistant, scheduler, MCP, and frontend tool catalog must derive from that one source.
10. Keep modules small. New service modules should stay under 300 lines unless the task explicitly justifies otherwise.

## Current Diagnosis

### D1. Why Claude's cataloging flow diverged from Moshu

Current Moshu has an external no-API writing workflow, but not an external no-API cataloging workflow.

Evidence:
- `docs/agent/external-no-api-writing.md` documents API-free chapter writing.
- `docs/mcp/claude-code-codex-client.md` tells external agents to start a cataloging job after import.
- `backend/app/services/workspace/tools/cataloging.py:start_cataloging_job` delegates to the internal cataloging orchestrator.
- `backend/app/services/cataloging/orchestrator.py` imports and calls `LLMGateway`.
- `backend/app/services/agent/planner.py` maps project initialization/cataloging intent to `start_cataloging_job`.

Result: when the user says "Moshu API is out of credit; use Claude itself", the external agent has no official cataloging playbook and falls back to ad-hoc CRUD.

### D2. Why "done" can be false

The MCP adapter now commits successful tool calls, but older running MCP processes or failed transactions can still leave an external agent in a misleading state.

Observed local state during audit:
- Imported project had 150 chapters.
- The same project had 0 outline nodes, 0 characters, 0 worldbuilding entries, and 0 relationships.

Likely causes:
- External agent ignored tool failures such as `PendingRollbackError`.
- The flow had no required read-after-write verification.
- No external cataloging run record required it to prove counts before saying done.
- The external agent may have been using a stale MCP server process from before the transaction fix.

### D3. Why permission UI feels wrong

Current permission settings are per-project:
- `ExternalAgentSettings.project_id` is required and unique.
- `McpPage` is only rendered inside `ProjectWorkspace`.

Actual Claude/Codex MCP exposure is configured by stdio CLI args:
- `Moshu.exe --mcp-server --permission-pack project_management`
- `scripts/setup-external-agent-mcp.ps1` writes that static permission pack into client config.

Result: the frontend setting and the actual MCP permission source are not the same thing. The UI can say "updated" while Claude/Codex still use the CLI pack.

### D4. Additional code consistency issue

`backend/app/services/agent/planner.py` currently defines `plan_cataloging_init` twice. The latter overrides the former, but the duplicate definition makes the intended flow easy to misunderstand.

## Phase 0 - Audit And Guardrails

### EAC-0001 - Write A Current-State Audit Test

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/tests/test_external_agent_cataloging_gap.py`
  - `backend/tests/test_mcp_transaction_visibility.py`
- Goal:
  - Capture the current failure mode before changing behavior.
- Implementation:
  - Build a test project with imported chapters.
  - Simulate external-agent calls that create outline/character/worldbuilding entries.
  - Assert successful calls are committed and visible from a fresh DB session.
  - Assert failed calls roll back and return `isError=true`.
  - Add a regression test that an agent cannot report cataloging complete unless verification counts are nonzero or explicitly accepted as empty.
- Verification:
  - `py -m pytest backend/tests/test_external_agent_cataloging_gap.py backend/tests/test_mcp_transaction_visibility.py -q`

### EAC-0002 - Remove Duplicate Cataloging Plan Function

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/services/agent/planner.py`
  - `backend/tests/test_agent_planner_cataloging.py`
- Goal:
  - Keep one canonical `plan_cataloging_init` definition.
- Implementation:
  - Delete the dead pseudo-tool version.
  - Add a test that "建档" intent produces the currently supported internal cataloging plan.
  - Add a second test that "不用墨枢 API 建档" is not routed to internal cataloging once EAC-0301 exists.
- Verification:
  - `py -m pytest backend/tests/test_agent_planner_cataloging.py -q`

## Phase 1 - System-Level External Agent Settings

### EAC-0101 - Add Global External Agent Settings Model

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/database/models.py`
  - `backend/app/database/migrations.py`
  - `backend/app/schemas/external_agent_settings.py`
  - `backend/tests/test_external_agent_global_settings.py`
- Goal:
  - Add a system-level external-agent configuration separate from project overrides.
- Design:
  - Add `ExternalAgentGlobalSettings`.
  - Fields:
    - `id`
    - `enabled_packs`
    - `trusted_local_enabled`
    - `trusted_local_clients`
    - `require_confirmation_for_writes`
    - `require_confirmation_for_destructive`
    - `mcp_permission_source`: `global_settings | cli_override`
    - `created_at`
    - `updated_at`
  - Keep existing `ExternalAgentSettings` as optional per-project override.
- Acceptance:
  - Old databases start without manual migration.
  - Default global pack is `readonly_collaboration`.
  - Project-level settings still work for existing projects.
- Verification:
  - `py -m pytest backend/tests/test_external_agent_global_settings.py -q`

### EAC-0102 - Add Global Settings API

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/app/routers/external_agent.py`
  - `backend/app/services/external_agent/permissions.py`
  - `backend/tests/test_external_agent_settings_api.py`
- Goal:
  - Expose external-agent settings at system scope.
- API:
  - `GET /api/v1/external-agent/settings`
  - `PUT /api/v1/external-agent/settings`
  - `GET /api/v1/external-agent/effective-permissions?project_id=...`
- Behavior:
  - Global settings are used when no project override exists.
  - Effective permissions include:
    - `global_enabled_packs`
    - `project_enabled_packs`
    - `effective_pack`
    - `source`
    - `cli_override`
    - `warnings`
- Verification:
  - `py -m pytest backend/tests/test_external_agent_settings_api.py -q`

### EAC-0103 - Make MCP Permission Source Explicit

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `backend/launcher.py`
  - `scripts/moshu-mcp-server.py`
  - `backend/app/mcp/server.py`
  - `backend/app/mcp/adapter.py`
  - `backend/app/services/external_agent/permissions.py`
  - `backend/tests/test_mcp_permission_source.py`
- Goal:
  - Make it impossible for the UI and actual MCP server permissions to silently disagree.
- Implementation:
  - Add CLI mode `--permission-pack auto`.
  - In `auto`, resolve permission from global settings for `tools/list`.
  - For `tools/call`, if `project_id` is passed, apply project override after global settings.
  - If an explicit CLI pack is passed, report `cli_override=true` through a new readonly tool `get_mcp_permission_status`.
  - Keep secret deny-list hardcoded regardless of mode.
- Verification:
  - `py -m pytest backend/tests/test_mcp_permission_source.py backend/tests/test_mcp_permission_pack_enforcement.py -q`

### EAC-0104 - Update Auto Configuration Script

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `scripts/setup-external-agent-mcp.ps1`
  - `release/setup-external-agent-mcp.ps1`
  - `docs/mcp/claude-code-codex-client.md`
  - `README.md`
- Goal:
  - Configure Claude/Codex to use `--permission-pack auto` by default.
- Implementation:
  - Default `$PermissionPack = "auto"`.
  - Keep explicit pack override available for advanced users.
  - Print a warning when user selects a fixed pack: "This bypasses UI global permission changes."
  - Update docs to explain global settings vs CLI override.
- Verification:
  - `powershell -ExecutionPolicy Bypass -File .\scripts\setup-external-agent-mcp.ps1 -DryRun`
  - Dry run output contains `--permission-pack auto`.

## Phase 2 - Frontend Information Architecture

### EAC-0201 - Move MCP / External Agent To Top-Level Navigation

- Status: `[x]`
- Owner: Claude Code
- File scope:
  - `frontend/src/App.tsx`
  - `frontend/src/pages/DashboardPage.tsx`
  - `frontend/src/pages/McpPage.tsx`
  - `frontend/src/pages/ProjectWorkspace.tsx`
  - `frontend/src/components/ExternalAgentPermissionPanel.tsx`
  - `frontend/src/types/externalAgentSettings.ts`
  - `frontend/src/__tests__/ExternalAgentSettingsPage.test.tsx`
- Goal:
  - Make external-agent/MCP settings a system-level page beside Project Management and System Settings.
- Design:
  - Add route `/external-agent`.
  - Dashboard header shows:
    - Project Management
    - System Settings
    - External Agent / MCP
  - `ProjectWorkspace` may keep a read-only project override summary, but the main settings live globally.
  - `McpPage` supports `projectId?: string`; when absent, it shows global settings and all-project MCP instructions.
- Verification:
  - `cd frontend && npm run build`
  - Frontend test confirms `/external-agent` renders without a project.

### EAC-0202 - Fix Permission Settings Persistence UX

- Status: `[ ]`
- Owner:
- File scope:
  - `frontend/src/components/ExternalAgentPermissionPanel.tsx`
  - `frontend/src/types/externalAgentSettings.ts`
  - `backend/tests/test_external_agent_settings_api.py`
  - `frontend/src/__tests__/ExternalAgentPermissionPanel.test.tsx`
- Goal:
  - Saving settings must be visibly and actually persistent.
- Implementation:
  - After PUT, immediately re-fetch GET and compare normalized payload.
  - Show a warning if the active MCP server is using a CLI override.
  - Show "Last saved at" from `updated_at`.
  - Support global settings and per-project override.
  - Do not silently fall back to default settings on fetch error; show an error banner.
- Acceptance:
  - Toggle pack, navigate away, navigate back: same pack is selected.
  - Restart packaged app: same pack is selected.
  - If `--permission-pack project_management` is active, UI says "Claude/Codex currently locked by CLI override."
- Verification:
  - `cd frontend && npm run build`
  - `py -m pytest backend/tests/test_external_agent_settings_api.py -q`

## Phase 3 - API-Free External Cataloging Workflow

### EAC-0301 - Add External Cataloging Prompt Pack

- Status: `[ ]`
- Owner:
- File scope:
  - `backend/app/services/prompt_packs/seed.py`
  - `backend/tests/test_prompt_packs_external_cataloging.py`
  - `docs/agent/external-no-api-cataloging.md`
- Goal:
  - Give external agents the same cataloging method Moshu expects, without using Moshu's API.
- Prompt pack:
  - `pack_id`: `cataloging_external_no_api`
  - `scope`: `cataloging`
  - `mode`: `external_no_api`
  - Must contain:
    - Per-chapter staged workflow.
    - Fact extraction schema.
    - Candidate update schema.
    - Merge rules for character aliases and duplicate characters.
    - Rules for current-state fields: overwrite state, rewrite-merge background/system prompt.
    - Read-after-write verification requirement.
    - Forbidden internal API-backed tools in no-API mode.
- Verification:
  - `py -m pytest backend/tests/test_prompt_packs_external_cataloging.py -q`

### EAC-0302 - Create External Cataloging Job Tools

- Status: `[ ]`
- Owner:
- File scope:
  - `backend/app/services/workspace/tools/external_cataloging.py`
  - `backend/app/services/workspace/registry.py`
  - `backend/app/services/workspace/tools/__init__.py`
  - `backend/tests/test_external_cataloging_tools.py`
- Goal:
  - Add API-free tools that let Claude/Codex catalog imported chapters through Moshu.
- Tools:
  - `start_external_cataloging_job`
    - Creates a `CatalogingJob` with mode `external_agent`.
    - Creates one `CatalogingChapterRun` per selected chapter.
    - Does not call `LLMGateway`.
  - `get_next_external_cataloging_chapter`
    - Returns the next pending chapter, chapter text, nearby context, character alias index, worldbuilding title index, outline neighborhood, and prompt pack.
  - `save_external_cataloging_facts`
    - Saves facts extracted by the external model into `CatalogingFact`.
  - `save_external_cataloging_candidates`
    - Saves external model's proposed candidates into `CatalogingCandidate`.
  - `verify_external_cataloging_progress`
    - Returns counts and samples for chapters, summaries, outline, characters, worldbuilding, relationships, aliases, and unapplied candidates.
- Permission packs:
  - Read/prepare tools: `readonly_collaboration`.
  - Save facts/candidates: `project_writing`.
  - Apply candidates: existing `apply_pending_cataloging`.
- Verification:
  - `py -m pytest backend/tests/test_external_cataloging_tools.py -q`

### EAC-0303 - Reuse Existing Cataloging Applier

- Status: `[ ]`
- Owner:
- File scope:
  - `backend/app/services/cataloging/applier.py`
  - `backend/app/services/cataloging/candidate_store.py`
  - `backend/app/services/workspace/tools/external_cataloging.py`
  - `backend/tests/test_external_cataloging_apply.py`
- Goal:
  - External cataloging candidates should be applied by the same code path as internal cataloging candidates.
- Requirements:
  - Character current state fields overwrite old current state.
  - Character background, appearance fallback, and custom system prompt use rewrite-merge semantics.
  - Character aliases are preserved and used for duplicate resolution.
  - Worldbuilding updates merge semantically, not append duplicate entries.
  - Outline nodes are created per chapter unless the candidate explicitly targets an existing node.
  - Chapter summaries are persisted and linked to outline/characters.
- Verification:
  - `py -m pytest backend/tests/test_external_cataloging_apply.py -q`

### EAC-0304 - Add External Cataloging Frontend Mode

- Status: `[ ]`
- Owner:
- File scope:
  - `frontend/src/pages/CatalogingPage.tsx`
  - `frontend/src/types/cataloging.ts`
  - `frontend/src/components/cataloging/`
  - `frontend/src/__tests__/CatalogingExternalMode.test.tsx`
- Goal:
  - Let users choose "External Agent / No Moshu API" when starting project cataloging.
- UX:
  - Mode selector:
    - Internal Moshu API
    - External Agent / No Moshu API
  - External mode shows:
    - Copyable Claude/Codex instruction block.
    - Required MCP tools.
    - Current job progress.
    - Current chapter awaiting external facts/candidates.
    - Apply/approve candidates controls reused from current manual cataloging UI.
- Verification:
  - `cd frontend && npm run build`
  - Test confirms external mode does not call internal cataloging stream endpoint.

### EAC-0305 - Route Assistant Intent Correctly

- Status: `[ ]`
- Owner:
- File scope:
  - `backend/app/services/agent/planner.py`
  - `backend/app/services/agent/bridge.py`
  - `backend/tests/test_agent_external_cataloging_plan.py`
- Goal:
  - Internal project assistant must not start internal cataloging when the user asks for no-API/external-agent cataloging.
- Behavior:
  - If message includes "API欠费", "不用墨枢 API", "用 Claude 建档", "用 Codex 建档", route to external cataloging handoff.
  - If Moshu model call fails due billing or missing key, assistant suggests external cataloging mode.
  - Normal "建档" still uses internal cataloging when API is available.
- Verification:
  - `py -m pytest backend/tests/test_agent_external_cataloging_plan.py -q`

## Phase 4 - External Agent Reliability Contract

### EAC-0401 - Add Tool Result Contract For External Agents

- Status: `[ ]`
- Owner:
- File scope:
  - `docs/mcp/claude-code-codex-client.md`
  - `docs/agent/external-no-api-cataloging.md`
  - `backend/app/services/prompt_packs/seed.py`
  - `backend/tests/test_prompt_pack_tool_contract.py`
- Goal:
  - Stop external agents from saying "done" after failed or skipped writes.
- Required rules:
  - After every tool call, parse `status`.
  - `status != "ok"` means stop, report exact failure, and do not summarize as complete.
  - After write operations, call a read/verify tool from a fresh query.
  - For cataloging, success requires:
    - imported chapter count > 0
    - outline count > 0 unless user selected "chapter summaries only"
    - character count > 0 for fiction imports
    - worldbuilding count > 0 for genre fiction imports
    - no pending failed chapter run
- Verification:
  - `py -m pytest backend/tests/test_prompt_pack_tool_contract.py -q`

### EAC-0402 - Add Project Archive Status Tool

- Status: `[ ]`
- Owner:
- File scope:
  - `backend/app/services/workspace/tools/project_status.py`
  - `backend/app/services/workspace/registry.py`
  - `backend/app/services/workspace/tools/__init__.py`
  - `backend/tests/test_project_archive_status_tool.py`
- Goal:
  - Give internal and external agents one canonical way to verify whether project data exists.
- Tool:
  - `get_project_archive_status`
- Output:
  - `chapters_count`
  - `chapter_summaries_count`
  - `outline_nodes_count`
  - `characters_count`
  - `character_aliases_count`
  - `relationships_count`
  - `worldbuilding_count`
  - `last_cataloging_job`
  - `warnings`
  - `recommended_next_steps`
- Verification:
  - `py -m pytest backend/tests/test_project_archive_status_tool.py -q`

### EAC-0403 - Improve MCP Error Visibility

- Status: `[ ]`
- Owner:
- File scope:
  - `backend/app/mcp/adapter.py`
  - `backend/app/mcp/schemas.py`
  - `backend/tests/test_mcp_error_contract.py`
- Goal:
  - External agents should receive actionable error details without exposing secrets.
- Implementation:
  - Include `tool`, `status`, `detail`, `error_type`, and a safe short traceback code in MCP errors.
  - Preserve `isError=true`.
  - Never hide `PendingRollbackError` behind only "Tool execution failed".
  - Include `next_suggestions` for recoverable cases.
- Verification:
  - `py -m pytest backend/tests/test_mcp_error_contract.py -q`

## Phase 5 - Documentation And Release

### EAC-0501 - Update Claude/Codex Client Guide

- Status: `[ ]`
- Owner:
- File scope:
  - `docs/mcp/claude-code-codex-client.md`
  - `README.md`
- Goal:
  - Document correct behavior for import, no-API writing, no-API cataloging, and permission settings.
- Required changes:
  - Import local novel:
    - use `import_file_as_project`
    - then call `get_project_archive_status`
  - Internal cataloging:
    - use `start_cataloging_job`
    - requires Moshu API/model config
  - External no-API cataloging:
    - use `get_prompt_pack(scope="cataloging", mode="external_no_api")`
    - use external cataloging tools
    - verify counts before saying done
  - Permissions:
    - explain global UI settings
    - explain CLI override
    - explain `--permission-pack auto`
- Verification:
  - `Get-Content docs/mcp/claude-code-codex-client.md | Select-String "external_no_api"`
  - `Get-Content README.md | Select-String "--permission-pack auto"`

### EAC-0502 - End-To-End No-API Cataloging Test

- Status: `[ ]`
- Owner:
- File scope:
  - `backend/tests/test_external_cataloging_no_api_e2e.py`
- Goal:
  - Prove a long imported novel can be cataloged without Moshu LLM calls.
- Test:
  - Create project with 3 sample chapters.
  - Start external cataloging job.
  - Use test-provided "external model" JSON to save facts and candidates.
  - Apply candidates.
  - Verify outline, characters, aliases, worldbuilding, chapter summaries, and links.
  - Monkeypatch `LLMGateway.chat_completion` to raise if called.
- Verification:
  - `py -m pytest backend/tests/test_external_cataloging_no_api_e2e.py -q`

### EAC-0503 - Packaged Smoke Test

- Status: `[ ]`
- Owner:
- File scope:
  - `scripts/smoke-test-release.ps1`
  - `PACKAGING.md`
  - `docs/agent/external-no-api-cataloging.md`
- Goal:
  - Verify the exe path that normal users use.
- Steps:
  - Build package.
  - Start `Moshu.exe`.
  - Configure MCP with `setup-external-agent-mcp.ps1 -DryRun`.
  - Import a small TXT file via MCP.
  - Run external no-API cataloging sample.
  - Verify data appears in UI/API.
- Verification:
  - `powershell -ExecutionPolicy Bypass -File .\scripts\smoke-test-release.ps1`

## Suggested Parallel Assignment

1. Backend permissions owner:
   - EAC-0101, EAC-0102, EAC-0103
2. Frontend settings owner:
   - EAC-0201, EAC-0202
3. External cataloging backend owner:
   - EAC-0301, EAC-0302, EAC-0303, EAC-0502
4. Assistant/planner owner:
   - EAC-0002, EAC-0305, EAC-0401
5. MCP reliability owner:
   - EAC-0001, EAC-0402, EAC-0403
6. Docs/release owner:
   - EAC-0104, EAC-0501, EAC-0503

## Final Acceptance Checklist

- [ ] User can configure external-agent permissions globally before entering any project.
- [ ] Claude Code / Codex configured with `--permission-pack auto` reflect global UI settings after restart.
- [ ] UI warns when an explicit CLI permission pack overrides UI settings.
- [ ] External agent can import a local TXT as a new project.
- [ ] External agent can catalog imported chapters without Moshu API.
- [ ] External agent can see and use the same prompt packs as internal assistant.
- [ ] External agent progress appears in Moshu UI during cataloging.
- [ ] External agent cannot report cataloging complete unless verification counts pass.
- [ ] Internal assistant routes "不用墨枢 API 建档" to external-agent workflow instead of internal cataloging.
- [ ] README clearly distinguishes internal API cataloging from external no-API cataloging.
