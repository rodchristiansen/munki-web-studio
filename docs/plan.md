# Product plan

Status: proposal · 2026-10-15

## Direction

Build Munki Web Studio as an independent, ground-up web application. MunkiStudio
is the workflow and interaction reference. MWA2 establishes the minimum useful
job: editing pkginfo and manifests in an existing Munki repository. This project
does not replace the separate effort to contribute focused maintenance PRs to
upstream MWA2, and does not imply upstream endorsement.

The first audience is an administrator or small team managing an existing repo.
The long-term scope can grow to imports, package builds, promotion, profiles,
dependency analysis, and GitOps. A useful first release does not require all of
those capabilities or a hosted service.

## Product principles

1. Repository files remain authoritative; an index is replaceable.
2. Opening a repo does not convert it or require moving it into a database.
3. Structured editing and source inspection are complementary.
4. Save, catalog generation, Git commit, and publication are distinct operations.
5. Capabilities are honest: unsupported host tools show a reason and a next step.
6. Changes are attributable and conflicts are visible before overwriting data.
7. Browser support includes current Safari, Edge/Chrome, and Firefox at release.

## Initial scope

| Capability | First useful release | Later |
| --- | --- | --- |
| Repository connection | One administrator-configured filesystem repo, read-only first | Multiple repos; hosted connector; object storage |
| Packages | Search/filter, versions, metadata editor, source and diff | Bulk operations, rich installer analysis |
| Manifests | Catalog order, included manifests, managed item lists, conditions | Effective targeting preview |
| Catalogs | Membership view; explicit generation job on a supported worker | Promotion rules and scheduling |
| Validation | Parse/type errors, missing references, dependency/include cycles | Client-equivalent condition evaluation |
| Changes | Draft isolation, preview, safe save, conflict detection, audit | Git commit/push and pull-request integration |
| Authentication | Authenticated team access before networked writable use | Broader identity integrations and scoped roles |
| Imports/builds | Capability boundary and job contract | Mac-backed package import, extraction, builds |
| Profiles/cleanup | Preserve existing fields during edits | Dedicated profile editor, previewable cleanup |

Git is optional. Existing AutoPkg and command-line workflows must continue to
work. YAML support is conditional on tested Munki/tool versions; plist support
is required. File format conversion is always an explicit action.

## Milestones and acceptance

### Discovery and decisions

- Review reference code and screens; record provenance without copying real data.
- Choose initial deployment and backend after a filesystem and tool-capability spike.
- Review the design study for Packages, Manifests, and Changes.
- Decide license, supported Munki versions, and identity boundary.

Done when those choices are recorded and the first implementation issue is bounded.

### Read-only repository explorer

- Synthetic repo fixtures for XML/binary plist, nested manifests, unknown keys,
  dates/data, scripts, missing references, and repeated package names/versions.
- Read/search/list/detail API and browser UI with stable relative-path identities.
- Invalid files remain visible with diagnostics; no writes on scan or open.
- Explicit reindex and freshness display when another tool changes files.

Done when scans leave every fixture byte unchanged, deep links work, and large
fixture repos remain usable. Proposed benchmark: 10,000 pkginfo files and 2,000
manifests; measure cold scan separately from warm search. Targets are provisional:
warm search below 300 ms server time and selection-to-editor below 200 ms on a
documented reference machine.

### Safe structured editing

- Small pkginfo field set and manifest item editor with source/diff preview.
- Unknown keys and value types survive; no-op saves do not rewrite files.
- A stale revision returns a conflict with reload/reapply choices.
- Auth, request forgery protection, authorization, path containment, durable audit,
  and crash recovery are prerequisites to networked writes.

Done when two editors plus an external CLI writer cannot silently lose changes,
and a failed batch can be recovered from its journal. State explicitly which
external-writer coordination mechanism is required.

### Catalog and Git workflow

- Worker capability handshake with pinned tool version and supported formats.
- Catalog generation runs against a reviewed snapshot, validates outputs, then
  publishes according to the configured deployment model.
- Optional Git reviews include the exact diff and current commit. Commit and
  push are separate; existing hooks are never run implicitly in a web service.

Done when failed jobs preserve the last published revision and retries cannot
duplicate publication. Test on a disposable repo and a real Munki test client.

### Imports and broader workflows

- Resumable large uploads into staging, hash verification, preview, Mac worker,
  and a reviewed import result.
- Expand profiles, build, promoter, and cleanup only as separate vertical slices.

## Open decisions

| Decision | Proposed default | What could change it |
| --- | --- | --- |
| Initial deployment | Self-hosted, existing filesystem repo | A hosted-first product goal |
| Frontend | React + TypeScript + Vite | Preference for another maintained UI stack |
| Backend | Compare FastAPI with modern Django in a small spike | Identity/admin needs may favor Django |
| Runtime topology | One writer per repo; optional worker process | Proven need for multiple writers/instances |
| State | SQLite on local durable disk for a single-host pilot | Shared control plane needs PostgreSQL |
| Mac jobs | Optional Mac worker; unavailable capabilities explicit | Requirement for imports in the first release |
| Git model | Optional repository integration | Team requires PR-only metadata changes |
| Identity | OIDC, exact provider still open | Existing self-hosted identity requirements |
| License/visibility | Private exploration; license undecided | Decision to publish and accept outside contributions |

## Next implementation slice

Start with the read-only explorer after the infrastructure decision. Avoid
scaffolding every future subsystem. Use small, independently reviewable PRs
with synthetic fixtures and one observable workflow per slice.
