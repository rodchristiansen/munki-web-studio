# Munki Web Studio

This is a new project for a ground-up web application inspired by MunkiStudio.
Maintenance contributions to upstream MWA2 are a separate effort.

## Project state

Planning is done; the build follows `docs/build-plan.md` and the `slice` issues.
Before building, read the issue and the docs it touches: `docs/plan.md`,
`docs/architecture.md`, `docs/infrastructure.md`, `docs/design.md`, and the
decided pages (`backend.md`, `hosting.md`, `mcp.md`, `cli.md`). Do not describe
the design prototype as a functioning repository editor.

## Worktrees and delivery

Create task worktrees inside `./.worktrees/` with the global `git wt` alias,
starting from `origin/main`. Keep `/.worktrees/` ignored. Use task branches
and reviewed pull requests. Never add assistant attribution or bracketed tags
to commit subjects. Track project work in this repository's GitHub issues.

## Repository integrity

Use synthetic fixtures. Never attach a live Munki repository during development
without explicit authorization. Preserve unknown metadata, value types, file
formats, relative paths, and meaningful ordering. Do not silently convert
formats or make Git mandatory. Catalogs are derived outputs.

## Standalone, and hostable

Munki Web Studio is a complete product with no dependency on any other
repository. It must run on its own for an admin who has never heard of Cimian.
Never import from `rodchristiansen/fleetmate-web-admin` or
`windowsadmins/cimian-web-studio`.

FleetMate Web Admin hosts this studio alongside Cimian Web Studio for admins
who run both. It depends on this repository, never the reverse. Keep the hosting
surface in `docs/hosting.md` working, and keep `docs/hosting.md` and
`docs/backend.md` identical to their copies in Cimian Web Studio.

Workspace, change-set, job, Git and audit code is copied between the two studios,
not shared. When you fix it here, open the matching pull request in Cimian Web
Studio.

## Infrastructure

All deployable configuration belongs in versioned IaC and the reviewed deployment
path. Planning does not authorize cloud provisioning or identity changes.
Keep application state, repository content, credentials, and package payloads
out of this source repository. Licensed MIT (`LICENSE`).
