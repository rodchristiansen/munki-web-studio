# Installing Munki Web Studio on one host

This sets up the studio with Docker Compose on a single Linux or macOS host,
reading a Munki repository that is already there, plus an optional Mac worker
for the jobs that need Munki's own tools. Nothing here needs a cloud account.

## The studio

You need Docker with the Compose plugin, and a Munki repository folder on the
host.

Make a folder for the deployment, and copy in the Compose file and the example
config from this repository's `deploy/` folder:

```sh
mkdir munki-web-studio && cd munki-web-studio
```

```sh
curl -fsSLO https://raw.githubusercontent.com/rodchristiansen/munki-web-studio/main/deploy/compose.yaml
```

```sh
curl -fsSLO https://raw.githubusercontent.com/rodchristiansen/munki-web-studio/main/deploy/studio.toml
```

Create `.env` with the image and the repository folder. Use the image digest
listed in the release you are installing, never a moving tag:

```sh
printf 'STUDIO_IMAGE=ghcr.io/rodchristiansen/munki-web-studio@sha256:<digest>\nMUNKI_REPO=/srv/munki-repo\n' > .env
```

Start it, and wait until Compose reports the container healthy:

```sh
docker compose up -d --wait
```

The studio now answers on <http://127.0.0.1:3186>, on this host only. What the
deployment does:

- The repository is mounted read-only at `/srv/munki`, and `studio.toml` says
  `writable = false`. To edit, point `MUNKI_REPO` at an authoring copy and
  change both: `:rw` on the mount, and `writable = true` in the config.
- Drafts, jobs, sessions, uploads, the audit log and the search index live
  on the `studio-state` volume. Back it up. It holds no repository content.
- The container runs as UID 10001 with a read-only root filesystem, all
  capabilities dropped, and no privilege escalation.

To serve other machines, set up sign-in (`[auth]`) in `studio.toml`, add the
host name to `allowed_hosts`, remove `MUNKI_WEB_STUDIO_ALLOW_UNAUTHENTICATED`
from `compose.yaml`, and put a TLS reverse proxy in front. Without sign-in the
studio refuses to start on a non-loopback address unless that variable is set,
so `docker run -p 3186:3186` with the image alone does not expose an open
studio.

To upgrade, change `STUDIO_IMAGE` to the new release's digest and run
`docker compose up -d --wait` again.

## Releases

Each release on GitHub lists the image digest to put in `STUDIO_IMAGE`, and
carries the Python package and the unsigned Mac worker installer. A release
is made by pushing a tag `v<version>` that matches `pyproject.toml`; the
Release workflow refuses a tag that doesn't match. Running the workflow by
hand builds and checks everything without publishing.

## The Mac worker (optional)

Catalog generation and package import run on a Mac with the Munki tools in
`/usr/local/munki`. Build the installer on a Mac with `uv` and the Xcode
command-line tools:

```sh
packaging/macos/build-worker-pkg.sh
```

The pkg is unsigned. Sign it with your own Developer ID Installer identity
before giving it to another Mac:

```sh
productsign --sign "Developer ID Installer: Your Name (TEAMID)" dist/MunkiWebStudioWorker-0.1.0-arm64.pkg MunkiWebStudioWorker.pkg
```

It installs `/usr/local/munki-web-studio-worker`, with its own Python, and a
LaunchDaemon that runs as the hidden `_mwsworker` user, never as root. Install
it:

```sh
sudo installer -pkg MunkiWebStudioWorker.pkg -target /
```

Make a token for the worker on any machine with `mwsctl`, and add the
`[[workers]]` lines it prints to `studio.toml`:

```sh
mwsctl worker token --id mac-1
```

Then, on the Mac, write the token to the worker's token file:

```sh
sudo sh -c 'cat > /usr/local/munki-web-studio-worker/worker.token'
```

Copy the example config and fill in the studio's address and the worker id:

```sh
sudo cp /usr/local/munki-web-studio-worker/worker.conf.example /usr/local/munki-web-studio-worker/worker.conf
```

Start it:

```sh
sudo launchctl bootstrap system /Library/LaunchDaemons/io.github.rodchristiansen.munki-web-studio.worker.plist
```

Settings in the studio shows the worker as connected, with its Munki tool
versions. Its log is `/Library/Logs/munki-web-studio-worker.log`.
