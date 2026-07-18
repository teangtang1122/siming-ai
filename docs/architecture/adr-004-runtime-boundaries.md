# ADR 004: Model, Operation, and Context Runtime Boundaries

## Status

Accepted for `3.0.0-alpha.3`.

## Context

The legacy model gateway selected providers, decrypted configuration, opened
database sessions, executed API or CLI requests, and updated readiness state in
one module. Operation state was interpreted by routers and services separately.
Context indexing also mixed selection policy, model identity, database
transactions, and background execution. These paths formed dependency cycles
and made partial failures difficult to recover safely.

## Decision

- `model_runtime` owns provider-neutral configuration snapshots, deterministic
  task selection, readiness persistence adapters, and API or CLI execution.
- `operations` owns lifecycle, health, outcome, failure classification, request
  binding, reporting, persistence, and control actions.
- `context` owns context runtime binding and checkpointed rebuild execution.
- Application code depends on module ports and shared contracts. SQLAlchemy and
  process details remain in infrastructure adapters configured by composition.
- Context rebuilds commit each project independently. A failed project is
  recorded without rolling back indexes already completed for other projects.
- Existing `app.ai`, `app.services.operation_runtime`, and context helper paths
  remain thin compatibility facades during the strangler migration.

## Consequences

The gateway no longer opens ad hoc database sessions, operation routes no
longer query ORM models directly, and context no longer imports the gateway to
identify a model. Full removal of compatibility facades is deferred until all
callers move to module interfaces. The architecture baseline ratchets direct
commit calls downward as each use case adopts Unit of Work boundaries.
