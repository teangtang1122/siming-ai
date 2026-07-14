# Siming 2.8.0 interaction contract

This document prevents future feature work from gradually rebuilding the
confusing interaction patterns removed in 2.8.0.

## Human factors principles

1. One screen has one dominant task. Secondary controls must not compete with
   the next meaningful action.
2. Recognition beats recall. Navigation names describe author goals, and the
   current project and current view remain visible.
3. Progressive disclosure is the default. Model diagnostics, version history,
   destructive actions, and advanced settings stay available without occupying
   the primary workspace.
4. Feedback must answer three questions: what is happening, what changed, and
   what the author can do next.
5. Recovery is part of the normal flow. Failed assistant turns can be returned
   to the composer; chapter versions can be compared and restored; stale novel
   creation stages are named in author-facing language.
6. The database remains the source of truth. Interface shortcuts cannot bypass
   normal persistence, archive, or version behavior.

## Information architecture

System level:

- Project library
- New novel creation
- AI assistant
- Models and AI
- Application and data
- External agents and run logs

Project level:

- Writing
- Outline
- Story library: characters, worldbuilding, relationship graph
- Continuity and archive: narrative governance, context governance, cataloging,
  statistics
- Tools and publishing: deconstruction, import, export, scheduling, skills,
  prompt contribution

## Interaction rules

- `/` always opens the project library. It never silently opens the most recent
  project.
- Project views are encoded in the `view` query parameter so refresh and browser
  history preserve the author's place.
- The desktop control panel navigates to the project library in the same window.
  Opening an external browser is never an unexpected side effect of a primary
  navigation action.
- Assistant history is closed by default. Runtime and model details are exposed
  through one status control and remain available to screen readers.
- The new-book wizard uses Chinese author-facing stage states. Raw structured
  data is an advanced editing surface, not a prerequisite for normal use.
- Destructive chapter actions live behind a secondary menu. Save state is
  visible next to chapter metadata.

## Release acceptance

- Frontend lint, tests, and production build pass.
- Backend tests pass.
- Desktop and narrow layouts keep the primary action visible without overlap.
- Empty, loading, running, failed, and recovery states are distinguishable.
- `Siming.exe`, `update.json`, and `sha256.txt` agree on version 2.8.0 and hash.
