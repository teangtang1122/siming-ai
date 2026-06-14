# External No-API Writing Workflow

> Version: 0.1.0 (draft)
> Date: 2026-06-09
> Status: Phase 0 — specification

## 1. Overview

This document defines the exact workflow when Moshu has **no model API configured** and Claude Code / Codex performs all writing and review tasks. In this mode, Moshu provides context, prompt packs, storage, telemetry, and write APIs — the external model does generation and review.

Moshu 2.1 data boundary: the database is authoritative. The project folder is
a read-only mirror that external agents may inspect for long-context reading.
Do not edit canonical mirror folders directly; all chapter saves, story
updates, character state changes, outline changes, and worldbuilding changes
must use Moshu MCP tools with the correct `project_id`.

## 2. Step-By-Step Flow

### Step 1: List Projects

```
Tool: list_projects
Arguments: {}
Result: List of projects with IDs and titles
```

**API-free**: Yes. Pure database read.

### Step 2: Select Project

```
Tool: get_project_info
Arguments: { "id": "PROJECT_ID" }
Result: Project metadata, settings, writing style
```

**API-free**: Yes. Pure database read.

### Step 3: Get Prompt Pack

```
Tool: get_prompt_pack
Arguments: { "scope": "chapter_writing", "mode": "quality" }
Result: Complete writing methodology, workflow, quality rubric, forbidden patterns
```

**API-free**: Yes. Reads from database.

### Step 4: Prepare Context

```
Tool: prepare_external_writing_context
Arguments: {
  "project_id": "PROJECT_ID",
  "outline_node_id": "NODE_ID",
  "mode": "quality",
  "include_prompt_pack": true
}
Result: Context sections, outline, characters, worldbuilding, prompt pack, warnings
```

**API-free**: Yes. Uses RAG/context packer without LLM calls.

For extra long-context inspection, the agent may also call
`get_project_files_info` and read/search the project folder mirror. File reads
are only for context; saving must still use the tools below.

### Step 5: External Model Writes

The external agent (Claude Code / Codex) generates chapter text using:
- The prompt pack's writing methodology
- The context sections from Step 4
- The quality rubric for self-guidance
- The forbidden patterns list for anti-AI rules

**No Moshu tool called** — this happens entirely in the external model.

### Step 6: External Model Self-Reviews

The external agent reviews its own output using:
- The quality rubric from the prompt pack
- The forbidden patterns list
- Character consistency checks
- Worldbuilding rule checks

**No Moshu tool called** — this happens entirely in the external model.

### Step 7: Save Draft

```
Tool: save_external_chapter_draft
Arguments: {
  "project_id": "PROJECT_ID",
  "title": "Chapter Title",
  "content": "Generated chapter text...",
  "outline_node_id": "NODE_ID",
  "source_agent": "claude-code",
  "quality_review_json": "{ \"scores\": {...}, \"pass\": true }"
}
Result: { "draft_id": "DRAFT_ID", "content_ref": "DRAFT_ID" }
```

**API-free**: Yes. Stores draft in database.

### Step 8: Record Quality Review (Optional)

```
Tool: record_external_quality_review
Arguments: {
  "draft_id": "DRAFT_ID",
  "scores": { "opening_hook": 8, "plot_progression": 7, ... },
  "issues": ["Pacing slow in middle section"],
  "revision_suggestions": ["Add more tension in paragraph 3"],
  "pass": true,
  "reviewer_model": "claude-sonnet-4-6"
}
Result: Review summary
```

**API-free**: Yes. Stores review in database.

### Step 9: Create Chapter

```
Tool: create_chapter
Arguments: {
  "title": "Chapter Title",
  "draft_id": "DRAFT_ID",
  "outline_node_id": "NODE_ID",
  "summary": "Brief chapter summary",
  "involved_characters": ["Hero", "Villain"]
}
Result: { "id": "CHAPTER_ID", "title": "Chapter Title", ... }
```

**API-free**: Yes. Creates database record from stored draft.

### Step 10: Apply Story Updates

```
Tool: apply_external_story_updates
Arguments: {
  "project_id": "PROJECT_ID",
  "chapter_id": "CHAPTER_ID",
  "updates": {
    "characters": [
      { "id": "CHAR_ID", "current_location": "New Location", "current_goal": "New Goal" }
    ],
    "worldbuilding": [
      { "title": "New Rule", "content": "Rule description", "dimension": "power_system" }
    ],
    "chapter_summary": "Brief summary for future context"
  },
  "mode": "auto"
}
Result: Applied update counts
```

**API-free**: Yes. Creates/updates database records.

## 3. API-Free Tools

These tools work without any Moshu model API configured:

