# PlotPilot Stability Lessons

This note records the parts of `shenminglinyi/PlotPilot` that Siming should
absorb as engineering direction, without copying PlotPilot code or changing
Siming's stack.

## Adopt

- CI split by surface: backend tests and frontend build should run separately,
  with path filters so small PRs get fast feedback.
- Database authority: project folders are readable mirrors. Writes to chapters,
  characters, outline, worldbuilding, and relationships must go through Siming
  tools so database state, versions, and file mirrors stay aligned.
- User-facing traces: runs should expose model source, tool mode, failure class,
  checkpoint/snapshot id, storage target, and next action when available.
- Checkpoint mindset: writing flows should create a recoverable chapter version
  before replacing content, then expose rollback in both UI and tools.
- Prompt self-tests: writing prompts need small golden cases with fact locks,
  completed-beat locks, revealed-clue lists, and review checklists.
- Beginner diagnostics: setup and model/CLI failures should explain what is
  missing and where to click next, not only show raw errors.

## Do Not Adopt Blindly

- Do not migrate Siming from React/FastAPI/PyInstaller to Vue/Tauri.
- Do not copy implementation files without a separate license review.
- Do not make local model/runtime paths default again until they pass the same
  diagnostics and compatibility tests as API and CLI paths.

## Current Siming Guardrails

- `sync_project_files` defaults to database-to-files export. File-to-database
  import requires `confirm_import_from_files=true`.
- External and local CLI writers must save long text through Siming tools, then
  call `create_chapter` or `update_chapter`.
- Storage health reports expose orphan chapter mirror files so direct filesystem
  writes can be repaired explicitly.
