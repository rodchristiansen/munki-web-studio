# Build plan

Status: active · 2026-11-04. This page is kept identical in Munki Web Studio and
Cimian Web Studio.

The build is a numbered list of slices, `S01` to `S32`, tracked as GitHub issues
with the `slice` label and grouped into milestones `M0` to `M7`. Each slice is
one vertical piece that ends working, tested, merged, and closed.

## Decided stack

| Layer        | Choice                                     |
| ------------ | ------------------------------------------ |
| Backend      | Python 3.14, FastAPI, managed with uv      |
| AI interface | FastMCP, mounted at `/mcp`                 |
| Command line | Typer: `mwsctl` (Munki), `cwsctl` (Cimian) |
| Browser      | React, TypeScript, Vite, pnpm              |
| App state    | SQLite on local durable disk               |
| Quality      | ruff, pyright, pytest, Vitest, Playwright  |
| License      | MIT                                        |

## Milestones

| Milestone             | Slices     | Ends with                                       |
| --------------------- | ---------- | ----------------------------------------------- |
| M0 Foundations        | S01 to S03 | Package, web app, CI, and fixtures run and test |
| M1 Codec              | S04 to S05 | Faithful reads, surgical edits, tagged JSON     |
| M2 Read-only explorer | S06 to S14 | Browse, search, validate by web, CLI, and MCP   |
| M3 Identity           | S15 to S17 | Sign-in, roles, and audit for every interface   |
| M4 Safe editing       | S18 to S23 | Drafts, review, and conflict-safe saves         |
| M5 Jobs and workers   | S24 to S28 | Jobs, worker, catalogs, Git, publication        |
| M6 Imports            | S29        | Uploads imported through the worker             |
| M7 Release            | S30 to S32 | Container, hosting surface, releases            |

## Shared and platform slices

A `shared` slice produces the same code in both studios. It is built in Munki
Web Studio first and ported to Cimian Web Studio in a matching pull request,
keeping the same folder layout and names so the copies can be compared. A
`platform` slice is written separately for each studio. A `needs-hardware` slice
needs a host with the platform's real tools.

## Definition of done for a slice

- Code, tests, and docs on a task branch from `origin/main`, in a worktree.
- Lint, type check, and tests pass locally and in CI.
- Pull request written in the maintainer's voice, merged, branch deleted.
- The issue closed by the pull request, with its acceptance boxes checked.
- For a `shared` slice, the port to the other studio merged too.

## Guardrails

- Synthetic fixtures only. Never attach a live repository.
- Never provision cloud resources, change identity configuration, or publish
  packages to a registry as part of a slice. Those wait for an explicit go-ahead.
- Never import from FleetMate Web Admin or from the other studio.
