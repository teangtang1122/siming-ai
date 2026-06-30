# Siming MCP Security Policy

> Version: 0.1.0
> Date: 2026-06-07
> Status: Phase 0 — specification

## 1. Principles

1. **Least privilege by default.** The MCP server starts in readonly mode. No write or generator tool is advertised or callable unless explicitly enabled by a higher permission tier.
2. **Never expose secrets.** API keys, model secrets, tokens, and credentials must never appear in any MCP response — tool output, resource content, error messages, or debug logs.
3. **Defense in depth.** Permission filtering is enforced at two layers: at `tools/list` time (denied tools are never advertised) and at `tools/call` time (denied tools are rejected even if the client sends a raw request).
4. **No silent escalation.** Moving from readonly to a higher tier requires an explicit, auditable configuration change — not an LLM instruction.

## 2. Network Binding

| Transport | Binding | v1 Status |
|-----------|---------|-----------|
| stdio | Local process only; no network exposure | Supported |
| HTTP / Streamable HTTP | Would require `127.0.0.1` binding + authentication | **Not supported in v1** |

**Recommendation:** For the first release, use stdio only. The server runs as a child process of the MCP client (e.g., Claude Desktop) and communicates over stdin/stdout. No port is opened, no network attack surface exists.

If HTTP transport is added later:
- Bind to `127.0.0.1` by default (not `0.0.0.0`).
- Require authentication (token or mTLS).
- Never expose over a public network without TLS.

## 3. Default Mode: Readonly

The server operates in **readonly** mode by default. In this mode:

- Only tools with `tool_type` in `{read, analysis, web}` and specific memory read tools (`recall`, `list_memories`) are advertised via `tools/list`.
- All other tools return `PermissionDenied` (-32001) on `tools/call`.
- No database writes occur through MCP.

The readonly mode is hardcoded as the startup default. It is not configurable via MCP protocol messages — changing it requires modifying server configuration files or command-line flags.

## 4. Denied Tool Families

The following tool families are denied in v1 and remain denied until the corresponding permission tier is implemented and enabled.

### 4.1 Write Tools (`write_confirmed` tier)

These tools mutate the database. They are denied until a confirmation-token model is implemented (Phase 4).

| Pattern | Matching Tools |
|---------|---------------|
| `create_*` | `create_project`, `create_character`, `create_chapter`, `create_outline_node`, `create_worldbuilding_entry`, `create_relationship`, `create_scheduled_task`, `create_skill` |
| `update_*` | `update_project_info`, `update_character`, `update_chapter`, `update_outline_node`, `update_worldbuilding_entry`, `update_relationship`, `update_scheduled_task`, `update_skill`, `update_cataloging_candidate` |
| `delete_*` | `delete_project`, `delete_character`, `delete_chapter`, `delete_outline_node`, `delete_worldbuilding_entry`, `delete_relationship`, `delete_scheduled_task`, `delete_skill` |
| `merge_*` | `merge_duplicate_characters` |
| `import_*` | `import_text_as_chapters`, `import_deconstruct_report` |
| `start_*` | `start_cataloging_job`, `start_deconstruct_job` |
| `run_*` | `run_scheduled_task_now` |
| `set_*` | `set_cataloging_mode`, `set_daily_word_goal` |
| `apply_*` | `apply_pending_cataloging` |
| `pause_*` | `pause_cataloging_job` |
| `resume_*` | `resume_cataloging_job` |
| `cancel_*` | `cancel_cataloging_job` |
| `rerun_*` | `rerun_cataloging_resolution_current`, `rerun_failed_deconstruct_chunks` |
| `ensure_*` | `ensure_builtin_skills` |
| `reset_*` | `reset_skill` |
| `forget` | Memory deletion tool |
| `export_*` | `export_project` (writes files to disk) |

### 4.2 Generator Tools (`draft` tier)

These tools produce content via LLM but do not write to the database. They are denied until the draft tier is implemented (Phase 4).

| Pattern | Matching Tools |
|---------|---------------|
| `*_writer` | `chapter_writer`, `outline_writer`, `character_writer`, `worldbuilding_writer` |
| `rewrite_*` | `rewrite_text` |
| `expand_*` | `expand_text` |
| `continue_*` | `continue_text` |
| `roleplay_*` | `roleplay_character` |
| `dialogue_*` | `dialogue_battle` |
| `remember` | Memory write tool |
| `draft_*` | `draft_skill` |

### 4.3 Permanently Denied (Secret Management)

The following categories must **never** be exposed through MCP, regardless of permission tier or future phases:

- API key CRUD (create, read, update, delete)
- Model secret / provider key management
- Configuration secret management
- Database connection string exposure
- Raw internal LLM prompt templates

As of the current `ToolRegistry`, no tools exist in these categories — secrets are managed through the application settings UI and environment variables, not through workspace tools. This policy ensures that if such tools are ever added to the registry, they will be automatically blocked by the MCP permission filter.

The permission filter maintains an explicit deny-list of secret-related tool name patterns:

```
*api_key*
*secret*
*credential*
*token*
*password*
```

Any tool whose name matches one of these patterns is denied regardless of its `tool_type` or permission tier.

## 5. Confirmation-Token Model (Future)

When write tools are enabled (Phase 4), each write invocation will require a confirmation token:

1. The MCP client calls a write tool.
2. The server checks for a valid `confirmation_token` in the tool arguments.
3. If missing or invalid, the server returns `PermissionDenied` with reason `confirmation_required`.
4. If valid, the server executes the tool and invalidates the token (single-use).
5. Tokens are scoped: a token issued for `create_chapter` cannot be used for `delete_project`.

Token issuance mechanism is defined in the Phase 4 specification (MCP-0402). Until then, all write tools are unconditionally denied.

## 6. Content Redaction

Even for allowed readonly tools, the server applies content redaction:

| Rule | Rationale |
|------|-----------|
| API keys, tokens, secrets never appear in output | Absolute security boundary |
| Chapter content truncated to 8000 characters | Prevent context-window flooding |
| Character cards truncated to 8000 characters | Same |
| Database IDs are exposed (not sensitive) | Required for tool chaining |
| Internal file paths are not exposed | Prevent path traversal information leak |
| Raw LLM system prompts are not exposed | Protect IP and prevent prompt injection |

## 7. Error Message Safety

Error messages returned to MCP clients must not contain:

- Stack traces or internal function names
- Database connection strings
- File system paths outside the project scope
- API keys or secrets, even in debug context
- SQL queries or ORM internals

Errors use the standardized codes defined in `docs/mcp/spec.md` Section 7.

## 8. Auditability

Every MCP tool call is logged with:

- Tool name
- Timestamp
- Arguments summary (secrets redacted)
- Permission tier at time of call
- Result status (success / denied / error)

Logs are stored in the existing run-log system (`backend/app/services/workspace/run_log.py`). They are accessible to the project owner through the Siming UI.

## 9. Security Checklist for Implementers

Before merging any MCP-related code, verify:

- [ ] No new tool is advertised via `tools/list` without being listed in this document's allow-list.
- [ ] No tool output contains API keys, model secrets, or tokens.
- [ ] Permission filter is applied at both `tools/list` and `tools/call`.
- [ ] Error messages do not leak internal state.
- [ ] The server defaults to readonly mode.
- [ ] stdio is the only transport.
- [ ] Secret-related tool name patterns are in the deny-list.
