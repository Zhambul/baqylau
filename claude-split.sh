#!/usr/bin/env bash
# claude-split.sh open|close|toggle|grow|shrink|reset
#
# Manage a single "command mirror" vertical split — by default the right 25% of
# the tab — that live-tails the most recent Claude Code command output (see
# claude-mirror.sh).
#
#   open          create the mirror if absent (idempotent: never a second pane).
#                 Width = ${CLAUDE_MIRROR_BIAS:-25} percent. Wired to SessionStart.
#   close         close the mirror. Wired to SessionEnd.
#   toggle        close if present, else open. For an on/off keybinding.
#   grow [N]      widen the mirror by N cells   (default ${CLAUDE_MIRROR_STEP:-4}).
#   shrink [N]    narrow the mirror by N cells  (default ${CLAUDE_MIRROR_STEP:-4}).
#   reset         restore the layout's default split sizing.
#
# Keybinding (background) launches have no KITTY_WINDOW_ID, so we only require
# KITTY_LISTEN_ON — the one var this script actually uses. It is inherited by
# `launch --type=background` children when remote control is enabled.

set -u
# Keymap-launched background processes can inherit a minimal PATH; guarantee the
# tools we shell out to (python3, ps, tr, sed) resolve.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:${PATH:-}"
cmd="${1:-}"
DIR="/Users/z.yermagambet/code/personal/kitty"
MARK="claude_mirror"
# Mirror width (% of tab) and resize step (cells). SINGLE SOURCE OF TRUTH is the
# `env` block of Claude's settings.json — read from BOTH the user/global file and
# the project file, with the project overriding the global (Claude's own layering).
# That env reaches Claude's hook processes directly (already merged), but NOT the
# kitty keybindings (which launch this script from kitty's environment) — so when
# the var isn't already in our env, we read+merge the files ourselves. The
# keybindings pass `--cwd current`, so $PWD is the project here too.
# Precedence: inherited env (if any) → project settings → global settings → default.
SETTINGS_FILES=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json")
proj="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$proj" ]; then                       # walk up from cwd to the nearest .claude
  d="$PWD"
  while [ -n "$d" ] && [ "$d" != "/" ] && [ "$d" != "$HOME" ]; do
    if [ -d "$d/.claude" ]; then proj="$d"; break; fi
    d="$(dirname "$d")"
  done
fi
[ -n "$proj" ] && SETTINGS_FILES+=("$proj/.claude/settings.json" "$proj/.claude/settings.local.json")

read_setting() {  # $1 = env-var name; echoes its merged value across SETTINGS_FILES (last wins)
  python3 -c 'import json,sys
key=sys.argv[1]; val=None
for f in sys.argv[2:]:
    try:
        v=json.load(open(f)).get("env",{}).get(key)
        if v is not None: val=v
    except Exception: pass
sys.stdout.write("" if val is None else str(val))' "$1" "${SETTINGS_FILES[@]}" 2>/dev/null
}
BIAS="${CLAUDE_MIRROR_BIAS:-$(read_setting CLAUDE_MIRROR_BIAS)}"; BIAS="${BIAS:-25}"
STEP="${CLAUDE_MIRROR_STEP:-$(read_setting CLAUDE_MIRROR_STEP)}"; STEP="${STEP:-4}"

