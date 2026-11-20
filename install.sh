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
ln -sfn "$CUR/bin/agents-inbox" "$BIN/agents-inbox"
echo "linked: $BIN/agents-inbox"

cat <<MSG

Add the mods to ~/.claude/settings.json (append to an existing list with ':'):

  "env": { "CLAUDE_CODE_PLUGIN_DIRS": "$CUR/plugins/push-guard:$CUR/plugins/agents-panel" }

Already running sessions pick the mods up only after a restart.

Inbox (optional): put the config in ~/.config/agents/inbox.conf
(see $REPO/examples/inbox.conf), then install the unit:

  mkdir -p ~/.config/systemd/user
  cp $REPO/examples/agents-inbox.service ~/.config/systemd/user/
  systemctl --user daemon-reload
  systemctl --user enable --now agents-inbox
MSG
