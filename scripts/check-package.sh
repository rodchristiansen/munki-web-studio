#!/bin/sh
# Install a built wheel in a fresh environment and check that mwsctl reports
# the expected version. Usage: scripts/check-package.sh <wheel> <version>
set -eu
wheel=$1
expected=$2
venv=$(mktemp -d)
trap 'rm -rf "$venv"' EXIT
uv venv --quiet --python 3.14 "$venv"
uv pip install --quiet --python "$venv/bin/python" "$wheel"
reported=$("$venv/bin/mwsctl" --version)
if [ "$reported" != "mwsctl $expected" ]; then
  echo "mwsctl reports '$reported', expected 'mwsctl $expected'" >&2
  exit 1
fi
echo "$reported"
