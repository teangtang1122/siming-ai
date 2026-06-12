# Claude Code / Codex MCP Client Setup Guide

> This guide shows how to connect Claude Code or Codex to Moshu through MCP, enabling the external Agent to read project data and report progress to the Moshu web UI.

By default, Moshu MCP can run without binding to a single project. In that mode,
external agents should first call `list_projects`, then pass the selected
`project_id`/`id` to project-scoped tools.

## Prerequisites

- Moshu installed (from source or packaged exe)
- A Moshu project created with some content
- Claude Code or Codex installed

## Quick Start

### Option 0: Automatic Windows Setup

If you are on Windows, use the setup script first. It detects Claude Code and
Codex, finds `Moshu.exe` when available, falls back to the source MCP entrypoint,
and writes the correct client configuration.

From a GitHub Release, place `setup-external-agent-mcp.ps1` next to `Moshu.exe`
and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-external-agent-mcp.ps1
```

From source:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-external-agent-mcp.ps1 -PreferSource
```

Preview without changing files:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-external-agent-mcp.ps1 -DryRun
```

### Option 1: From Source

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "moshu": {
      "command": "python",
      "args": [
        "scripts/moshu-mcp-server.py",
        "--permission-pack",
        "project_management"
      ],
      "cwd": "D:\\AI\\agent"
    }
  }
}
```

### Option 2: From Packaged Exe

```json
{
  "mcpServers": {
    "moshu": {
      "command": "C:\\path\\to\\Moshu.exe",
      "args": ["--mcp-server", "--permission-pack", "project_management"]
    }
  }
}
```

If you want to bind the MCP server to one default project, add
`--project-id YOUR_PROJECT_ID`. This is optional; without it, Claude Code/Codex
can see all projects through `list_projects`.

### Finding Your Project ID

1. Open Moshu in your browser
2. Go to your project
3. The project ID is in the URL: `http://localhost:8765/projects/YOUR_PROJECT_ID/...`

## Permission Packs

- `readonly_collaboration`: read/search/API-free context tools only.
- `draft_generation`: legacy compatibility pack; does not expose Moshu internal LLM tools.
- `project_writing`: can create/update chapters, characters, outline, worldbuilding, and external drafts without calling Moshu's model API.
- `project_management`: project CRUD, import/export, scheduler and skill management. It does **not** expose internal LLM tools.
- `internal_llm`: explicit opt-in pack for tools that spend Moshu's configured model API, such as `chapter_writer` and `start_cataloging_job`.
- `trusted_local_maintenance`: exposes destructive tools such as delete/merge. It does **not** imply `internal_llm`.

For normal local Claude Code/Codex use, `project_management` is the practical
default: it can list all projects, create new projects, write content, manage
skills, and export data, while destructive tools and internal LLM tools remain
outside the pack.

Default rule for external agents: do your own reading, reasoning, cataloging,
and writing unless the user explicitly says to use Moshu's internal API/model
quota. Use `internal_llm` only for that explicit opt-in mode.

## Operating Rules for External Agents

When Claude Code or Codex operates Moshu through MCP, follow these rules for the best experience:

### 1. Start a Run

Before reading any project data, create an Agent run:

```
Tool: start_agent_run
Arguments: { "client_name": "claude-code", "title": "Writing Chapter 5" }
```

Save the returned `run_id` — you'll use it for all subsequent calls.

### 2. Report a Plan

Before starting work, report your plan:

```
Tool: report_agent_plan
Arguments: {
  "run_id": "YOUR_RUN_ID",
  "plan": [
    "Read outline and recent chapters",
    "Select relevant characters and worldbuilding",
    "Generate chapter draft",
    "Request write confirmation"
  ]
}
```

### 3. Use Moshu Resources/RAG Before Writing

Read project context before generating content:

```
Tool: search_chapters
Arguments: { "query": "recent", "limit": 3 }

Tool: search_characters
Arguments: { "query": "main character" }

Tool: search_worldbuilding
Arguments: { "query": "magic system" }
```

### 4. Report Selected Context

Tell Moshu which context you're using:

```
Tool: report_context_selected
Arguments: {
  "run_id": "YOUR_RUN_ID",
  "sources": [
    { "source_type": "chapter", "title": "Chapter 4", "reason": "Previous chapter for continuity" },
    { "source_type": "character", "title": "Hero", "reason": "Main character in this scene" }
  ]
}
```

### 5. Stream Draft Chunks

For long writing, stream content in chunks:

