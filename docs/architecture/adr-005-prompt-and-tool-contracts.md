# ADR 005: Prompt and Tool Contracts

## Status

Accepted for `3.0.0-beta.1`.

## Context

Assistant, new-novel creation, cataloging, local CLI, MCP, and public prompt
packs previously assembled overlapping instructions in Python strings. Workspace
tools were registered in one large function, while OpenAI, MCP, and frontend
metadata rebuilt related schemas independently. A wording or field change could
therefore reach one entry point without reaching the others.

## Decision

- Built-in prompts use Markdown bodies with YAML front matter under
  `backend/prompt_specs`. Metadata declares identity, version, inputs, output,
  tool policy, budget, fragments, visibility, and deterministic golden cases.
- `PromptCompiler` rejects unknown placeholders and tools, fragment cycles,
  budget overflow, and failed golden assertions without contacting a model.
- The compiled template hash identifies the exact built-in source used by a
  public prompt pack. GUI contribution packages retain this base identity and
  hash alongside the author-readable diff.
- `ToolSpec` owns typed input and output models plus OpenAI, MCP, and frontend
  projections. Domain modules now own creation and continuity tool contracts.
- Existing `ToolDef` names and handlers remain compatibility adapters. Tools
  not yet migrated receive an exact legacy schema projection, so the public
  REST, MCP, CLI, and assistant contracts do not break during migration.
- Legacy prompt call sites may use the standalone prompt infrastructure while
  module application services receive the configured compiler through explicit
  composition.

## Consequences

Six initial PromptSpecs cover shared execution, workspace assistance, quality
chapter writing, staged new-novel creation, merged cataloging, and external
cataloging. Ten high-impact creation and continuity tools now derive schemas
from Pydantic models; the remaining tools can migrate incrementally. Fixed
prompt text is substantially smaller, but prompt edits now need to pass the
compiler and golden tests before release.
