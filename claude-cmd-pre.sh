#!/usr/bin/env bash
# claude-cmd-pre.sh — PreToolUse(Bash) hook.
#
# Thin wrapper around claude-cmd-pre.py, which rewrites long-running foreground
# commands to stream live in the mirror pane (see that file for the mechanism).
# Kept separate from the python so its logic can use both quote chars freely.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pre="$here/claude-cmd-pre.py"
[ -f "$pre" ] || exit 0

python3 "$pre" 2>/dev/null
exit 0