```
Tool: append_draft_chunk
Arguments: {
  "run_id": "YOUR_RUN_ID",
  "content": "The first part of the chapter...",
  "chunk_index": 0
}
```

### 6. Mark Draft Complete

When the draft is ready:

```
Tool: mark_draft_ready
Arguments: {
  "run_id": "YOUR_RUN_ID",
  "content_type": "chapter",
  "summary": "Chapter 5: The Battle of Helm's Deep, 2500 words"
}
```

### 7. Request Confirmed Writes

Instead of directly creating chapters, request confirmation:

```
Tool: report_agent_progress
Arguments: {
  "run_id": "YOUR_RUN_ID",
  "message": "Requesting write confirmation for Chapter 5"
}
```

The user will see the request in the Moshu UI and can confirm or reject.

### 8. Finish the Run

When done, summarize what was accomplished:

```
Tool: finish_agent_run
Arguments: {
  "run_id": "YOUR_RUN_ID",
  "summary": "Generated Chapter 5 (2500 words). User confirmed write. All worldbuilding constraints followed."
}
```

## Tool Result Contract

Every Moshu MCP tool returns a JSON result with a `status` field. Follow these rules:

1. **Always check `status`** — after every tool call, parse the result JSON.
2. **`status != "ok"` means failure** — stop immediately, report the exact error
   to the user, and do not summarize as complete.
3. **Verify after writes** — after write operations, call a read/verify tool to
   confirm data was persisted. Do not trust the write response alone.
4. **Never report "done" without verification** — for cataloging, success requires:
   - Imported chapter count > 0
   - Outline node count > 0 (unless user selected "chapter summaries only")
   - Character count > 0 (for fiction imports)
   - Worldbuilding count > 0 (for genre fiction)
   - No pending failed chapter runs
   - No unapplied candidates

If a tool returns `isError: true`, the error payload includes:
- `tool` — which tool failed
- `status` — always `"error"`
- `detail` — human-readable explanation
- `error_type` — exception class name
- `traceback_code` — short hash for log correlation
- `next_suggestions` — actionable recovery steps (when available)

## Importing Local Novels

When the user asks to import a local TXT/DOCX novel as a new Moshu project,
prefer `import_file_as_project` instead of reading the whole file and passing the
full text through MCP arguments.

Example:

```
Tool: import_file_as_project
Arguments: {
  "file_path": "E:\\download\\穿越女娃，竟被病毒追着杀.txt",
  "title": "穿越女娃，竟被病毒追着杀"
}
```

Use `import_file_as_chapters` only when the target Moshu project already exists.

After import, verify the import succeeded:

```
Tool: get_project_archive_status
Arguments: { "project_id": "YOUR_PROJECT_ID" }
```

This returns chapter count, character count, outline count, etc. If chapters > 0 but everything else is 0, the project needs cataloging.

Start a cataloging job if the user wants Moshu to initialize
chapter summaries, character cards, outline nodes, worldbuilding, and links.

## Available MCP Tools

### Read-Only Tools (Always Available)

| Tool | Description |
|------|-------------|
| `list_projects` | List all projects |
| `get_project_info` | Get project metadata |
| `search_chapters` | Search chapters by title |
| `search_characters` | Search characters by name |
| `search_worldbuilding` | Search worldbuilding entries |
| `search_outline` | Search outline nodes |
| `search_context` | Full-text RAG search |
| `preview_writing_context` | Pre-write context check |
| `web_search` | Internet search |

### External Agent Reporting Tools

| Tool | Description |
|------|-------------|
| `start_agent_run` | Create a new run |
| `report_agent_plan` | Report execution plan |
| `report_agent_progress` | Report progress update |
| `report_context_selected` | Report selected context |
| `append_draft_chunk` | Stream draft content |
| `mark_draft_ready` | Signal draft completion |
| `finish_agent_run` | Signal run completion |

### Draft Tools (Generator)

| Tool | Description |
|------|-------------|
| `chapter_writer` | Generate chapter content |
| `outline_writer` | Generate outline nodes |
| `character_writer` | Generate character cards |
| `worldbuilding_writer` | Generate worldbuilding entries |
| `rewrite_text` | Rewrite text |
| `expand_text` | Expand text details |
| `continue_text` | Continue writing |

### Project Management Tools

| Tool | Description |
|------|-------------|
| `create_project` | Create a new Moshu project |
| `import_file_as_project` | Create a project from a local TXT/DOCX file and import chapters |
| `import_file_as_chapters` | Import a local TXT/DOCX file into an existing project |
| `import_text_as_chapters` | Import pasted text into an existing project |
| `start_cataloging_job` | Initialize project cards from imported chapters |
| `export_project` | Export project content |

