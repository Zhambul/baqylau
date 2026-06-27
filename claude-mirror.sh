#!/usr/bin/env bash
# claude-mirror.sh [slug]
#
# Runs INSIDE the kitty "command mirror" split (opened by claude-split.sh) and
# execs the renderer. Producers (the *-fmt.py hooks + claude-stream.py /
# claude-substream.py) append width-INDEPENDENT paint ops (JSONL) to the mirror
# log; claude-mirror.py reads them and paints at the pane's CURRENT width,
# re-rendering everything on resize (SIGWINCH) so the content reflows. Each command
# Claude runs still appears as a block:
#
#   ▶ command            (highlighted, blue)
#   ──────────────
#   output
#   ──────────────
#   ■ finished · 1.2s    (highlighted, magenta)
#
# Foreground command output is captured from the PostToolUse payload (it is not in
# any tasks file), so each block appears when its command completes. A single
# renderer process — no file-switching, no orphaned tails, no interleaving.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
slug="${1:-$(pwd -P | sed 's#[/.]#-#g')}"
log="/tmp/claude-mirror-$slug.log"

exec python3 "$here/claude-mirror.py" "$log"
