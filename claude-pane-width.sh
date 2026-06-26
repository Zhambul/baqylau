#!/usr/bin/env bash
# claude-pane-width.sh — print the command-mirror pane's current column width
# (falls back to 53 if kitty remote control or the pane is unavailable). Shared
# by claude-cmd-log.sh and claude-monitor-log.sh.

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

kitten="${KITTY_KITTEN_BIN:-}"
if [ -z "$kitten" ]; then
  if command -v kitten >/dev/null 2>&1; then kitten="kitten"
  elif [ -x /Applications/kitty.app/Contents/MacOS/kitten ]; then
    kitten="/Applications/kitty.app/Contents/MacOS/kitten"
  fi
fi

width=53
if [ -n "$kitten" ] && [ -n "${KITTY_LISTEN_ON:-}" ]; then
  w="$("$kitten" @ --to "$KITTY_LISTEN_ON" ls 2>/dev/null | "$here/claude-mirror-width.py" 2>/dev/null)"
  case "$w" in ''|*[!0-9]*) ;; *) [ "$w" -ge 16 ] && width="$w" ;; esac
fi
printf '%s' "$width"
