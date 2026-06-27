#!/usr/bin/env bash
# claude-task-log.sh — TaskCreated / TaskCompleted hook.
#
# Appends a one-line agent-team task event (✚ created / ✓ completed) to the mirror
# log the split pane tails. The event name is in the hook payload, so this wrapper
# just locates the pane width + the cwd-keyed mirror log and hands the payload to
# claude-task-fmt.py. Always exits 0 so it can never block a hook.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fmt="$here/claude-task-fmt.py"
[ -f "$fmt" ] || exit 0

# The formatter derives the per-session log path from the payload's session_id.
python3 "$fmt" 2>/dev/null
exit 0
