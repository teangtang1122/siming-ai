# ADR 002: Transactions And Migrations

Status: accepted for 3.0

Alembic owns schema history from `300a1_baseline`. A recognized unversioned
Siming database is backed up and reconciled once. An unknown schema enters
read-only recovery mode.

Command use cases own one `UnitOfWork`. Repositories may flush but cannot commit.
Database commit happens before derived file synchronization; a mirror failure is
retryable and never rolls back authoritative data.
