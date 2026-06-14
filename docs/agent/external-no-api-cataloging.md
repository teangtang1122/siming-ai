# External No-API Cataloging Workflow

> Version: 0.1.0 (draft)
> Date: 2026-06-09
> Status: Phase 3 — specification

## 1. Overview

This document defines the exact workflow when Moshu has **no model API configured** and Claude Code / Codex performs cataloging (extracting characters, worldbuilding, outline, and chapter summaries from imported text).

Moshu 2.1 data boundary: the database is authoritative, while the project
folder is a read-only mirror. External agents may read mirrored chapter and
card files directly, but all facts, candidates, applies, creates, updates, and
deletes must be saved through Moshu MCP tools with the correct `project_id`.
Do not edit `chapters/`, `characters/`, `worldbuilding/`, `outline/`, or
`relationships/` files directly.

## 2. Step-By-Step Flow

### Step 1: Start External Cataloging Job

```
Tool: start_external_cataloging_job
Arguments: { "project_id": "PROJECT_ID" }
Result: { "job_id": "...", "chapter_count": 150 }
```

**API-free**: Yes. Creates a CatalogingJob with mode `external_agent`.

### Step 2: Get Next Chapter

```
Tool: get_next_external_cataloging_chapter
Arguments: { "job_id": "JOB_ID", "phase": "facts" }
Result: {
  "chapter_id": "...",
  "title": "Chapter 1",
  "content": "Full chapter text...",
  "character_alias_index": {"Hero": "char-123"},
  "worldbuilding_title_index": {"Magic System": "wb-456"},
  "outline_neighborhood": {...},
  "prompt_pack": {...}
}
```

**API-free**: Yes. Reads authoritative state from the database. The agent may
also use `get_project_files_info` / `search_project_files` or direct file reads
for long context, but writes still go through Moshu tools.

Use `phase: "facts"` for the parallel fact extraction stage. Multiple external
agents may fetch and analyze different chapters in this phase.

### Step 3: Extract Facts (External Agent)

The external agent analyzes the chapter text and extracts:
- Character appearances and state changes
- Worldbuilding elements (locations, rules, factions)
- Plot events and conflicts
- Chapter summary (200 words max)

**No Moshu tool called** — this happens entirely in the external model.

### Step 4: Save Facts

```
Tool: save_external_cataloging_facts
Arguments: {
  "job_id": "JOB_ID",
  "chapter_id": "CHAPTER_ID",
  "facts": [
    {"type": "character_appearance", "data": {"name": "Hero", "state": "injured"}},
    {"type": "worldbuilding", "data": {"title": "Magic System", "content": "..."}},
    {"type": "plot_event", "data": {"description": "Hero defeats villain"}}
  ]
}
```

**API-free**: Yes. Stores facts in CatalogingFact table.

### Step 5: Generate Candidates (External Agent)

The external agent generates candidate updates:
- New characters (name, personality, background)
- Character updates (current state changes)
- New worldbuilding entries
- Outline nodes (one per chapter)
- Chapter summaries

**No Moshu tool called** — this happens entirely in the external model.

Before generating candidates, call `get_next_external_cataloging_chapter` with
`phase: "candidates"` and only generate candidates for the chapter returned by
Moshu. Candidate generation must follow chapter order, not the order in which
fact extraction finished. This keeps character backgrounds, aliases, current
status, outline nodes, and worldbuilding merges chronological.

### Step 6: Save Candidates

```
Tool: save_external_cataloging_candidates
Arguments: {
  "job_id": "JOB_ID",
  "chapter_id": "CHAPTER_ID",
  "candidates": [
    {"type": "character", "action": "create", "name": "Hero", "personality": "Brave"},
    {"type": "character", "action": "update", "id": "char-123", "current_location": "Castle"},
    {"type": "worldbuilding", "action": "create", "title": "Magic System", "content": "..."},
    {"type": "outline", "action": "create", "title": "Chapter 1: The Beginning"},
    {"type": "chapter_summary", "summary": "Hero starts journey..."}
  ]
}
```

**API-free**: Yes. Stores candidates in CatalogingCandidate table.

### Step 7: Apply Candidates

```
Tool: apply_pending_cataloging
Arguments: { "job_id": "JOB_ID" }
Result: { "status": "ok", "data": { "events": [...] } }
```

**API-free**: Yes. Uses existing cataloging applier.

