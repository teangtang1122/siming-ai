# External Agent Live Session Specification

> Version: 0.1.0 (draft)
> Date: 2026-06-09
> Status: Phase 8 — specification
> Depends on: docs/mcp/spec.md, docs/mcp/security.md

## 1. Overview

This document defines how external MCP clients (Claude Code, Codex, etc.) report their progress to Moshu in real time. Users watch the external Agent working inside their project through the Moshu web UI.

### 1.1 Design Goals

1. **Observability, not control.** The external Agent drives its own execution. Moshu observes and displays.
2. **No hidden reasoning.** Only explicit plans, tool calls, progress messages, selected context, draft chunks, warnings, and committed writes are shown. Chain-of-thought is never requested, stored, or displayed.
3. **Backward compatible.** The existing internal project assistant SSE stream (`/api/v1/projects/{project_id}/assistant/stream`) continues to work unchanged.
4. **Security-first.** API keys, model secrets, auth tokens, local credentials, and raw confirmation tokens are never stored in events or displayed in the UI.

## 2. Run Lifecycle

An Agent run progresses through the following states:

```
created → running → waiting_confirmation → running → ... → completed
                  ↘ failed
                  ↘ cancelled
```

| Status | Description |
|--------|-------------|
| `created` | Run record created. No work started yet. |
| `running` | Agent is actively working (reading, planning, drafting). |
| `waiting_confirmation` | Agent requested a write and is waiting for user confirmation. |
| `completed` | Agent finished successfully. |
| `failed` | Agent encountered an unrecoverable error. |
| `cancelled` | User cancelled the run. |

### 2.1 Terminal States

`completed`, `failed`, and `cancelled` are terminal states. No further events are accepted after a terminal state.

### 2.2 Cancellation

- The user cancels a run through the Moshu UI or API.
- The backend records a `cancelled` event.
- The external client checks for cancellation by reading the run status (via `GET /api/v1/projects/{project_id}/agent-runs/{run_id}`) before reporting the next event.
- If cancelled, the client should stop work and not report further events.

## 3. Event Types

Every event has a `sequence` number (monotonically increasing per run), an `event_type`, a `status`, an optional `message`, and an optional `payload_json`.

### 3.1 Event Type Definitions

| Event Type | When Emitted | Payload Fields |
|------------|-------------|----------------|
| `plan` | Agent reports its plan before starting work | `plan`: array of step descriptions |
| `progress` | Agent reports a progress update | `step`: current step index, `detail`: what's happening |
| `tool_start` | Agent begins calling a Moshu tool | `tool`: tool name, `args_summary`: truncated arguments |
| `tool_result` | Tool call completed | `tool`: tool name, `status`: ok/skipped/error, `detail`: result summary |
| `context_selected` | Agent reports which context it selected for reasoning | `sources`: array of {source_type, source_id, title, reason} |
| `draft_chunk` | Agent streams a chunk of draft content | `content`: text chunk, `chunk_index`: sequence number |
| `draft_ready` | Agent finished generating a complete draft | `content_type`: chapter/outline/character/worldbuilding, `summary`: brief description |
| `write_requested` | Agent requests a confirmed write | `write_type`: create_chapter/update_chapter/..., `payload_summary`: what will be written |
| `write_committed` | User confirmed and write was applied | `write_type`, `result_status`, `result_detail` |
| `warning` | Non-fatal issue encountered | `detail`: warning description |
| `error` | Fatal error occurred | `detail`: error description |
| `run_finished` | Agent signals it is done | `summary`: final summary of what was accomplished |

### 3.2 Event Status

Each event has a `status` field:

| Status | Meaning |
|--------|---------|
| `ok` | Event completed successfully |
| `running` | Event is in progress (for long-running operations) |
| `error` | Event encountered an error |
| `skipped` | Event was skipped |

## 4. Payload Size Limits and Truncation

| Field | Max Size | Truncation Rule |
|-------|----------|-----------------|
| `message` | 500 chars | Truncate with `...[truncated]` |
| `args_summary` | 300 chars | Truncate with `...[truncated]` |
| `detail` | 2000 chars | Truncate with `...[truncated]` |
| `payload_json` | 10,000 chars | Truncate with `...[truncated]` |
| `draft_chunk` content | 5000 chars | Truncate with `...[truncated]` |
| `plan` array items | 10 items max, 200 chars each | Truncate excess items with `...and N more steps` |
| `sources` array | 20 items max | Truncate excess items |
| `summary` | 1000 chars | Truncate with `...[truncated]` |

### 4.1 Content References

For large content (full chapter text, large tool results), store a reference instead of the full content:

```json
{
  "content_ref": "draft:abc123",
  "content_type": "chapter",
  "word_count": 2500
}
```

The frontend can fetch the full content on demand if needed.

## 5. Security Rules

### 5.1 Never Store in Events

The following must never appear in any event payload, message, or summary:

