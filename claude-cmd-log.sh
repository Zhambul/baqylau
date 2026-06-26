#!/usr/bin/env bash
# claude-cmd-log.sh — PostToolUse(Bash) hook.
#
# Appends a formatted block (command | output | elapsed) to the mirror log that
# the split pane tails (claude-mirror.sh). The PostToolUse payload carries a
# foreground command's full stdout/stderr and exact duration_ms — which is the
# ONLY place a foreground command's output is available (it is never written to
# the tasks/*.output files). So this is how foreground commands get mirrored.
#
# The actual formatting/highlighting/wrapping lives in claude-cmd-fmt.py (its own
# file so its regexes can use both quote chars without bash-quoting hazards). For
# a background command it also spawns claude-stream.py to tail the live output.
# This wrapper just finds the pane width and hands the hook payload to it.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fmt="$here/claude-cmd-fmt.py"
[ -f "$fmt" ] || exit 0

slug="$(pwd -P 2>/dev/null | sed 's#[/.]#-#g')"
[ -n "$slug" ] || exit 0

width="$("$here/claude-pane-width.sh" 2>/dev/null)"
[ -n "$width" ] || width=53

python3 "$fmt" "/tmp/claude-mirror-$slug.log" "$width" 2>/dev/null
exit 0
