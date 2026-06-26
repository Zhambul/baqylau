#!/usr/bin/env bash
# claude-monitor-log.sh — PostToolUse hook for the Monitor tool.
#
# Writes a monitor header to the mirror log and spawns a detached tailer for the
# monitor's event stream (see claude-monitor-fmt.py + claude-stream.py). Thin
# wrapper: find the pane width, forward the payload to the formatter.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fmt="$here/claude-monitor-fmt.py"
[ -f "$fmt" ] || exit 0

slug="$(pwd -P 2>/dev/null | sed 's#[/.]#-#g')"
[ -n "$slug" ] || exit 0

width="$("$here/claude-pane-width.sh" 2>/dev/null)"
[ -n "$width" ] || width=53

python3 "$fmt" "/tmp/claude-mirror-$slug.log" "$width" 2>/dev/null
exit 0
