#!/bin/sh
# Build the Mac worker installer on this Mac and add it to a published release.
# Usage: scripts/attach-worker-pkg.sh v0.2.0
set -eu
tag=$1
cd "$(dirname "$0")/.."
version=$(uv version --short)
[ "$tag" = "v$version" ] || { echo "Check out $tag first: this tree is version $version" >&2; exit 1; }
[ "$(git describe --tags --exact-match 2>/dev/null)" = "$tag" ] || { echo "HEAD is not $tag" >&2; exit 1; }
pkg=$(packaging/macos/build-worker-pkg.sh | tail -1)
gh release upload "$tag" "$pkg" --clobber
echo "Attached $(basename "$pkg") to $tag"
