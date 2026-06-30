# Novel Creation Cross-Codebase Consistency Audit

> Date: 2026-06-09
> Auditor: Claude Code

## Audit Checklist

### 1. New Tools Registered Once in ToolRegistry

**Status: PASS**

All new tools are registered in `backend/app/services/workspace/registry.py`:

| Tool | Registry | Internal Agent | Scheduler | MCP | Frontend Catalog |
|------|----------|---------------|-----------|-----|-----------------|
| `prepare_external_writing_context` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `save_external_chapter_draft` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_external_chapter_draft` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `record_external_quality_review` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `apply_external_story_updates` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `list_prompt_packs` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_prompt_pack` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_tool_playbook` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `get_quality_rubric` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `start_novel_creation_session` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `draft_novel_blueprint` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `review_novel_blueprint` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `apply_novel_blueprint` | ✅ | ✅ | ✅ | ✅ | ✅ |

Verification: `py scripts/check-tool-registry.py` — PASS, 119 tools.

### 2. Tool Schemas Consistent

**Status: PASS**

All tool schemas are defined in the `ToolDef.input_schema` field in `registry.py`. The MCP adapter reads these directly. Frontend tool catalog derives from the same registry.

No manual schema lists maintained separately.

### 3. Long-Content Workflows Use draft_id/content_ref

**Status: PASS**

- `save_external_chapter_draft` returns `draft_id`/`content_ref`
- `create_chapter` accepts `draft_id` to avoid passing full text through tool arguments
- `record_external_quality_review` accepts `draft_id` or `chapter_id`
- Chapter content is stored server-side, not copied through repeated calls

### 4. Prompt Packs Match Between Internal and External

**Status: PASS**

- `inject_public_prompt_pack_section()` appends the same public pack data to internal prompts
- External agents fetch packs via `get_prompt_pack` which reads from the same `PublicPromptPack` table
- Version matching: internal prompt builder records pack version, external agents receive pack version

### 5. Runtime Schema Sync

**Status: PASS**

New tables added:
- `public_prompt_packs` — prompt pack storage
- `method_cards` — method card storage
- `novel_creation_sessions` — creation session tracking

All use `Base` from `app.database.session` which supports runtime schema sync via `ensure_runtime_schema()`.

### 6. Old Projects Compatible

**Status: PASS**

- No existing tables modified
- No existing columns changed
- New tables are additive-only
- Runtime schema sync creates missing tables on startup

### 7. Permission Packs Correct

**Status: PASS**

| Tool | Pack | Reason |
|------|------|--------|
| `prepare_external_writing_context` | readonly_collaboration | API-free read |
| `save_external_chapter_draft` | readonly_collaboration | API-free storage |
| `get_external_chapter_draft` | readonly_collaboration | API-free read |
| `record_external_quality_review` | readonly_collaboration | API-free storage |
| `list_prompt_packs` | readonly_collaboration | API-free read |
| `get_prompt_pack` | readonly_collaboration | API-free read |
| `get_tool_playbook` | readonly_collaboration | API-free read |
| `get_quality_rubric` | readonly_collaboration | API-free read |
| `start_novel_creation_session` | readonly_collaboration | API-free |
| `draft_novel_blueprint` | readonly_collaboration | API-free |
| `review_novel_blueprint` | readonly_collaboration | API-free |
| `apply_external_story_updates` | project_writing | Writes project data |
| `apply_novel_blueprint` | project_management | Creates project |

Verification: No API key/model secret tools exposed in any pack.

### 8. Frontend Pages Follow Conventions

**Status: PASS**

- `PromptPacksPage.tsx` — uses Ant Design Card/Collapse/Tag patterns
- `ExternalWritingPanel.tsx` — uses Ant Design Card/Collapse/Button patterns
- `NovelCreationWizardPage.tsx` — uses Ant Design Steps/Form patterns
- `ExternalAgentRunPanel.tsx` — uses Ant Design Collapse/Badge/Tag patterns
- `ExternalAgentPermissionPanel.tsx` — uses Ant Design Card/Switch/Alert patterns

All follow existing Siming UI conventions.

### 9. Documentation Consistent

**Status: PASS**

- `docs/agent/shared-prompt-pack-contract.md` — uses "Prompt Pack", "Method Card", "permission pack"
- `docs/agent/external-no-api-writing.md` — uses "external agent", "API-free"
- `docs/agent/novel-project-creation-task-board.md` — consistent terminology
- `docs/mcp/claude-code-codex-client.md` — updated with "No Siming API mode"
- `README.md` — updated with external agent writing reference

### 10. Tests Cover Both Modes

**Status: PASS**

- Internal API-backed mode: `test_prompt_packs.py`, `test_quality_mode_shared_prompts.py`
- External no-API mode: `test_external_writing_no_api_e2e.py`, `test_external_writing_context.py`
- Novel creation: `test_novel_creation_brief.py`, `test_novel_blueprint_draft.py`, `test_apply_novel_blueprint.py`
- MCP exposure: `test_mcp_prompt_pack_tools.py`, `test_mcp_external_writing_tools.py`, `test_mcp_novel_creation_tools.py`

## Summary

All 10 audit checks pass. The implementation is consistent across backend, frontend, MCP, prompts, tools, tests, and docs.
