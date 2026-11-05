#!/bin/sh
# Start the Compose deployment against the test fixture and check it serves.
# Usage: deploy/smoke.sh <image>
set -eu
image=$1
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'docker compose -p studio-smoke -f "$here/compose.yaml" down -v >/dev/null 2>&1 || true; rm -rf "$work"' EXIT
cp -R "$here/../tests/fixtures/repo" "$work/repo"
cp "$here/studio.toml" "$here/compose.yaml" "$work/"
cd "$work"
STUDIO_IMAGE=$image MUNKI_REPO=$work/repo docker compose -p studio-smoke up -d --wait --wait-timeout 90
curl -fsS http://127.0.0.1:3186/api/health
echo
curl -fsS http://127.0.0.1:3186/api/repositories | grep -q '"id":"main"'
curl -fsS http://127.0.0.1:3186/ | grep -q '<div id="root">'
user=$(docker compose -p studio-smoke exec -T studio id -u)
[ "$user" = 10001 ] || { echo "running as uid $user, not 10001" >&2; exit 1; }
if docker compose -p studio-smoke exec -T studio sh -c 'touch /srv/munki/x' 2>/dev/null; then
  echo "the repository mount is writable" >&2
  exit 1
fi
echo "smoke test passed"
