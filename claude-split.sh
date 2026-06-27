#!/usr/bin/env bash
# claude-split.sh open|close|toggle|grow|shrink|reset|setpct
#
# Manage a "command mirror" vertical split — by default the right 25% of the tab —
# that renders the Claude Code session's activity (see claude-mirror.sh).
#
# PER SESSION. Everything is keyed by the Claude session_id so PARALLEL sessions
# never collide: each mirror pane carries var:claude_mirror=<sid>, each Claude pane
# carries var:claude_session=<sid> (tagged at SessionStart), and each session's
# content lives in its own /tmp/claude-mirror-<sid>.log. Toggling/resizing one
# session's mirror never touches another's.
#
#   open          SessionStart: truncate this session's log, tag the Claude pane,
#                 open its mirror at ${CLAUDE_MIRROR_BIAS:-25}%. (sid from stdin payload)
#   close         SessionEnd: close this session's mirror + remove its log. (sid from stdin)
#   toggle        close if present, else (re)open — WITHOUT truncating, so the
#                 session's history re-appears; while closed there is no process.
#   grow/shrink [N]  widen/narrow by N cells (default ${CLAUDE_MIRROR_STEP:-4}).
#   reset / setpct N set the width to BIAS% / N% of the tab.
#
# open/close get the sid from their hook payload (stdin); the keybindings have no
# payload, so they recover the sid from the currently focused kitty tab.
#
# Keybinding (background) launches have no KITTY_WINDOW_ID, so we only require
# KITTY_LISTEN_ON — it is inherited / self-resolved (see below).

set -u
# Keymap-launched background processes can inherit a minimal PATH; guarantee the
# tools we shell out to (python3, ps, tr, sed) resolve.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin:${PATH:-}"
cmd="${1:-}"
DIR="/Users/z.yermagambet/code/personal/kitty"
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

# --- session identity --------------------------------------------------------
sid_from_stdin() {   # SessionStart/SessionEnd: read session_id from the hook payload
  python3 -c 'import json,sys
try: sys.stdout.write(str(json.load(sys.stdin).get("session_id") or ""))
except Exception: pass' 2>/dev/null
}

sid_from_focus() {   # keybinding: the session of the currently focused kitty tab
  "$kitten" @ ls 2>/dev/null | python3 -c '
import json,sys
for o in json.load(sys.stdin):
  if not o.get("is_focused"): continue            # the frontmost OS window
  for t in o["tabs"]:
    if not t.get("is_focused"): continue          # its active tab
    sess=mir=""
    for w in t["windows"]:
      uv=w.get("user_vars",{})
      if uv.get("claude_session"): sess=uv["claude_session"]
      if uv.get("claude_mirror"):  mir=uv["claude_mirror"]
    sys.stdout.write(sess or mir); sys.exit(0)' 2>/dev/null
}

# Canonical per-session log path — derived by claude_ops.log_path so it is byte-for-
# byte the same path the producers write to (sid primary, cwd slug fallback).
log_for() {  # $1 = sid (may be empty -> cwd fallback)
  SID="$1" MDIR="$DIR" python3 -c 'import os,sys
sys.path.insert(0, os.environ["MDIR"])
import claude_ops as O
sys.stdout.write(O.log_path({"session_id": os.environ.get("SID",""), "cwd": os.getcwd()}))' 2>/dev/null
}

# --- pane ops, all scoped to ONE session's mirror (var:claude_mirror=<sid>) ----
mirror_exists() {  # $1 = sid
  "$kitten" @ ls 2>/dev/null | SID="$1" python3 -c '
import json,os,sys
sid=os.environ["SID"]
for osw in json.load(sys.stdin):
  for t in osw["tabs"]:
    for w in t["windows"]:
      if w.get("user_vars",{}).get("claude_mirror")==sid: sys.exit(0)
sys.exit(1)'
}

open_mirror() {  # $1=sid  $2=log  (does NOT truncate — caller decides)
  mirror_exists "$1" && return 0
  # vsplit sizing only works in the splits layout; switch the active tab to it.
  "$kitten" @ goto-layout splits >/dev/null 2>&1
  "$kitten" @ launch \
    --location=vsplit --bias "$BIAS" --keep-focus --cwd current \
    --var "claude_mirror=$1" --title "◧ cmd mirror" \
    "$DIR/claude-mirror.sh" "$2" >/dev/null 2>&1
}

