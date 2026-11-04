# MCP server

Status: decided 2026-11-04 to build in; tool list is a proposal. This page is kept
identical in Munki Web Studio and Cimian Web Studio.

## What it is

Each studio includes an MCP server built with FastMCP, so AI agents and
assistants can work with a repository through the same rules as the browser. An
admin can ask an assistant "which manifests install ExampleEditor?", or "draft a
change moving ExampleEditor 2.10 into production", and review the result in the
studio's Changes view.

The MCP server is not a second backend. It is another front door to the same
domain services the FastAPI routes use, so validation, revision checks, roles,
and audit apply exactly as they do in the browser.

## Where it runs

- Mounted in the studio's FastAPI app at `/mcp`, over streamable HTTP, in the
  same process and release as the web app.
- A local stdio mode for running against a disposable fixture repository during
  development.
- Inside FleetMate, each studio's server is mounted under a prefix (`munki_`,
  `cimian_`) behind one combined `/mcp` endpoint. See `hosting.md`.

## Rules

1. **Agents draft; people publish.** Write tools create or extend a draft change
   set. No MCP tool saves to the repository, runs `makecatalogs`, commits, or
   publishes. Those stay explicit human actions in the studio.
2. **Same identity, same roles.** MCP clients sign in through the studio's OIDC
   provider (OAuth for remote clients). A Viewer's token gets read tools only; an
   Editor's token can also draft. There is no separate MCP key.
3. **Same fidelity.** Draft edits go through the adapter's surgical editor, so an
   agent's change produces the same minimal diff as a form edit.
4. **Attributed.** Every draft and tool call is audited with the user and the MCP
   client name.
5. **Bounded.** Results are paginated and size-limited. Scripts, icon data, and
   payload hashes are returned only when asked for by name.
6. **No execution.** No tool runs installer payloads, scripts, or shell commands.

## Proposed tools

| Tool                  | Kind  | Purpose                                                            |
| --------------------- | ----- | ------------------------------------------------------------------ |
| `search_packages`     | Read  | Find pkginfo by name, version, category, catalog                   |
| `get_pkginfo`         | Read  | One pkginfo as typed fields, with its path and revision            |
| `get_manifest`        | Read  | One manifest with includes, items, and conditional items           |
| `where_used`          | Read  | Manifests and dependencies that reference a package                |
| `explain_conditions`  | Read  | A manifest's conditions as readable rules                          |
| `validate_repository` | Read  | Parse errors, missing references, include cycles, missing payloads |
| `list_changes`        | Read  | Open draft change sets and their diffs                             |
| `draft_pkginfo_edit`  | Draft | Propose field changes to one pkginfo                               |
| `draft_manifest_edit` | Draft | Propose adding, removing, or moving manifest items                 |
| `draft_catalog_move`  | Draft | Propose catalog membership changes for packages                    |

Resources expose the repository layout and each studio's field schema, so an
agent knows which fields exist before it proposes an edit.

## Not yet decided

- Whether job tools (start `makecatalogs`, read job status) become available to
  Publisher tokens later, behind a confirmation step.
- The FastMCP version, pinned when the backend is scaffolded.
