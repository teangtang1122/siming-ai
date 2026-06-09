# Claude Code / Codex MCP Client Setup Guide

> This guide shows how to connect Claude Code or Codex to Moshu through MCP, enabling the external Agent to read project data and report progress to the Moshu web UI.

## Prerequisites

- Moshu installed (from source or packaged exe)
- A Moshu project created with some content
- Claude Code or Codex installed

## Quick Start

### Option 1: From Source

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "moshu": {
      "command": "python",
      "args": [
        "scripts/moshu-mcp-server.py",
        "--project-id",
        "YOUR_PROJECT_ID"
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
      "args": ["--mcp-server", "--project-id", "YOUR_PROJECT_ID"]
    }
  }
}
```

### Finding Your Project ID

1. Open Moshu in your browser
2. Go to your project
3. The project ID is in the URL: `http://localhost:8765/projects/YOUR_PROJECT_ID/...`

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
