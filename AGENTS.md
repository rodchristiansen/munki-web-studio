# Munki Web Studio

This is a new project for a ground-up web application inspired by MunkiStudio.
Maintenance contributions to upstream MWA2 are a separate effort.

## Project state

Planning only. Read `docs/plan.md`, `docs/architecture.md`,
`docs/infrastructure.md`, and `docs/design.md` before implementation.
Recommendations in those documents remain proposals until explicitly accepted.
Do not describe the design prototype as a functioning repository editor.

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

## Shared core

Munki Web Studio is built on `rodchristiansen/fleetmate-web-admin`, which owns
the workspace UI, change sets, jobs, identity, and audit. This repository owns
the Munki adapter (plist and YAML layout, schema, references, `NSPredicate`
conditions) and the Mac worker. Shared behavior changes go into the core, never
a local copy. Cimian Web Studio (`windowsadmins/cimian-web-studio`) is the
sibling studio on the same core.

## Infrastructure

All deployable configuration belongs in versioned IaC and the reviewed deployment
path. Planning does not authorize cloud provisioning or identity changes.
Keep application state, repository content, credentials, and package payloads
out of this source repository. Select a license before public distribution.
