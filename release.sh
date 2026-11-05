#!/bin/bash
# Publishes the committed state of bin/ and plugins/ to a stable
# copy that running sessions load. Sessions hot-reload a mod whenever its files
# change, so they must never point at the working tree you are editing.
#
#   ./release.sh            check, copy HEAD, switch $DEST/current atomically
#   ./release.sh --list     show releases
#   ./release.sh --rollback <sha>
set -euo pipefail
REPO=$(cd "$(dirname "$0")" && pwd)
DEST=${DEST:-$HOME/.local/share/claude-agents-kit}
cd "$REPO"

switch() { ln -sfn "releases/$1" "$DEST/current.new" && mv -T "$DEST/current.new" "$DEST/current"; echo "current -> $1"; }

case "${1:-}" in
  --list) ls -1t "$DEST/releases"; echo "current: $(readlink "$DEST/current")"; exit 0 ;;
  --rollback) [ -d "$DEST/releases/${2:?sha}" ] || { echo "no release $2"; exit 1; }; switch "$2"; exit 0 ;;
esac

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "uncommitted changes: commit first, a release is always a commit"; exit 1
fi
untracked=$(git ls-files --others --exclude-standard)
if [ -n "$untracked" ]; then
  echo "untracked files (commit or .gitignore them; the GitHub copy would miss them):"; echo "$untracked"; exit 1
fi
if ! command -v claude >/dev/null; then
  if [ "${SKIP_PLUGIN_CHECKS:-}" != 1 ]; then
    echo "claude is not on PATH: cannot validate and test the plugins (SKIP_PLUGIN_CHECKS=1 to release anyway)"; exit 1
  fi
  echo "warning: SKIP_PLUGIN_CHECKS=1, plugins are released without validate/test"
else
  for p in plugins/*/; do
    claude plugin validate "$p" >/dev/null 2>&1 || { echo "validate failed: $p"; exit 1; }
    if [ -d "$p/tests" ]; then
      out=$(claude plugin test "$p" 2>&1) || { echo "$out" | tail -20; echo "tests failed: $p"; exit 1; }
    fi
  done
fi
python3 -m py_compile bin/agents

sha=$(git rev-parse --short HEAD)
mkdir -p "$DEST/releases"
if [ -d "$DEST/releases/$sha" ]; then
  # same commit already released: never rewrite a tree live sessions load from
  switch "$sha"; exit 0
fi
rm -rf "$DEST/releases/$sha.tmp" && mkdir -p "$DEST/releases/$sha.tmp"
git archive HEAD bin plugins | tar -x -C "$DEST/releases/$sha.tmp"
rm -rf "$DEST/releases/$sha" && mv "$DEST/releases/$sha.tmp" "$DEST/releases/$sha"
switch "$sha"
# keep the last 5
ls -1t "$DEST/releases" | tail -n +6 | while read -r old; do [ "$old" != "$sha" ] && rm -rf "$DEST/releases/$old"; done
