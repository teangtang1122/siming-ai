# ADR 006: Frontend State and Generated Contracts

## Status

Accepted for `3.0.0-beta.2`.

## Context

The frontend previously mixed three ownership models for the same data:
Zustand async actions, page-local effects, and direct API polling. Project
lists could therefore disagree with the active workspace, and the global task
center maintained a second operation array beside its SSE stream. Most success
responses were also emitted as `unknown` in OpenAPI, forcing the GUI to repeat
backend interfaces by hand.

## Decision

- `src/app` owns application composition and providers.
- `src/shared` owns framework-neutral API, query, and reusable UI building
  blocks. It cannot depend on features or pages.
- `src/features` owns business-facing API functions, query keys, mutations,
  cache projections, and feature components. It cannot depend on pages.
- TanStack Query owns reusable server state. The first migrated slices are
  projects, first-run model readiness, and operations.
- Zustand stores only cross-page client state such as the persistent global
  error banner. It no longer fetches or mirrors project rows.
- SSE events update the same operation query cache used by polling. A broken
  event stream does not create a separate source of truth.
- FastAPI routes expose typed response models for the migrated slices.
  `openapi-typescript` generates their TypeScript contracts, and CI rejects
  generated-file drift.
- Generated request models may be wrapped by small feature-level draft types
  when a form intentionally omits backend defaults. The API adapter fills those
  defaults before sending the generated request contract.

## Consequences

Project mutations now invalidate a stable query-key family, content-root
migration refreshes that same family, and a directly opened project workspace
loads only its own detail. First-run gates share cached readiness while keeping
one-time login credentials local. Operation lifecycle and health are rendered
through one status component and retain the existing SSE reconnection behavior.

Legacy pages can migrate incrementally. Existing REST paths and payloads remain
compatible, but new shared server state must enter through a feature query
rather than a Zustand async action or an uncoordinated page effect.