# Need socket remote control inside kitty, else no-op. A keymap-driven
# `launch --type=background` child does NOT inherit KITTY_LISTEN_ON, so when it
# is absent, resolve the controlling instance's socket ourselves: listen_on
# `unix:/tmp/kitty` yields `/tmp/kitty-<kitty-pid>`, and that kitty pid is an
# ancestor of this process. Fall back to the lone socket if just one instance.
if [ -z "${KITTY_LISTEN_ON:-}" ]; then
  pid="$PPID"
  while [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
    if [ -S "/tmp/kitty-$pid" ]; then KITTY_LISTEN_ON="unix:/tmp/kitty-$pid"; break; fi
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  done
  if [ -z "${KITTY_LISTEN_ON:-}" ]; then
    socks=(/tmp/kitty-*)
    [ "${#socks[@]}" -eq 1 ] && [ -S "${socks[0]}" ] && KITTY_LISTEN_ON="unix:${socks[0]}"
  fi
  export KITTY_LISTEN_ON
fi
[ -n "${KITTY_LISTEN_ON:-}" ] || exit 0

kitten="${KITTY_KITTEN_BIN:-}"
if [ -z "$kitten" ]; then
  if command -v kitten >/dev/null 2>&1; then kitten="kitten"
  elif [ -x /Applications/kitty.app/Contents/MacOS/kitten ]; then
    kitten="/Applications/kitty.app/Contents/MacOS/kitten"
  else exit 0; fi
fi

# 0 if a mirror pane already exists in this kitty instance.
mirror_exists() {
  "$kitten" @ ls 2>/dev/null | MARK="$MARK" python3 -c '
import json,os,sys
m=os.environ["MARK"]
for osw in json.load(sys.stdin):
  for t in osw["tabs"]:
    for w in t["windows"]:
      if w.get("user_vars",{}).get(m): sys.exit(0)
sys.exit(1)'
}

open_mirror() {
  mirror_exists && return 0
  slug="$(pwd -P | sed 's#[/.]#-#g')"
  # vsplit sizing only works in the splits layout; switch the active tab to it.
  "$kitten" @ goto-layout splits >/dev/null 2>&1
  "$kitten" @ launch \
    --location=vsplit --bias "$BIAS" --keep-focus --cwd current \
    --var "${MARK}=1" --title "◧ cmd mirror" \
    "$DIR/claude-mirror.sh" "$slug" >/dev/null 2>&1
}

close_mirror() {
  "$kitten" @ close-window --match "var:${MARK}=1" >/dev/null 2>&1
}

# Resize the mirror pane (matched by its marker, so it works regardless of which
# pane is focused). Positive increment widens the matched window.
resize_mirror() {
  "$kitten" @ resize-window --match "var:${MARK}=1" \
    --axis horizontal --increment "$1" >/dev/null 2>&1
}

# Resize the mirror to an ABSOLUTE width of PCT% of the tab. kitty can only resize
# by a relative increment (and its own `--axis reset` snaps to an equal split, not
# our target); worse, in the splits layout one increment unit isn't exactly one
# column, so a single delta over/undershoots. So we read the live geometry, resize
# toward the target, and ITERATE — re-measuring each time — until within a cell.
target_delta() {  # echo (target_cols - current_mirror_cols) for PCT% of the tab
  "$kitten" @ ls 2>/dev/null | PCT="$1" python3 -c '
import json,os,sys
pct=float(os.environ["PCT"])
# Find the tab that actually contains the mirror window (no reliance on focus,
# which is unset when the OS window is not frontmost).
for osw in json.load(sys.stdin):
  for t in osw["tabs"]:
    wins=t["windows"]
    cur=next((w.get("columns",0) for w in wins if w.get("user_vars",{}).get("claude_mirror")),0)
    if not cur: continue
    total=sum(w.get("columns",0) for w in wins)
    if total:
      print(round(total*pct/100.0)-cur)
    sys.exit(0)'
}

size_to() {
  local pct="$1" inc i
  for i in 1 2 3 4 5 6; do
    inc="$(target_delta "$pct")"
    case "$inc" in ''|*[!0-9-]*) return ;; esac     # no mirror / unreadable -> stop
    [ "$inc" = "0" ] && return                       # on target
    resize_mirror "$inc"
    [ "$inc" = "1" ] || [ "$inc" = "-1" ] && return  # within a cell -> avoid oscillation
    sleep 0.08                                        # let kitty apply before re-measuring
  done
}

case "$cmd" in
  open)   open_mirror ;;
  close)  close_mirror ;;
  toggle) if mirror_exists; then close_mirror; else open_mirror; fi ;;
  grow)   resize_mirror "${2:-$STEP}" ;;
  shrink) resize_mirror "-${2:-$STEP}" ;;
  reset)  size_to "$BIAS" ;;
  setpct) size_to "${2:-$BIAS}" ;;        # set to an absolute PCT% (size presets)
esac
exit 0
