# Munki Web Studio

A ground-up web workspace for Munki repositories, inspired by
[MunkiStudio](https://github.com/rodchristiansen/munkistudio).

Browse packages, edit manifests and metadata, understand dependencies, review
changes, and publish through an explicit workflow. Existing repository files
remain authoritative. Contributions to upstream MWA2 continue separately.

**Status: planning and design exploration. There is no application backend yet.**

## Start here

- [Product plan and milestones](docs/plan.md)
- [Architecture and compatibility boundaries](docs/architecture.md)
- [Infrastructure options and deployment requirements](docs/infrastructure.md)
- [Design direction and interaction specification](docs/design.md)
- [Reference research](docs/research.md)
- [Interactive workspace study](docs/design/workspace.html)

The workspace study uses invented sample data and runs locally without a build
step. Download/open the HTML file in a browser, or serve the repository locally.
GitHub's file viewer displays its source.

To serve the design study locally, run this from the repository root:

```sh
python3 -m http.server 3186 --bind 127.0.0.1
```

Then open <http://127.0.0.1:3186/docs/design/workspace.html>.

The proposed starting point is a self-hosted web app with a repository service
and an optional macOS worker. Deployment, framework, identity provider, and
licensing choices remain open. No cloud resources have been provisioned.
