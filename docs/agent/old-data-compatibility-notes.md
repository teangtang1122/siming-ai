# Old Data Compatibility Notes

> Date: 2026-06-09
> Version: 1.3.9+

## New Database Tables

The following tables are added in this version. They are created automatically by runtime schema sync on startup.

| Table | Purpose |
|-------|---------|
| `agent_runs` | External Agent run tracking |
| `agent_run_events` | External Agent run events |
| `external_agent_settings` | Per-project external Agent permission settings |
| `public_prompt_packs` | Public prompt pack storage |
| `method_cards` | Method card storage |
| `novel_creation_sessions` | Novel creation session tracking |

## New Columns

No existing tables have new columns. All new data uses new tables.

## New Relationships

The `projects` table gains two new relationships:
- `agent_runs` — one-to-many with `agent_runs`
- `external_agent_settings` — one-to-one with `external_agent_settings`

These are SQLAlchemy-only relationships and do not modify the `projects` table schema.

## Migration Strategy

Siming uses **runtime schema sync** via `ensure_runtime_schema()`. On startup:

1. All `Base.metadata.create_all(engine)` is called.
2. Missing tables are created.
3. Missing columns are added.
4. Existing data is preserved.

No manual migration steps are required.

## Verified Compatibility

- Old projects (created by v1.3.7 or earlier) open without errors.
- Old chapters, characters, outline nodes, worldbuilding entries are preserved.
- Old skills, scheduled tasks, and settings are preserved.
- New features (prompt packs, external writing, novel creation) work on old projects.

## Rollback

If you need to roll back to a previous version:
1. The new tables will simply be ignored by the old code.
2. No data loss occurs.
3. The new tables remain in the database but are not used.
