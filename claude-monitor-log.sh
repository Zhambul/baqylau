#!/usr/bin/env bash
# claude-monitor-log.sh — PostToolUse hook for the Monitor tool.
#
# Writes a monitor header to the mirror log and spawns a detached tailer for the
# monitor's event stream (see claude-monitor-fmt.py + claude-stream.py). Thin
# wrapper: forward the payload to the formatter (width is handled at paint time
# by claude-mirror.py, so it isn't needed here).

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fmt="$here/claude-monitor-fmt.py"
[ -f "$fmt" ] || exit 0

# The formatter derives the per-session log path from the payload's session_id.
python3 "$fmt" 2>/dev/null
exit 0