## Permission Packs

By default, Claude Code / Codex only has **readonly** access to your project. You can grant broader permissions through the Moshu UI.

### Available Packs

| Pack | What It Allows | Risk |
|------|---------------|------|
| **只读协作** (readonly_collaboration) | Search chapters, characters, worldbuilding, outline. Read project info. | Safe |
| **草稿生成** (draft_generation) | Use AI to generate chapter content, outline, characters. Content stays in memory. | Low |
| **项目写入** (project_writing) | Create and update chapters, characters, outline, worldbuilding in the database. | Medium |
| **项目管理** (project_management) | Manage project settings, import/export, scheduled tasks, skills. | High |
| **可信本地维护** (trusted_local_maintenance) | Delete and merge operations. Only in trusted local mode. | Destructive |

### Pack Hierarchy

Packs form a hierarchy — enabling a higher pack automatically enables all lower packs:

```
只读协作 ⊂ 草稿生成 ⊂ 项目写入 ⊂ 项目管理 ⊂ 可信本地维护
```

### How to Enable More Packs

1. Open Moshu web UI
2. Go to your project
3. Find the "外部 Agent 权限设置" panel
4. Toggle the packs you want to enable
5. Confirm the changes

### Global Settings vs CLI Override

Moshu has two levels of permission settings:

**Global settings** (system-wide):
- Configured in the Moshu web UI under "External Agent / MCP"
- Apply to all projects unless overridden
- Default: `readonly_collaboration`

**Project settings** (per-project):
- Configured in each project's settings
- Override global settings for that project only

**CLI override** (fixed pack):
- Set via `--permission-pack` flag when starting the MCP server
- Bypasses all UI settings
- Use `--permission-pack auto` (default) to respect UI settings
- Use a fixed pack (e.g., `--permission-pack project_management`) to lock permissions

**Recommended:** Use `--permission-pack auto` (the default) so UI settings take effect. Only use fixed packs for advanced use cases.

When a CLI override is active, the UI will show a warning: "Claude/Codex currently locked by CLI override."

### Check Current Permission Status

Use the `get_mcp_permission_status` tool to see which permissions are active:

```
get_mcp_permission_status()
```

Returns:
- `effective_pack`: the active permission pack
- `source`: where it came from (global_settings, project_override, cli_override)
- `cli_override`: whether a CLI override is active
- `warnings`: any issues

### Trusted Local Mode

Trusted local mode allows Claude Code / Codex to skip write confirmations for project content. **Only enable this on machines you control.**

Requirements:
- You must explicitly enable it in project settings
- The MCP client must connect via stdio or localhost
- The client name must be in your trusted client list

When enabled:
- Project write tools work without confirmation tokens
- Project management tools become available
- Destructive tools still require confirmation
- All writes are audited

### Why Is a Tool Not Listed?

If a tool you expect is not available:

1. **Project ID missing** — Make sure you're using `--project-id YOUR_PROJECT_ID`
2. **Permission pack disabled** — Check project settings in Moshu UI
3. **Tool marked internal-only** — Some tools are only for the internal assistant
4. **Secret deny-list** — API key/model secret tools are permanently blocked
5. **Schema validation failure** — Check linter: `python scripts/check-tool-registry.py`

## No Moshu API Mode

Claude Code / Codex can write novels through Moshu **without any model API configured inside Moshu**. In this mode, Moshu provides context, prompt packs, storage, and telemetry — the external model does all generation and review.

### How It Works

1. Moshu stores your project data (outline, characters, worldbuilding)
2. Claude Code / Codex fetches writing context and prompt packs from Moshu
3. The external model generates chapter text using its own capabilities
4. The external model self-reviews using Moshu's quality rubric
5. The draft is saved to Moshu and promoted to a chapter after confirmation

### Writing a Chapter Without Moshu API

```
# 1. Get writing context
prepare_external_writing_context({
  "project_id": "YOUR_PROJECT_ID",
  "outline_node_id": "NODE_ID",
  "mode": "quality"
})

# 2. [Claude Code writes chapter using the context and prompt pack]

# 3. Save the draft
save_external_chapter_draft({
  "content": "Generated chapter text...",
  "title": "Chapter Title",
  "outline_node_id": "NODE_ID",
  "source_agent": "claude-code"
})

# 4. Record quality review
record_external_quality_review({
  "draft_id": "DRAFT_ID",
  "scores": {"opening_hook": 8, "plot_progression": 7, ...},
  "pass": true,
  "reviewer_model": "claude-sonnet-4-6"
})

# 5. Create the chapter
create_chapter({
  "title": "Chapter Title",
  "draft_id": "DRAFT_ID",
  "outline_node_id": "NODE_ID"
})

# 6. Apply story updates
apply_external_story_updates({
  "chapter_id": "CHAPTER_ID",
  "updates": {
    "characters": [{"id": "CHAR_ID", "current_location": "New Location"}],
    "chapter_summary": "Brief summary..."
  },
  "mode": "auto"
})
```