close_mirror() { "$kitten" @ close-window --match "var:claude_mirror=$1" >/dev/null 2>&1; }  # $1=sid

tag_window() {  # $1=sid — tag THIS hook's own Claude pane so a keybinding can find it
  [ -n "${KITTY_WINDOW_ID:-}" ] && \
    "$kitten" @ set-user-vars --match "id:${KITTY_WINDOW_ID}" "claude_session=$1" >/dev/null 2>&1
}

resize_mirror() {  # $1=inc  $2=sid  (positive increment widens)
  "$kitten" @ resize-window --match "var:claude_mirror=$2" \
    --axis horizontal --increment "$1" >/dev/null 2>&1
}

# Resize a session's mirror to an ABSOLUTE width of PCT% of the tab. kitty only
# resizes by a relative increment (and its `--axis reset` snaps to 50/50); worse,
# in the splits layout one increment unit isn't exactly one column, so a single
# delta over/undershoots. So read the live geometry, resize toward the target, and
# ITERATE — re-measuring — until within a cell.
target_delta() {  # $1=pct  $2=sid  -> (target_cols - current_mirror_cols)
  "$kitten" @ ls 2>/dev/null | PCT="$1" SID="$2" python3 -c '
import json,os,sys
pct=float(os.environ["PCT"]); sid=os.environ["SID"]
for osw in json.load(sys.stdin):
  for t in osw["tabs"]:
    wins=t["windows"]
    cur=next((w.get("columns",0) for w in wins if w.get("user_vars",{}).get("claude_mirror")==sid),0)
    if not cur: continue
    total=sum(w.get("columns",0) for w in wins)
    if total:
      print(round(total*pct/100.0)-cur)
    sys.exit(0)'
}

size_to() {  # $1=pct  $2=sid
  local pct="$1" sid="$2" inc i
  for i in 1 2 3 4 5 6; do
    inc="$(target_delta "$pct" "$sid")"
    case "$inc" in ''|*[!0-9-]*) return ;; esac     # no mirror / unreadable -> stop
    [ "$inc" = "0" ] && return                       # on target
    resize_mirror "$inc" "$sid"
    [ "$inc" = "1" ] || [ "$inc" = "-1" ] && return  # within a cell -> avoid oscillation
    sleep 0.08                                        # let kitty apply before re-measuring
  done
}

case "$cmd" in
  open)                                              # SessionStart (payload on stdin)
    sid="$(sid_from_stdin)"
    log="$(log_for "$sid")"
    [ -n "$log" ] || exit 0
    : > "$log"                                       # fresh log for this session
    tag_window "$sid"
    open_mirror "$sid" "$log"
    ;;
  close)                                             # SessionEnd (payload on stdin)
    sid="$(sid_from_stdin)"
    [ -n "$sid" ] && close_mirror "$sid"
    log="$(log_for "$sid")"
    [ -n "$log" ] && rm -f "$log"                    # remove this session's log
    ;;
  toggle)                                            # keybinding
    sid="$(sid_from_focus)"; [ -n "$sid" ] || exit 0
    if mirror_exists "$sid"; then
      close_mirror "$sid"                            # keep the log -> history preserved
    else
      open_mirror "$sid" "$(log_for "$sid")"         # no truncation -> re-shows history
    fi
    ;;
  grow)   sid="$(sid_from_focus)"; [ -n "$sid" ] && resize_mirror "${2:-$STEP}" "$sid" ;;
  shrink) sid="$(sid_from_focus)"; [ -n "$sid" ] && resize_mirror "-${2:-$STEP}" "$sid" ;;
  reset)  sid="$(sid_from_focus)"; [ -n "$sid" ] && size_to "$BIAS" "$sid" ;;
  setpct) sid="$(sid_from_focus)"; [ -n "$sid" ] && size_to "${2:-$BIAS}" "$sid" ;;
esac
exit 0
