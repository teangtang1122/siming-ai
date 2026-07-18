# Siming 3.0 Architecture

Siming 3.0 is a modular monolith. It keeps one deployable Windows application,
one authoritative SQLite database, the existing `/api/v1` surface, and stable
MCP tool names while moving behavior behind explicit application boundaries.

## Module Map

The canonical modules are `story`, `creation`, `continuity`, `assistant`,
`operations`, `model_runtime`, `context`, and `integrations`.

Each module grows through four folders:

1. `domain`: business rules and value objects.
2. `application`: commands, queries, ports, and transaction boundaries.
3. `interfaces`: HTTP, MCP, CLI, and event adapters.
4. `infrastructure`: SQLAlchemy repositories and external implementations.

Interfaces and infrastructure may depend on application contracts. Application
may depend on domain. Domain must remain independent.

## Compatibility Strategy

The 2.9 implementation remains active while use cases migrate one by one.
`app.database.models` stays as a compatibility export, existing routes and tool
names remain stable, and Alembic preserves all table names and primary keys.

The database is the only write authority. File mirrors are derived content and
are projected through the durable `content_sync_jobs` outbox introduced in
alpha.2. A story command commits its business rows and synchronization intents
atomically; projection runs only after that commit and can be retried safely.

Alpha.3 moves model configuration, provider selection, operation state, and
context rebuild execution behind module-owned ports. The model gateway receives
configuration snapshots instead of opening database sessions. Long-running
features publish one operation vocabulary, while context rebuilds commit one
project checkpoint at a time so a later failure cannot erase completed work.

Beta.1 introduces compiled PromptSpecs and typed ToolSpecs. Assistant, creation,
and continuity prompts now share versioned Markdown/YAML sources, deterministic
golden checks, and source hashes. OpenAI, MCP, and frontend tool projections use
the same catalog while unmigrated tools retain exact compatibility schemas.

Beta.2 establishes `app`, `shared`, and `features` as frontend dependency
boundaries. TanStack Query owns reusable server state, Zustand is limited to
cross-page UI state, and core project, onboarding, and operation responses are
generated from explicit FastAPI response contracts instead of handwritten
duplicates.

## Enforced Rules

- Import cycles are forbidden.
- New 3.0 modules cannot call `Session.commit()` directly.
- `UnitOfWork` owns commit and rollback at command boundaries.
- New modules over 1000 lines or functions over 150 lines fail CI.
- Existing oversized modules and direct commits are frozen by a ratcheting
  baseline and cannot grow.
- Prompt declarations cannot import the workspace runtime.
- The model gateway cannot import the context orchestrator.
- Frontend `shared` cannot import `app`, `features`, or `pages`; features cannot
  import `app` or `pages`.
- OpenAPI generated types must be current before frontend CI can pass.

Run the checks with:

```text
python scripts/check-architecture.py
cd backend
ruff check app/architecture app/bootstrap app/modules
lint-imports
python scripts/compile_prompts.py
```

## Release Stages

- `3.0.0-alpha.1`: factory, lifespan, Alembic baseline, contracts, UoW, gates.
- `3.0.0-alpha.2`: story writes and versions use explicit command boundaries;
  content mirrors use a durable, retryable post-commit outbox.
- `3.0.0-alpha.3`: model runtime uses configuration ports; operations share one
  state machine and service; context rebuilds use durable per-project UoW
  checkpoints; legacy imports remain compatibility-only facades.
- `3.0.0-beta.1`: compiled PromptSpec sources, typed ToolSpec projections, and
  assistant/creation/continuity compatibility facades.
- `3.0.0-beta.2`: frontend feature boundaries and generated API types.
- `3.0.0-rc.1`: cleanup, migration rehearsals, security, recovery, performance.
- `3.0.0`: stable release after RC validation.