### Creating a New Novel Without Moshu API

```
# 1. Start creation session
start_novel_creation_session({
  "user_brief": "A xianxia novel about a female cultivator",
  "genre": "xianxia",
  "target_audience": "male",
  "platform": "qidian"
})

# 2. Draft blueprints (external agent fills the schema)
draft_novel_blueprint({
  "session_id": "SESSION_ID",
  "execution_mode": "external_agent"
})
# [Claude Code generates blueprints using the provided schema]

# 3. Review blueprint
review_novel_blueprint({
  "session_id": "SESSION_ID",
  "execution_mode": "external_agent",
  "blueprint": { ... }
})

# 4. Apply blueprint to create project
apply_novel_blueprint({
  "session_id": "SESSION_ID",
  "mode": "auto"
})
```

### Cataloging Without Moshu API

After importing a novel, you can catalog it (extract characters, worldbuilding,
outline, and chapter summaries) without Moshu's model API:

For long Claude Code / Codex conversations, treat Moshu's tool results as the
current source of truth. If the agent is unsure what to do next, call
`get_moshu_usage_guide({"scenario":"cataloging_no_api","no_api":true})` again.
For Chinese novels, keep all archive data in Chinese: character names, aliases,
chapter titles, summaries, outline nodes, facts, evidence, and worldbuilding.
Do not translate to English or pinyin unless the user explicitly requests it.

```
# 0. Ask Moshu which workflow to use
get_moshu_usage_guide({
  "scenario": "cataloging_no_api",
  "no_api": true
})

# 1. Get the cataloging prompt pack
get_prompt_pack({
  "scope": "cataloging",
  "mode": "external_no_api"
})

# 2. Start external cataloging job
start_external_cataloging_job({
  "project_id": "YOUR_PROJECT_ID"
})

# 3A. Fact stage can be parallel. Multiple agents may fetch different chapters.
get_next_external_cataloging_chapter({
  "job_id": "JOB_ID",
  "phase": "facts"
})
# [Analyze the chapter text and extract facts only]

save_external_cataloging_facts({
  "job_id": "JOB_ID",
  "chapter_id": "CHAPTER_ID",
  "facts": [...]
})

# 3B. Candidate stage must be serial by chapter_order. Always ask Moshu
# which chapter is allowed next; never use fact-completion order.
get_next_external_cataloging_chapter({
  "job_id": "JOB_ID",
  "phase": "candidates"
})
# [Generate candidates for the returned chapter only]

save_external_cataloging_candidates({
  "job_id": "JOB_ID",
  "chapter_id": "CHAPTER_ID",
  "candidates": [...]
})

# 4. Apply this chapter's candidates before generating candidates for the next chapter
apply_pending_cataloging({
  "job_id": "JOB_ID"
})

# 5. Verify this chapter wrote real project data
verify_external_cataloging_progress({
  "job_id": "JOB_ID"
})

# 6. Repeat candidate-stage steps in chapter_order; then do final verification
get_project_archive_status({})
```

`save_external_cataloging_candidates` only stages data. The data is not visible
as real characters, outline nodes, worldbuilding, or chapter summaries until
`apply_pending_cataloging` succeeds. If `verify_external_cataloging_progress`
reports `pending_candidates > 0` or `chapters_awaiting_confirmation > 0`, apply
the pending chapter before moving to the next chapter.

Fact extraction may be parallel, but candidate generation is intentionally
serialized. Candidate generation merges into cumulative character backgrounds,
current status, outline nodes, and worldbuilding entries, so it must follow
`chapter_order` rather than the order in which fact extraction finishes.

### Tools That Work Without Moshu API

