# ADR 003: Compatibility Strangler

Status: accepted for 3.0

The 2.9 implementation is replaced use case by use case. Existing REST paths,
MCP tool names, CLI task files, database identities, and project mirrors remain
compatible throughout the migration.

Legacy code may use compatibility exports, but new modules cannot import legacy
routers or concrete service implementations. Baseline gates prevent debt from
growing while each pre-release reduces it.
