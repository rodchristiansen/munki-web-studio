#!/bin/sh
# Build the Mac worker installer: a pkg with its own Python, mwsctl, and a
# LaunchDaemon that runs `mwsctl worker run` as the unprivileged _mwsworker user.
#
#   packaging/macos/build-worker-pkg.sh            # writes dist/MunkiWebStudioWorker-<version>-<arch>.pkg
#   PREFIX=/tmp/w packaging/macos/build-worker-pkg.sh --tree-only   # just the installed tree, for tests
#
# The pkg is unsigned. Sign it with your own Developer ID Installer identity:
#   productsign --sign "Developer ID Installer: …" in.pkg out.pkg
set -eu
cd "$(dirname "$0")/../.."
repo=$(pwd)
prefix=${PREFIX:-/usr/local/munki-web-studio-worker}
python_version=${PYTHON_VERSION:-3.14}
version=$(uv version --short)
arch=$(uname -m)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# The web assets go in the wheel even though a worker never serves them, so the
# one package is the same everywhere.
if [ ! -f src/munki_web_studio/static/index.html ]; then
  (cd web && pnpm install --frozen-lockfile && pnpm build)
fi
uv build --wheel --out-dir "$work/dist" >/dev/null

if [ "${1:-}" = "--tree-only" ]; then
  root=""
else
  root="$work/root"
fi
tree="$root$prefix"
mkdir -p "$tree"
uv python install "$python_version" --install-dir "$tree/python" >/dev/null
interpreter=$(find "$tree/python" -path '*/bin/python3' -type f -o -path '*/bin/python3' -type l | head -1)
uv venv --relocatable --python "$interpreter" "$tree/venv" >/dev/null
uv pip install --python "$tree/venv/bin/python" "$work"/dist/*.whl >/dev/null
# The venv records where its interpreter lives; point that at the installed
# location rather than the build folder.
built_home=$(dirname "$interpreter")
final_home="$prefix${built_home#"$tree"}"
sed -i '' "s|^home = .*|home = $final_home|" "$tree/venv/pyvenv.cfg"
install -m 0755 packaging/macos/run-worker "$tree/run-worker"
install -m 0644 packaging/macos/worker.conf.example "$tree/worker.conf.example"

if [ -z "$root" ]; then
  echo "$tree"
  exit 0
fi
mkdir -p "$root/Library/LaunchDaemons"
sed "s|@PREFIX@|$prefix|g" packaging/macos/worker.plist \
  > "$root/Library/LaunchDaemons/io.github.rodchristiansen.munki-web-studio.worker.plist"
mkdir -p dist
out="dist/MunkiWebStudioWorker-$version-$arch.pkg"
pkgbuild --quiet --root "$root" --scripts packaging/macos/scripts \
  --identifier io.github.rodchristiansen.munki-web-studio.worker \
  --version "$version" --install-location / "$out"
echo "$repo/$out"