| Tool | Purpose |
|------|---------|
| `list_projects` | List all projects |
| `get_project_info` | Get project metadata |
| `get_moshu_usage_guide` | First-stop guide for external agents |
| `list_prompt_packs` | List available writing methods |
| `get_prompt_pack` | Get a specific writing method |
| `get_tool_playbook` | Get tool usage guide |
| `get_quality_rubric` | Get quality scoring criteria |
| `prepare_external_writing_context` | Build writing context package |
| `save_external_chapter_draft` | Store generated draft |
| `get_external_chapter_draft` | Retrieve stored draft |
| `record_external_quality_review` | Store quality review |
| `start_novel_creation_session` | Start new novel creation |
| `draft_novel_blueprint` | Generate blueprint schema |
| `review_novel_blueprint` | Review blueprint |
| `apply_novel_blueprint` | Create project from blueprint |
| `apply_external_story_updates` | Apply character/worldbuilding updates |
| `search_chapters` | Search existing chapters |
| `search_characters` | Search characters |
| `search_worldbuilding` | Search worldbuilding |
| `search_outline` | Search outline |
| `detect_forbidden_patterns` | Check for AI patterns |
| `start_external_cataloging_job` | Start API-free cataloging job |
| `get_next_external_cataloging_chapter` | Get next chapter for cataloging |
| `save_external_cataloging_facts` | Save extracted facts |
| `save_external_cataloging_candidates` | Save proposed candidates |
| `verify_external_cataloging_progress` | Check cataloging progress |

### Tools That Require Moshu API

These tools call the configured model API and will fail if no API key is set:

| Tool | Why It Needs API |
|------|-----------------|
| `chapter_writer` | Generates chapter text using LLM |
| `outline_writer` | Generates outline nodes using LLM |
| `character_writer` | Generates character cards using LLM |
| `worldbuilding_writer` | Generates worldbuilding entries using LLM |
| `rewrite_text` | Rewrites text using LLM |
| `expand_text` | Expands text using LLM |
| `continue_text` | Continues text using LLM |
| `roleplay_character` | Character roleplay using LLM |
| `dialogue_battle` | Multi-character dialogue using LLM |
| `evaluate_chapter` | Chapter quality evaluation using LLM |
| `design_plot` | Plot design using LLM |
| `suggest_conflicts` | Conflict suggestions using LLM |
| `detect_character_changes` | Character-change analysis using LLM |
| `detect_new_worldbuilding` | Worldbuilding detection using LLM |
| `detect_worldbuilding_conflicts` | Worldbuilding conflict check using LLM |
| `start_cataloging_job` | Internal cataloging job using Moshu's configured LLM |

## Troubleshooting

### Wrong Database Path

If Moshu can't find your project:

1. Check that `MOSHU_HOME` environment variable points to the correct directory
2. Default location: `%LOCALAPPDATA%\Moshu`
3. The database file should be `novel_agent.db` in that directory

### Missing Project ID

If you get "Project not found":

1. Make sure you're using the correct project ID
2. Open Moshu web UI and check the URL
3. The project ID is a UUID like `550e8400-e29b-41d4-a716-446655440000`

### Insufficient Model Balance

If tools fail with model errors:

1. Moshu uses your configured model API (OpenAI, Anthropic, etc.)
2. Check your API key balance
3. The MCP server itself doesn't need a model — only the tools that call LLMs do

### chapter_writer Fails Because No API Key

If `chapter_writer` or other LLM tools fail with "no API key configured":

1. You're trying to use tools that require Moshu's model API
2. Use the **No Moshu API mode** instead (see above)
3. Replace `chapter_writer` with:
   - `prepare_external_writing_context` to get context
   - External model generates text
   - `save_external_chapter_draft` to store the draft
   - `create_chapter` with `draft_id` to save

### SSE Not Connected

If the frontend doesn't show live updates:

1. Make sure the Moshu backend is running
2. Check browser console for SSE errors
3. The SSE endpoint is: `/api/v1/projects/{project_id}/agent-runs/{run_id}/stream`

### Tools Not Appearing

If MCP tools don't appear in your client:

1. Restart your MCP client after changing configuration
2. Check that the Moshu server starts without errors
3. Run `python scripts/moshu-mcp-server.py --help` to verify the entrypoint works

## Security Notes

- The MCP server runs in readonly mode by default
- API keys and model secrets are never exposed through MCP
- Write tools require explicit confirmation tokens
- All tool calls are logged for audit purposes

## Advanced: Custom Tool Policies

You can restrict which tools are available by configuring the MCP server:

```python
# In your custom entrypoint
from app.mcp.server import serve_stdio

serve_stdio(
    db=db,
    project_id="your-project-id",
    allowed_tiers={"readonly", "draft"},  # Allow draft tools too
)
```

## Further Reading

- [MCP Architecture Spec](spec.md)
- [Security Policy](security.md)
- [External Agent Live Session Spec](external-agent-live-session.md)
