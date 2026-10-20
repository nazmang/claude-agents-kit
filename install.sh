#!/bin/bash
# Links the tools into place. Does not edit settings.json - prints what to add.
set -euo pipefail
REPO=$(cd "$(dirname "$0")" && pwd)
BIN=${BIN:-$HOME/.local/bin}

CUR=${DEST:-$HOME/.local/share/claude-agents-kit}/current
"$REPO/release.sh"
mkdir -p "$BIN"
ln -sfn "$CUR/bin/agents" "$BIN/agents"
echo "linked: $BIN/agents"

cat <<MSG

Add the mods to ~/.claude/settings.json (append to an existing list with ':'):

  "env": { "CLAUDE_CODE_PLUGIN_DIRS": "$CUR/plugins/push-guard:$CUR/plugins/agents-panel" }

Already running sessions pick the mods up only after a restart.
MSG
