#!/usr/bin/env bash
# claude-file-log.sh — PostToolUse hook for Read/Edit/Write/MultiEdit/NotebookEdit.
#
# Appends a compact one-liner (verb + file basename) to the mirror log the split
# pane tails, e.g. "Read(README.md)" / "Update(README.md)". The formatting lives
# in claude-file-fmt.py; this wrapper just locates it and forwards the payload.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fmt="$here/claude-file-fmt.py"
[ -f "$fmt" ] || exit 0

slug="$(pwd -P 2>/dev/null | sed 's#[/.]#-#g')"
[ -n "$slug" ] || exit 0

python3 "$fmt" "/tmp/claude-mirror-$slug.log" 2>/dev/null
exit 0