| Tool | Purpose |
|------|---------|
| `list_projects` | List all projects |
| `get_project_info` | Get project metadata |
| `list_prompt_packs` | List available prompt packs |
| `get_prompt_pack` | Get a specific prompt pack |
| `get_tool_playbook` | Get tool usage guide |
| `get_quality_rubric` | Get quality scoring criteria |
| `prepare_external_writing_context` | Build writing context package |
| `save_external_chapter_draft` | Store generated draft |
| `record_external_quality_review` | Store quality review |
| `create_chapter` | Create chapter from draft |
| `update_chapter` | Update chapter content |
| `apply_external_story_updates` | Apply character/worldbuilding updates |
| `search_chapters` | Search existing chapters |
| `search_characters` | Search characters |
| `search_worldbuilding` | Search worldbuilding |
| `search_outline` | Search outline |
| `detect_character_changes` | Detect character state changes |
| `detect_new_worldbuilding` | Detect unrecorded worldbuilding |
| `detect_forbidden_patterns` | Check for AI patterns |

## 4. API-Backed Tools (Skip in No-API Mode)

These tools require Moshu's configured model API and should be **skipped** when no API is available:

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
| `detect_worldbuilding_conflicts` | Conflict detection using LLM |
| `preview_writing_context` | Context preview (may use LLM for selection) |

## 5. Frontend Telemetry Events

When external writing is in progress, the following events should be displayed:

| Event | Description |
|-------|-------------|
| `prompt_pack_selected` | Which prompt pack was fetched |
| `context_prepared` | Context sections and warnings |
| `draft_received` | External draft saved |
| `review_recorded` | Quality review recorded |
| `chapter_created` | Chapter created from draft |
| `story_updates_applied` | Character/worldbuilding updates applied |

## 6. Failure Handling

### Missing Outline

If the target outline node doesn't exist:
- `prepare_external_writing_context` returns a warning
- The external agent should ask the user to create an outline first
- Or use `outline_writer` (requires API) to generate one

### Missing Context

If RAG returns insufficient context:
- `prepare_external_writing_context` includes warnings
- The external agent should note the gaps and proceed cautiously
- Or ask the user for additional information

### User Rejects Draft

If the user rejects the external draft:
- The draft remains stored but is not promoted to a chapter
- The external agent can revise and save a new draft
- The rejection is recorded as a telemetry event

### Write Confirmation Needed

If the project requires write confirmation:
- `create_chapter` returns a confirmation request
- The user must confirm in the Moshu UI
- The external agent waits for confirmation before proceeding

## 7. Example Session

```
# Claude Code session with Moshu (no Moshu API key)

## 1. List projects
> list_projects()
< [{ "id": "proj-123", "title": "My Novel" }]

## 2. Get writing prompt pack
> get_prompt_pack({ "scope": "chapter_writing", "mode": "quality" })
< { "system_prompt": "...", "workflow": [...], "quality_rubric": {...} }

## 3. Prepare context
> prepare_external_writing_context({
    "project_id": "proj-123",
    "outline_node_id": "node-456",
    "mode": "quality"
  })
< {
    "prompt_pack": { ... },
    "context_sections": [ ... ],
    "outline": { "title": "Chapter 5", "summary": "..." },
    "characters": [ ... ],
    "worldbuilding": [ ... ],
    "forbidden_patterns": [ ... ],
    "quality_rubric": { ... },
    "warnings": []
  }

## 4. [Claude Code writes chapter using prompt pack + context]

## 5. Save draft
> save_external_chapter_draft({
    "project_id": "proj-123",
    "title": "Chapter 5: The Battle",
    "content": "The rain fell...",
    "outline_node_id": "node-456",
    "source_agent": "claude-code"
  })
< { "draft_id": "draft-789" }

## 6. Record review
> record_external_quality_review({
    "draft_id": "draft-789",
    "scores": { "opening_hook": 8, "plot_progression": 7, ... },
    "pass": true,
    "reviewer_model": "claude-sonnet-4-6"
  })
< { "review_id": "rev-012", "pass": true }

## 7. Create chapter
> create_chapter({
    "title": "Chapter 5: The Battle",
    "draft_id": "draft-789",
    "outline_node_id": "node-456",
    "involved_characters": ["Hero", "Villain"]
  })
< { "id": "ch-345", "title": "Chapter 5: The Battle", "word_count": 2500 }

## 8. Apply story updates
> apply_external_story_updates({
    "project_id": "proj-123",
    "chapter_id": "ch-345",
    "updates": {
      "characters": [
        { "id": "char-hero", "current_location": "Battlefield", "current_goal": "Defeat Villain" }
      ],
      "chapter_summary": "Hero confronts Villain in a fierce battle..."
    },
    "mode": "auto"
  })
< { "characters_updated": 1, "chapter_summary_saved": true }
```
