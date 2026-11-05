# Munki Web Studio

A ground-up web workspace for Munki repositories, inspired by
[MunkiStudio](https://github.com/rodchristiansen/munkistudio).

Browse packages, edit manifests and metadata, understand dependencies, review
changes, and publish through an explicit workflow. Existing repository files
remain authoritative. Contributions to upstream MWA2 continue separately.

Munki Web Studio runs on its own; it needs nothing from Cimian or any other
project. Admins who manage both Munki and Cimian can run it alongside
[Cimian Web Studio](https://github.com/windowsadmins/cimian-web-studio) in
[FleetMate Web Admin](https://github.com/rodchristiansen/fleetmate-web-admin),
which hosts both studios in one app. See [standalone and hosted](docs/hosting.md).

**Status: planning and design exploration. There is no application backend yet.**

## Start here

- [Build plan](docs/build-plan.md)
- [Product plan and milestones](docs/plan.md)
- [Architecture and compatibility boundaries](docs/architecture.md)
- [Infrastructure options and deployment requirements](docs/infrastructure.md)
- [Design direction and interaction specification](docs/design.md)
- [Backend language and why](docs/backend.md)
- [Standalone and hosted](docs/hosting.md)
- [MCP server for AI agents](docs/mcp.md)
- [Command line](docs/cli.md)
- [Reference research](docs/research.md)
- [Interactive workspace study](docs/design/workspace.html)

The workspace study uses invented sample data and runs locally without a build
step. Open the HTML file with its adjacent CSS and JavaScript files, or serve the
repository locally.
GitHub's file viewer displays its source.

To serve the design study locally, run this from the repository root:

```sh
python3 -m http.server 3186 --bind 127.0.0.1
```

Then open <http://127.0.0.1:3186/docs/design/workspace.html>.

The proposed starting point is a self-hosted web app with a repository service
and an optional macOS worker. Deployment, framework, identity provider, and
licensing choices remain open. No cloud resources have been provisioned.

## Command line

`mwsctl` works on a repository folder directly, with no server. Find packages
and manifests:

```sh
mwsctl search editor --repo /path/to/repo
```

Print one file, or list what refers to a package:

```sh
mwsctl show pkgsinfo/apps/Editor-2.10.plist --repo /path/to/repo
```

```sh
mwsctl where-used Editor --repo /path/to/repo
```

Check the whole repository. The command exits 1 when it finds errors, so a
repository's CI can run it on every pull request; `--strict` fails on warnings
too, and `--json` prints a machine-readable report:

```sh
mwsctl validate --repo /path/to/repo
```

## MCP server

The studio includes an MCP server with read-only tools: search_packages,
get_pkginfo, get_manifest, where_used, explain_conditions and
validate_repository, plus a studio://layout resource. `mwsctl serve` exposes it
at `/mcp`. To give an assistant on this machine direct access to a repository
folder over stdio, run:

```sh
mwsctl mcp --repo /path/to/repo
```

## Development

The backend is a Python 3.14 package managed with [uv](https://docs.astral.sh/uv/).
Install the dependencies from the lockfile:

```sh
uv sync
```

Run the linters, type checker, and tests:

```sh
uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest
```

Start the web app, API, and MCP server on <http://127.0.0.1:3186>:

```sh
uv run mwsctl serve
```

The health check is at `/api/health` and the MCP server at `/mcp`.

The browser workspace lives in `web/` (React, TypeScript, Vite, pnpm). Build it
into the Python package so `mwsctl serve` serves it at `/`:

```sh
cd web && pnpm install && pnpm build
```

Run its type check, component tests, and the browser test against the real
server:

```sh
pnpm typecheck && pnpm test && pnpm e2e
```

The browser talks to the API through a client generated from `web/openapi.json`.
After changing an API route or model, regenerate both from the repository root:

```sh
uv run mwsctl openapi --output web/openapi.json && (cd web && pnpm gen:api)
```

CI fails when either file is out of date.

For live reloading, run `uv run mwsctl serve` and `pnpm dev` side by side; the
Vite dev server forwards `/api` and `/mcp` to the API.