### Step 8: Verify Progress

```
Tool: verify_external_cataloging_progress
Arguments: { "job_id": "JOB_ID" }
Result: {
  "chapters_processed": 10,
  "chapters_total": 150,
  "characters_count": 5,
  "worldbuilding_count": 3,
  "outline_nodes_count": 10,
  "pending_candidates": 0,
  "failed_chapters": 0,
  "warnings": []
}
```

**API-free**: Yes. Reads from database.

### Step 9: Final Verify

```
Tool: verify_external_cataloging_progress
Arguments: { "job_id": "JOB_ID" }
Result: {
  "chapters_processed": 10,
  "characters_found": 5,
  "worldbuilding_found": 3,
  "outline_nodes_created": 10,
  "pending_candidates": 0,
  "warnings": []
}
```

**Success criteria**: `pending_candidates == 0` and `characters_count > 0`.

## 3. Forbidden Internal API Tools

These tools must NOT be called in external no-API mode:

| Tool | Reason |
|------|--------|
| `start_cataloging_job` | Uses internal LLM orchestrator |
| `chapter_writer` | Generates text using LLM |
| `character_writer` | Generates characters using LLM |
| `outline_writer` | Generates outline using LLM |
| `worldbuilding_writer` | Generates worldbuilding using LLM |
| `design_plot` | Uses LLM for plot design |
| `evaluate_chapter` | Uses LLM for evaluation |
| `suggest_conflicts` | Uses LLM for suggestions |
| `detect_worldbuilding_conflicts` | Uses LLM for detection |

## 4. Merge Rules

### Character Merge
- Current state fields (location, goal, status): **overwrite** old state
- Background, appearance, system prompt: **append** new information
- Aliases: preserve and use for duplicate resolution
- Duplicate characters with same name: merge into one

### Worldbuilding Merge
- Same title: **semantic merge** (update content, don't create duplicate)
- Different title: create new entry

### Outline
- Each chapter creates one outline node unless explicitly targeting an existing node
- Nodes are created under the volume/chapter hierarchy

## 5. Example Session

```
# Claude Code cataloging a 150-chapter novel (no Moshu API)

## 1. Start job
> start_external_cataloging_job({ "project_id": "proj-123" })
< { "job_id": "job-001", "chapter_count": 150 }

## 2. Process chapter 1
> get_next_external_cataloging_chapter({ "job_id": "job-001", "phase": "facts" })
< { "chapter_id": "ch-1", "title": "The Beginning", "content": "..." }

## 3. [Claude Code analyzes chapter 1]

## 4. Save facts
> save_external_cataloging_facts({
    "job_id": "job-001",
    "chapter_id": "ch-1",
    "facts": [
      {"type": "character_appearance", "data": {"name": "Hero", "state": "young"}},
      {"type": "worldbuilding", "data": {"title": "Village", "dimension": "geography"}}
    ]
  })
< { "status": "ok", "facts_saved": 2 }

## 5. Ask for the next allowed candidate chapter, then save candidates
> get_next_external_cataloging_chapter({ "job_id": "job-001", "phase": "candidates" })
< { "chapter_id": "ch-1", "title": "The Beginning", "content": "..." }

> save_external_cataloging_candidates({
    "job_id": "job-001",
    "chapter_id": "ch-1",
    "candidates": [
      {"type": "character", "action": "create", "name": "Hero", "role_type": "protagonist"},
      {"type": "outline", "action": "create", "title": "Chapter 1: The Beginning"},
      {"type": "chapter_summary", "summary": "Hero begins journey..."}
    ]
  })
< { "status": "ok", "candidates_saved": 3 }

## 6. Apply chapter 1 candidates
> apply_pending_cataloging({ "job_id": "job-001" })
< { "status": "ok", "data": { "events": [...] } }

## 7. Verify progress before moving on
> verify_external_cataloging_progress({ "job_id": "job-001" })
< { "chapters_processed": 1, "characters_count": 1, "worldbuilding_count": 1, "outline_nodes_count": 1, "pending_candidates": 0 }

## 8. Facts can be extracted in parallel, but repeat candidate steps in chapter_order...

## 9. Final verify
> verify_external_cataloging_progress({ "job_id": "job-001" })
< { "chapters_processed": 150, "characters_count": 45, "worldbuilding_count": 23, "outline_nodes_count": 150, "pending_candidates": 0 }
```
