---
id: assistant.workspace.fast
version: 3.0.0
scope: assistant
visibility: internal
inputs: [scope_label, outline_batch_count, auto_apply, tool_names]
output_format: text_reply
tool_policy: dynamic_selected
tools:
  - preview_writing_context
  - chapter_writer
  - evaluate_chapter
  - create_chapter
  - archive_chapter_after_write
  - outline_writer
  - create_outline_nodes
  - start_local_cli_agent_run
fragments: [assistant.workspace.quality]
budget:
  fixed_chars: 6200
  context_chars: 5000
golden_cases:
  - name: same-controller-contract
    required_text: ["evaluate_chapter", "archive_chapter_after_write"]
---
