# Command line

Status: language and packaging decided 2026-11-04; commands are a proposal. This page is kept identical in Munki Web Studio
and Cimian Web Studio.

## Three front doors, one core

Each studio has three ways in, all over the same domain services:

| Interface    | For                                             | Built with                          |
| ------------ | ----------------------------------------------- | ----------------------------------- |
| Web API      | The browser workspace and integrations          | FastAPI                             |
| MCP server   | AI agents and assistants                        | FastMCP, mounted in the FastAPI app |
| Command line | Admins in a terminal, scripts, and CI pipelines | Python with Typer                   |

Validation, revision checks, roles, and audit live in the domain services, so
they behave identically whichever door is used. A rule added for the browser
applies to the CLI and MCP without extra work.

## Why Python for the CLI

Decided 2026-11-04: Python with Typer.

The CLI ships in the same Python package as the API and MCP server, so it uses
the same adapter and surgical editor directly. A CLI in another language would
need its own copy of the codec, which is the part the codec spike showed is
easiest to get wrong. Typer builds the commands from typed Python functions,
with help text and shell completion.

Install with `uv tool install` or `pipx` on macOS, Windows, and Linux. A
single-file executable for machines without Python is a later option.

## Commands

| Product | Command |
| --- | --- |
| Munki Web Studio | `mwsctl` |
| Cimian Web Studio | `cwsctl` |
| FleetMate Web Admin | `fmwsctl` |

Each studio installs only its own command. `mwsctl` knows nothing about
Cimian, and `cwsctl` nothing about Munki.

## Two modes

- **Remote.** The CLI talks to a running studio's API. Sign-in uses the OIDC
  device code flow, so the same roles apply as in the browser. Drafts created
  here show up in the web Changes view.
- **Local.** The CLI reads a repository on disk with no server, for read-only
  work: search, show, where-used, validate. This is what a repository's CI
  pipeline runs on every pull request.

## What it does, and what it leaves alone

The CLI covers what the web studio adds: search across the repository,
where-used, condition explanations, validation, drafts, review, and saving a
reviewed draft. It does not replace Munki's or Cimian's own tools.
`makepkginfo`, `munkiimport`, `cimiimport`, `manifestutil`, and `makecatalogs`
keep their jobs, and the studio's workers call them.

Saving and publishing stay explicit: they take a named draft, show its diff, and
ask for confirmation unless `--yes` is passed by a user who holds the role.

## Inside FleetMate

FleetMate's `fmwsctl` includes both studios' commands as subcommands
(`fmwsctl munki …`, `fmwsctl cimian …`), taken from the installed studio
packages, plus commands for the combined deployment. Each studio's own command
can also point at a FleetMate deployment with the studio's prefix.

## Subcommands

Local, on a repository folder or a running studio with `--url`: `search`,
`show`, `where-used`, `validate`. Sign-in: `login`, `logout`, `token`. Servers:
`serve`, `mcp`, `openapi`.

Drafts, on a running studio: `drafts list`, `create`, `show`, `diff`, `fields`,
`edit`, `save`, `discard`. `save` prints the diff and asks unless `--yes` is
given, and says so first when the repository is live. `discard` asks too.
