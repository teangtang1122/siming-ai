# External No-API Chapter and Outline Drafting

This is the single generation path for an external MCP or local CLI model when Siming does not supply the model call. The database remains authoritative; project mirrors are read-only.

## Contract

- The user's latest message is the task. The currently open chapter or selected outline node is not an implicit target.
- The Agent reads project entities and chooses a real chapter-level `outline_node_id`. Siming validates project ownership and entity type; it does not infer the target from natural language.
- Only the quality writing prompt is available. De-AI rewriting and quality scoring are separate user actions that read the current editor draft.
- Generation creates one independent unsaved draft. It never updates a saved chapter.
- `save_external_chapter_draft` and `save_external_outline_draft` are terminal. After either succeeds, the model must stop.
- The author chooses **Save and catalog** or **Save only** in the UI. Cataloging is not started by draft generation.
- A pending unsaved draft or unfinished cataloging job blocks another chapter-writing turn. The application returns the durable state without calling the model again.

## Authoritative flow

1. Call `list_projects` or `get_project_info` to bind the task to the correct project.
2. Use outline/chapter search tools to inspect real entities and select the target required by the latest message. Do not reuse the UI selection as a target.
3. Call `prepare_task_context` or `prepare_external_writing_context` with the selected chapter-level `outline_node_id`. This returns only the hard anchors: target outline, project style/brief, latest author requirement, and explicit author pins.
4. Choose focused queries and call `search_task_context` as needed. Its compact candidates are for review only and are not silently injected into generation.
5. Call `submit_context_evidence` with only the candidate IDs needed for this chapter. An explicit empty list is valid when the hard anchors are sufficient. The server exact-fetches the chosen sources and verifies ownership and hashes. Selected exact sources have no fixed per-source character or source-count cap; the 32k-token context target is advisory rather than blocking, and usable input capacity is derived from the selected model's window after output reserve and safety margin. The tool returns `task_context` plus an unpredictable, one-use `context_selection_token`.
6. In the next model step, generate exactly one quality-mode chapter draft from that exact `task_context`.
7. Call `save_external_chapter_draft` with the same `project_id`, `outline_node_id`, `context_manifest_id`, and `context_selection_token`.
8. Stop immediately. The returned draft is loaded into the editor and remains outside the formal chapter table until the author acts.

Example terminal write:

```json
{
  "tool": "save_external_chapter_draft",
  "arguments": {
    "project_id": "PROJECT_ID",
    "outline_node_id": "CHAPTER_OUTLINE_ID",
    "context_manifest_id": "MANIFEST_ID",
    "context_selection_token": "TOKEN_FROM_SUBMIT_CONTEXT_EVIDENCE",
    "content": "Generated chapter text...",
    "source_agent": "external_cli"
  }
}
```

The result contains `draft_id`, the draft content, `turn_terminal=true`, and the two author actions `save_and_catalog` and `save_only`.

## Authoritative outline-proposal flow

1. Resolve the real parent and insertion anchor required by the latest user message. Empty `parent_id` is an explicit root position; it is not permission to infer a UI target.
2. Call `prepare_task_context` with `task_type="outline_planning"` and the resolved `parent_id`, `insert_after_id`, `batch_count`, and author requirements.
3. Use `search_task_context` as needed, then call `submit_context_evidence` with only the reviewed candidates.
4. In the next model step, use only the returned `task_context` to propose chapter nodes and their sections.
5. Call `save_external_outline_draft` with the same manifest, one-use selection token, and insertion position.
6. Stop immediately. The proposal remains outside the formal outline until the author edits and confirms it.

When a user asks to write the next chapter but no chapter-level outline exists, this outline-proposal flow is the only allowed result for that turn. It must not call `create_outline_nodes` or continue into chapter writing. “Confirm outline and write” is a later author action that confirms the proposal, receives a real chapter outline ID, and starts a new Agent turn.

## Separate author actions

The following actions are not part of the generation turn:

- **De-AI** and **quality scoring** operate on the current editor text, including an unsaved draft.
- **Save only** promotes the pending draft to a formal chapter without starting cataloging.
- **Save and catalog** promotes the draft and starts the one canonical cataloging job.
- If the author explicitly starts API-free cataloging, the external Agent uses the staged loop `facts -> candidates -> apply -> verify`, completing one chapter before requesting another.

## Failure behavior

- A missing or wrong-type outline ID is rejected; the Agent must read real entities and choose another ID.
- A missing or stale context manifest is returned as `needs_confirmation`; no alternate context renderer is used.
- A selection token is consumed when generation/storage starts. If that attempt fails before a draft is stored, the Agent must repeat the search review and `submit_context_evidence`; a consumed token cannot be replayed.
- A pending draft is returned as a blocking state and is never overwritten.
- A non-terminal cataloging job is returned as a blocking state and is never polled by the chat model.
- Database or worker failures are persisted as actionable task states. They do not trigger a hidden fallback workflow.
