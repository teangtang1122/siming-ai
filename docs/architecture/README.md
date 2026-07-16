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
will move to a durable post-commit synchronization queue in alpha.2.

## Enforced Rules

- Import cycles are forbidden.
- New 3.0 modules cannot call `Session.commit()` directly.
- `UnitOfWork` owns commit and rollback at command boundaries.
- New modules over 1000 lines or functions over 150 lines fail CI.
- Existing oversized modules and direct commits are frozen by a ratcheting
  baseline and cannot grow.
- Prompt declarations cannot import the workspace runtime.
- The model gateway cannot import the context orchestrator.

Run the checks with:

```text
python scripts/check-architecture.py
cd backend
ruff check app/architecture app/bootstrap app/modules
lint-imports
```

## Release Stages

- `3.0.0-alpha.1`: factory, lifespan, Alembic baseline, contracts, UoW, gates.
- `3.0.0-alpha.2`: story writes, versions, and content mirror synchronization.
- `3.0.0-alpha.3`: model runtime, local CLI, operations, and context.
- `3.0.0-beta.1`: PromptSpec, ToolSpec, assistant, creation, and continuity.
- `3.0.0-beta.2`: frontend feature boundaries and generated API types.
- `3.0.0-rc.1`: cleanup, migration rehearsals, security, recovery, performance.
- `3.0.0`: stable release after RC validation.
