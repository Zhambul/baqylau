#!/usr/bin/env bash
# claude-mirror.sh LOG
#
# Runs INSIDE the kitty "command mirror" split (opened by claude-split.sh) and
# execs the renderer on the given per-session log path. Producers (the *-fmt.py
# hooks + claude-stream.py / claude-substream.py) append width-INDEPENDENT paint
# ops (JSONL, keyed per session by claude_ops.log_path) to that log; claude-mirror.py
# reads them and paints at the pane's CURRENT width, re-rendering everything on
# resize (SIGWINCH) so the content reflows. Each command Claude runs appears as:
#
#   ▶ command            (highlighted, blue)
#   ──────────────
#   output
#   ──────────────
#   ■ finished · 1.2s    (highlighted, magenta)
#
# The renderer reads the log from the top (no truncation), so toggling the pane
# off/on re-shows the whole session's history; while off there is no process at all.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log="${1:-/tmp/claude-mirror-$(pwd -P | sed 's#[/.]#-#g').log}"

exec python3 "$here/claude-mirror.py" "$log"
