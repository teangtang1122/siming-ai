# ADR 002: Transactions And Migrations

Status: accepted for 3.0

Alembic owns schema history from `300a1_baseline`. A recognized unversioned
Siming database is backed up and reconciled once. An unknown schema enters
read-only recovery mode.

Command use cases own one `UnitOfWork`. Repositories may flush but cannot commit.
Database commit happens before derived file synchronization; a mirror failure is
retryable and never rolls back authoritative data.

## Alpha.2 Outbox Contract

Every story write queues a `ContentSyncIntent` in the same database transaction
as the authoritative change. The intent is persisted as a `content_sync_jobs`
row with a stable deduplication key. Rollback removes both the story change and
its queued projection.

After commit, `ContentSyncProcessor` projects the requested project, chapter,
outline, character, worldbuilding, relationship, or deletion change. A failed
projection records its error and retry time while leaving the story transaction
committed. Startup recovery resets interrupted jobs and resumes eligible work.

Legacy workspace tools call the application-facing content-sync port while they
move behind command use cases. Only the infrastructure adapter may call the
low-level file mirror functions. A mirror read may enqueue an independent repair
transaction, but it must never commit the caller's pending work.