- API keys, model secrets, tokens, credentials
- Database connection strings
- Raw confirmation tokens (the token ID is ok; the token value is not)
- Internal file system paths outside the project scope
- Raw LLM system prompts used by Moshu

### 5.2 Secret Detection

Before persisting any event payload, the backend scans for secret-like patterns:

```
*api_key*
*secret*
*credential*
*token*
*password*
```

If a match is found:
- The matching value is replaced with `[REDACTED]`.
- A `warning` event is recorded: "Event payload contained a secret-like value and was redacted."

### 5.3 Argument Summary

Tool arguments are summarized before storage:

- Full chapter content → `"[chapter content, 2500 words]"`
- Full character card → `"[character card: Hero]"`
- Database IDs are kept (they are not sensitive)
- Other large fields are replaced with `"[field_name: N chars]"`

## 6. Frontend Rendering Contract

### 6.1 Run Panel

The frontend displays a collapsible "External Agent" panel in the project workspace:

- **Active run selector**: Shows the current active run (if any).
- **Historical run list**: Shows past runs with status, title, and timestamp.
- **Timeline**: Events displayed in sequence order, grouped by plan steps and tool calls.
- **Latest status strip**: Shows the most recent `progress` or `plan` event.
- **Draft preview**: Shows the latest `draft_chunk` content with copy/apply controls (disabled until confirmed-write flow exists).
- **Tool log**: List of `tool_start`/`tool_result` pairs.
- **Warnings and errors**: Displayed prominently without requiring developer console.
- **Empty state**: Explains how to connect Claude Code / Codex through MCP.

### 6.2 Event Display

| Event Type | Display |
|------------|---------|
| `plan` | Numbered list of steps |
| `progress` | Status text with spinner |
| `tool_start` | Tool name with "running" indicator |
| `tool_result` | Tool name with status badge and detail |
| `context_selected` | List of sources with type badges |
| `draft_chunk` | Appended to draft preview area |
| `draft_ready` | "Draft complete" badge with summary |
| `write_requested` | Confirmation dialog with preview |
| `write_committed` | "Write confirmed" badge |
| `warning` | Yellow warning banner |
| `error` | Red error banner |
| `run_finished` | Summary card |

### 6.3 SSE Stream Format

The SSE stream uses the standard MCP event format:

```
event: agent_run_event
data: {"run_id":"abc","sequence":1,"event_type":"progress","status":"ok","message":"Reading chapters...","payload_json":null,"created_at":"2026-06-09T10:00:00Z"}

event: agent_run_event
data: {"run_id":"abc","sequence":2,"event_type":"tool_start","status":"running","message":null,"payload_json":"{\"tool\":\"search_chapters\",\"args_summary\":\"{\\\"query\\\":\\\"hero\\\"}\"}","created_at":"2026-06-09T10:00:01Z"}
```

### 6.4 Backward Compatibility

The existing internal assistant SSE stream at `/api/v1/projects/{project_id}/assistant/stream` is unchanged. External Agent events use a separate endpoint:

```
GET /api/v1/projects/{project_id}/agent-runs/{run_id}/stream
```

The frontend subscribes to this endpoint when viewing an external Agent run.

## 7. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/projects/{project_id}/agent-runs` | Create a new run |
| `GET` | `/api/v1/projects/{project_id}/agent-runs` | List runs |
| `GET` | `/api/v1/projects/{project_id}/agent-runs/{run_id}` | Get run details |
| `GET` | `/api/v1/projects/{project_id}/agent-runs/{run_id}/events` | Get all events |
| `GET` | `/api/v1/projects/{project_id}/agent-runs/{run_id}/stream` | SSE stream |
| `POST` | `/api/v1/projects/{project_id}/agent-runs/{run_id}/cancel` | Cancel run |

## 8. MCP Tools for External Agents

External Agents use these MCP tools to report progress:

| Tool | Description | Tier |
|------|-------------|------|
| `start_agent_run` | Create a new run | readonly |
| `report_agent_plan` | Report the execution plan | readonly |
| `report_agent_progress` | Report a progress update | readonly |
| `report_context_selected` | Report which context was selected | readonly |
| `append_draft_chunk` | Stream a draft content chunk | readonly |
| `mark_draft_ready` | Signal that a draft is complete | readonly |
| `finish_agent_run` | Signal run completion | readonly |

These tools write run telemetry only, not project content. They are allowed in readonly mode.

## 9. Backward Compatibility

- The existing `execute_workspace_action` function is unchanged.
- The existing internal assistant SSE stream is unchanged.
- The existing MCP tools (Phase 1-7) continue to work without `run_id`.
- When `run_id` is not provided, no telemetry is logged (existing behavior).

## 10. Out of Scope

- Multi-turn Agent conversations through MCP (the external Agent manages its own conversation).
- Streaming the external Agent's internal reasoning.
- Real-time collaboration between multiple external Agents.
- Agent-to-Agent communication through Moshu.
