#!/usr/bin/env bash
# claude-mirror.sh [slug]
#
# Runs INSIDE the kitty "command mirror" split (opened by claude-split.sh).
# It simply tails the command log that claude-cmd-log.sh (a PostToolUse hook)
# appends to. Each command Claude runs appears as a block:
#
#   ▶ command            (highlighted, blue)
#   ──────────────
#   output
#   ──────────────
#   ■ finished · 1.2s    (highlighted, magenta)
#
# Foreground command output is captured from the PostToolUse payload (it is not
# in any tasks file), so each block appears when its command completes. Single
# `tail` process — no file-switching, no orphaned tails, no interleaving.

slug="${1:-$(pwd -P | sed 's#[/.]#-#g')}"
log="/tmp/claude-mirror-$slug.log"

: > "$log" 2>/dev/null    # start fresh each time the pane opens

clear 2>/dev/null || printf '\033[2J\033[H'
printf '\033[38;5;244m ◧ command mirror — waiting for commands… \033[0m\n'

exec tail -n +1 -F "$log" 2>/dev/null
