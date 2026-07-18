# Database Migration And Recovery

Siming keeps SQLite as the authority and creates a verified backup before a schema upgrade. Unknown database structures enter read-only recovery mode instead of receiving guessed columns.

## Rehearse Before An RC Upgrade

Close Siming, locate `novel_agent.db`, then run the rehearsal against a disposable copy:

```powershell
backend\.venv\Scripts\python.exe scripts\rehearse_database_migration.py `
  "C:\path\to\novel_agent.db" `
  --working-copy ".build\migration-rehearsal.db" `
  --report ".build\migration-rehearsal.json"
```

Success requires all of the following:

- Source SHA-256 is unchanged.
- Source and migrated copy both pass `PRAGMA integrity_check`.
- Existing project, chapter, character, outline and worldbuilding row counts do not decrease.
- Alembic reaches the expected head revision.

The working copy may contain private novel data. Do not attach it to public issues. The JSON report contains the local source path, so review it before sharing.

## Automatic Backups

Backups are written beside the database under `backups/` and include the application version and UTC timestamp. The SQLite backup API captures committed WAL data. A backup is not accepted until its integrity check returns `ok`.

## Read-Only Recovery

If Siming cannot recognize or migrate the database:

1. Leave the original database untouched.
2. Keep the reported backup and copy it to another disk.
3. Run the rehearsal tool on a copy and preserve its JSON report.
4. Install a repaired release or open an issue with the report after removing private paths.
5. Do not import project mirror files over the database unless you explicitly choose the repair import action.

Restoring a backup should be done only while Siming is fully closed. Keep the failed database under a different filename until the restored copy has opened successfully and project counts have been checked.
