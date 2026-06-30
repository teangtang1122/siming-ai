# Shared Prompt Pack Contract

> Version: 0.1.0 (draft)
> Date: 2026-06-09
> Status: Phase 0 — specification

## 1. Overview

This document defines how the internal project assistant and external agents (Claude Code, Codex) access the same writing methods through Siming's prompt pack system.

### 1.1 Problem

Currently, Siming's internal writing prompts (chapter_writer, evaluate_chapter, etc.) are embedded in Python code and not directly accessible to external agents. External agents can call these tools but cannot see the methodology behind them.

### 1.2 Solution

Introduce **public prompt packs** — structured, versioned writing method documents that:
- Can be read by both internal assistant and external agents
- Summarize Siming's writing methodology without exposing private system prompts
- Include workflow steps, quality rubrics, tool playbooks, and forbidden patterns
- Are stored in the database and indexed in RAG

## 2. Hidden vs Public Prompts

### 2.1 Hidden Internal System Prompts

These remain private and are NOT exposed through MCP or prompt pack APIs:

- `backend/app/prompts/packs/chapter_quality.py` — full system prompt with internal instructions
- `backend/app/prompts/packs/workspace_fast.py` — fast mode controller prompt
- `backend/app/prompts/packs/workspace_quality.py` — quality mode controller prompt
- `evaluate_chapter` internal scoring prompt
- `chapter_writer` internal technique assembly
- Internal RAG/context packer selection algorithms

### 2.2 Public Prompt Packs

These are exposed through MCP and prompt pack APIs:

- Writing methodology summaries
- Quality rubric dimensions and scoring criteria
- Tool workflow playbooks (how to use tools step by step)
- Forbidden pattern lists
- Context selection policies
- Output contracts (what the agent should produce)

## 3. Prompt Pack Fields

Every public prompt pack has the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique identifier (e.g., `chapter_writing_quality`) |
| `version` | str | Semantic version (e.g., `1.0.0`) |
| `scope` | str | What this pack covers (see Section 4) |
| `title` | str | Human-readable title |
| `summary` | str | One-paragraph description |
| `system_prompt` | str | The public system prompt for this workflow |
| `workflow` | list[Step] | Ordered workflow steps |
| `quality_rubric` | dict | Quality dimensions and scoring criteria |
| `tool_playbook` | dict | How to use each tool in this workflow |
| `forbidden_patterns` | list[str] | Patterns to avoid in output |
| `context_policy` | dict | What context to select and why |
| `output_contract` | dict | Expected output format and validation |

### 3.1 Workflow Step

```json
{
  "step": 1,
  "name": "prepare_context",
  "description": "Read outline, recent summaries, characters, and worldbuilding",
  "tools": ["search_outline", "search_chapters", "search_characters", "search_worldbuilding"],
  "required": true,
  "output": "context_sections"
}
```

### 3.2 Quality Rubric

```json
{
  "dimensions": [
    {
      "name": "opening_hook",
      "description": "Does the opening grab attention?",
      "max_score": 10,
      "criteria": ["Surprising opening", "Clear scene setting", "Character voice established"]
    }
  ],
  "passing_score": 60,
  "max_score": 80
}
```

### 3.3 Tool Playbook

```json
{
  "create_chapter": {
    "scenario": "external_writing",
    "steps": [
      "Call prepare_external_writing_context to get context",
      "Write the chapter text following the prompt pack",
      "Call save_external_chapter_draft to store the draft",
      "Call record_external_quality_review to log self-review",
      "Call create_chapter with draft_id/content_ref to save"
    ],
    "required_args": ["title", "draft_id"],
    "optional_args": ["outline_node_id", "summary", "involved_characters"]
  }
}
```

## 4. Prompt Pack Scopes

| Scope | Description |
|-------|-------------|
| `new_project` | Creating a new novel from scratch |
| `chapter_writing` | Writing a single chapter |
| `chapter_review` | Reviewing chapter quality |
| `character_design` | Designing a character card |
| `worldbuilding` | Designing worldbuilding entries |
| `outline_planning` | Planning outline structure |
| `anti_ai_review` | Detecting AI-like patterns |

## 5. Compatibility Rules

### 5.1 Internal Assistant

The internal workspace prompt builder (`backend/app/services/agent/prompt_builder.py`) must:
- Consume the same public prompt pack data that external agents can fetch
- Inject public prompt pack sections into the system prompt
- Record the prompt pack version in plan metadata

### 5.2 External Agents

External agents (Claude Code, Codex) can:
- Fetch prompt packs via `list_prompt_packs` and `get_prompt_pack` tools
- Follow the workflow steps in the pack
- Use the quality rubric for self-review
- Use the tool playbook for save/review operations

### 5.3 Version Matching

When the internal assistant writes a chapter, the prompt pack version is recorded in the chapter metadata. This allows:
- Auditing which writing method was used
- Comparing results across different prompt versions
- Ensuring external and internal agents use equivalent methods

## 6. Redaction Rules

Public prompt packs must NEVER include:

- API keys, model secrets, tokens, credentials
- Private environment paths
- Hidden chain-of-thought requirements
- Internal implementation details that could be exploited
- Raw database queries or ORM patterns

Public prompt packs SHOULD include:

- Writing methodology and principles
- Quality criteria and scoring
- Tool usage workflows
- Forbidden patterns and anti-AI rules
- Context selection guidelines

## 7. Storage And Indexing

### 7.1 Database Storage

Prompt packs are stored in the `public_prompt_packs` table:
- Built-in packs are seeded on first access
- Project-level overrides are stored separately
- Version history is maintained

### 7.2 RAG Indexing

Prompt packs are indexed in RAG with source types:
- `prompt_pack` — the full pack
- `method_card` — individual method sections
- `tool_playbook` — tool usage guides

### 7.3 Refresh

The RAG index is refreshed when:
- A prompt pack is created or updated
- A project-level override is applied
- A built-in pack version is upgraded

## 8. Backward Compatibility

- Existing `ToolRegistry` tool definitions are unchanged
- Existing `chapter_writer` and `evaluate_chapter` tools continue to work
- Internal system prompts are not modified (they consume public packs as additional context)
- Existing MCP permission packs continue to work

## 9. Out of Scope

- Exposing full internal system prompts through MCP
- Allowing external agents to modify built-in prompt packs
- Real-time prompt pack synchronization across clients
- Prompt pack marketplace or sharing
