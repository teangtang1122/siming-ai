# ADR 007: Agent Conversation Context and Native Tool Boundaries

## Status

Accepted for the release following `3.3.8`.

## Context

The workspace and creation agents previously bounded history with fixed message
counts and character truncation. Those shortcuts made prompt size predictable,
but they could silently discard an author's still-relevant decision. Replaying
every stored message indefinitely is also unsafe: model windows are finite, and
native tool calls and results must remain paired in each provider's protocol.

Project records such as characters, outlines, chapters, and creation artifacts
already provide task-specific, reloadable facts. Conversation history has a
different purpose: it records how the author's intent changed over time. It
therefore needs its own compaction contract rather than being copied into, or
replaced by, project records.

## Decision

- The complete transcript and durable run steps remain the audit source. They
  are never rewritten or deleted as a side effect of context compaction.
- Every message has a stable, positive sequence number within its conversation.
  Timestamp ordering is not an authority boundary.
- Each model step is assembled as `conversation_context_frame.v1`. It keeps the
  system contract, historical checkpoint, recent exact turns, current user
  message, execution ledger, and open native-tool transactions as distinct
  fields until the provider adapter renders them.
- The latest user message is stored and rendered byte-for-byte as the sole
  current intent. Historical text, selected text, attachments, routed data, and
  project metadata are explicitly labelled as untrusted reference data and
  cannot replace or outrank it.
- Recent exact history is selected dynamically from the verified model window.
  Fixed `takeLast`/slice counts and arbitrary character clipping are not context
  policy.
- Older completed, closed turns may be represented by a versioned structured
  checkpoint. Its semantic summary is non-authoritative navigation. Exact
  author quotes are accepted only after server validation of message identity,
  offsets, content hash, and active/superseded state.
- Project and execution facts are never accepted from model prose. Project IDs
  in a checkpoint are reread before use. Execution receipts and resource
  references are derived only from durable server or device records and their
  verified result hashes.
- Checkpoints are derived, replaceable data. Creation uses the same provider and
  model binding as the business turn, with no business tools, MCP servers, file
  grants, or hidden project access. Invalid output may be repaired once through
  the same isolated path; failure blocks the business turn without discarding
  history.
- Checkpoint publication uses an idempotency key and revision/CAS ownership.
  Model I/O occurs outside database transactions. A losing, cancelled, stale,
  or superseded attempt cannot become active later.
- Native assistant tool calls and model-visible results form atomic
  transactions. Pending or delivered transactions stay in their provider-native
  roles until the next model response consumes them. Only then may their full
  payload leave the prompt and be replaced by a deterministic receipt.
- A native transaction is admitted as a whole before any handler runs. Missing
  or duplicate call IDs, undeclared tools, empty/invalid/non-object JSON
  arguments, mixed controller/terminal batches, call-count overflow, or
  assistant-transaction overflow fail the turn with zero handlers executed.
  Tool arguments are preserved exactly for audit; the runtime does not repair
  them to `{}`.
- Tool result size is governed at the ToolSpec boundary through documented
  projection, pagination, ranges, or durable result references. The runtime
  never fabricates identifiers or silently truncates an arbitrary result.
- Every provider request is checked against the full projected request:
  prompts, tool schemas, wrappers, historical data, current input, provider
  state, possible tool-result growth, output reserve, and safety margin. Unknown
  model capacity fails with an actionable configuration error; model names are
  not used to guess a safe window.
- Providers may use different wire formats, but all adapters must preserve the
  same logical frame, latest-user position, native tool pairing, reasoning and
  provider state. A native-tool error never falls back to parsing ordinary text
  as an executable call.
- Android independent mode implements the same frame, hashes, conservative
  budget rules, checkpoint lifecycle, and error codes in local durable storage.
  Gateway transcript import is explicit, device-scoped, ordered, hashed, and
  idempotent; implicit history fields are not a second source of truth.

## Consequences

Short conversations continue with exact history and do not pay for a checkpoint
call. Long conversations retain a bounded active prompt without deleting the
author-visible transcript. A checkpoint can help the model navigate earlier
intent, but it cannot manufacture project state or execute a tool.

Models without a verified context profile use the shared 256K bounded fallback
with an `unverified` capacity marker and the UTF-8 byte counter. A matching saved
profile always takes precedence. Other unverified capacity/counter combinations
still stop before an Agent turn because silent truncation can cause irreversible
writes.

All new Agent entry points must reuse the shared context runtime and protocol
validator. Adding another fixed history slice, text fallback, provider-specific
checkpoint store, or a different unverified capacity default creates a second
authority path and violates this decision.
