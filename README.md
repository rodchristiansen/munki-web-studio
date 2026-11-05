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

## Saving

A repository is read-only in the studio unless its entry in the config file
says `writable = true`. Editors then save their drafts into it; every file in a
draft is written or none is, and a draft whose files changed since it was made
(by another person, AutoPkg, or `munkiimport`) is refused instead of
overwriting that change. If clients read the folder directly, mark it
`live = true`: the studio then asks for an explicit confirmation before each
save, because the change reaches clients at once.

## Git

When a repository is a Git working tree, the top bar shows its branch, and Settings
shows the commit, the upstream and what is uncommitted. After a save, the studio
offers to commit the files that save wrote, and only those, even when other
files are staged or modified. It refuses if one of them has changed again since
the save. Pushing is a separate action for Publishers, to the branch's upstream,
over https or ssh only.

Git never runs anything the repository names. Every command ignores the system
and global Git config and points hooks at an empty folder. It also turns off the
fsmonitor, pager, editor, credential helpers, external diffs and signing. A
repository whose own config sets a filter, a diff textconv, an ssh command, a
credential helper or an include is left alone, and Settings names the setting
that stopped it. A repository that is not a Git working tree works exactly as
before.

## Jobs

Work that takes a while runs as a job: validating the repository, re-reading
every file, and later catalog generation and imports. Start one from Activity.
Jobs are kept in the studio's state folder, so closing the browser or
restarting the studio does not lose one; a job queued before a restart runs
after it. Each job is pinned to the repository revision it started from, tries
again a bounded number of times if its worker stops responding, and records
its result exactly once. Activity follows a running job live and falls back to
polling when it cannot hold a stream open.

Jobs that need tools the studio does not have, such as Munki's own command-line
tools, are refused with the capability they need instead of waiting in a queue.

### Workers

Jobs that need Munki's own tools run on a worker: `mwsctl worker run` on a Mac
with the Munki tools installed. The worker connects out to the studio, reports
its macOS version and the version of each tool it finds, and runs only
`makecatalogs` and `makepkginfo` from `/usr/local/munki`, with fixed arguments,
in a fresh staging folder per job. It sends back the output files with their
SHA-256 hashes, and the studio checks every hash before it records a result.
Settings shows each worker, its tools and what it can run; when no worker can
run a job, Activity says which tool is needed.

### Catalogs

Catalogs shows what each catalog holds and where it has fallen behind the
pkginfo: items a pkginfo lists for a catalog that the catalog is missing, and
items still in a catalog that no pkginfo puts there. Generate catalogs runs
`makecatalogs --skip-pkg-check` on the worker against the repository's pkginfo
at the revision it was started on. The studio does not trust the
worker with what clients install. It compares every catalog entry with its own
reading of the pkginfo, field for field, allowing only the keys `makecatalogs`
drops (`notes`, `_metadata`) or adds (`loop_fingerprint`). Values must match in type too,
so `true` never passes for `1`. Each catalog must hold exactly the pkginfo that
list it, no other catalog may appear, and every file must be an XML plist in
exactly the form `makecatalogs` writes. The check and the swap use the same
private copy of the files, so nothing uploaded afterwards is applied. Then it
swaps the catalogs into `catalogs/` in one step, removing stale
catalogs as `makecatalogs` does and keeping hidden files such as `.gitkeep`. If
the tool fails, the output fails the check, the job is cancelled, or the
repository changes while the job runs, the existing catalogs stay as they were. The result matches running
`makecatalogs` directly on the same pkginfo. One difference: the worker does not
receive `icons/`, so it does not write `icons/_icon_hashes.plist`. Generating
needs a writable repository, and a live repository asks for confirmation first.

Make a token for the worker. The command prints the token, and the lines to add
to the studio's config file:

```sh
mwsctl worker token --id mac-1
```

Save the token on the Mac in a file only you can read, then start the worker:

```sh
mwsctl worker run --url https://studio.example.org --id mac-1 --token-file ~/.config/mwsctl/mac-1.token
```

## Sign-in

Without an `[auth]` table the studio is single-user and answers only on this
machine (localhost). To serve it to a team, add OpenID Connect sign-in. Any
provider works, including Entra ID; the client secret is read from the
environment variable you name, never from the file:

```toml
allowed_hosts = ["studio.example.org"]

[auth]
mode = "oidc"
issuer = "https://login.microsoftonline.com/<tenant-id>/v2.0"
client_id = "<application-id>"
client_secret_env = "STUDIO_OIDC_SECRET"
public_url = "https://studio.example.org"
role_claim = "roles"

[auth.roles]
Viewer = ["Studio.Viewer"]
Editor = ["Studio.Editor"]
Publisher = ["Studio.Publisher"]
Administrator = ["Studio.Administrator"]
```

Register `https://studio.example.org/auth/callback` as the redirect URI. Each
person gets the highest role their claim values map to; people with none are
refused unless you set `default_role`. Sessions live on the server, and every
change must carry the session's CSRF token.

The command line signs in with the provider's device flow. Register a public
client for it (`cli_client_id` in `[auth]`) with device sign-in enabled, then:

```sh
mwsctl login --url https://studio.example.org
```

After that, `search`, `where-used` and `validate` take `--url` (or the
`MWSCTL_URL` variable) to ask the studio instead of reading a folder. The token
is stored in `~/.config/mwsctl/credentials.json`, readable only by you;
`mwsctl logout` revokes it.

MCP clients send the same token as a bearer token to `/mcp`. Print it for a
client's configuration with:

```sh
mwsctl token --url https://studio.example.org
```

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

### Drafts

The `drafts` commands work on a running studio, with the same drafts the browser
shows, so a change can be started in one and finished in the other. Start a
draft; the command prints its id:

```sh
mwsctl drafts create "Move Editor to production" --url https://studio.example.org
```

Add pkginfo field edits to it. Lists are comma-separated and booleans take true
or false; `mwsctl drafts fields` lists the fields:

```sh
mwsctl drafts edit <id> pkgsinfo/apps/Editor-2.10.plist --set catalogs=testing,production --url https://studio.example.org
```

See the exact diff, then save. Saving shows the diff again and asks first:

```sh
mwsctl drafts diff <id> --url https://studio.example.org
```

```sh
mwsctl drafts save <id> --url https://studio.example.org
```

`drafts list`, `drafts show` and `drafts discard` cover the rest. Set
`MWSCTL_URL` to leave out `--url`.

## MCP server

The studio includes an MCP server. Read tools: search_packages, get_pkginfo,
get_manifest, where_used, explain_conditions, validate_repository and
list_changes. Draft tools, for Editors: draft_pkginfo_edit, draft_manifest_edit
and draft_catalog_move. Draft tools add to the caller's open draft, which then
appears in the studio for review; no tool saves, so an assistant's change reaches
the repository only when a person saves it. Resources: studio://layout and
studio://fields.

`mwsctl serve` exposes it at `/mcp`. To give an assistant on this machine
direct access over stdio, run it with the studio's config file, so its drafts
land where the studio shows them:

```sh
mwsctl mcp --config /path/to/studio.toml
```

`mwsctl mcp --repo /path/to/repo` serves a folder on its own; its drafts last
only as long as that server.

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
