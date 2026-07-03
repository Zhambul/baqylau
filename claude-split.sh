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

# --- audit: pane operations (open/close/toggle/resize + failures) ---------------
# The kitten calls here are all silenced (>/dev/null), so a mirror that failed to
# open — or a resize that did nothing — used to leave no evidence. Every pane op
# is recorded in the audit DB's pane_events (fire-and-forget; CLAUDE_AUDIT=0 off).
audit_pane() {  # $1=sid $2=action $3=ok(0|1) $4=detail
  [ "${CLAUDE_AUDIT:-1}" = "0" ] && return 0
  ( nohup python3 "$DIR/claude_audit.py" pane "$1" "$2" "$3" "$4" \
      >/dev/null 2>&1 & ) 2>/dev/null || true
}

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

# --- per-project remembered size --------------------------------------------
# The width you set (grow/shrink/setpct/reset) is remembered PER PROJECT and
# restored on the next SessionStart. Keyed by the project cwd — $PWD is the project
# both at SessionStart (runs in it) and for the keybindings (they pass --cwd
# current). Stored under the Claude config dir so it survives restarts.
SIZE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/kitty-mirror-sizes"
size_file() { printf '%s/%s' "$SIZE_DIR" "$(pwd -P 2>/dev/null | sed 's#[/.]#-#g')"; }

project_bias() {  # remembered % for this project, or the configured default (BIAS)
  local b; b="$(cat "$(size_file)" 2>/dev/null)"
  case "$b" in ''|*[!0-9]*) printf '%s' "$BIAS" ;; *) printf '%s' "$b" ;; esac
}

current_pct() {  # $1=sid -> the mirror's current width as % of its tab
  "$kitten" @ ls 2>/dev/null | SID="$1" python3 -c '
import json,os,sys
sid=os.environ["SID"]
for osw in json.load(sys.stdin):
  for t in osw["tabs"]:
    wins=[w for w in t["windows"] if not w.get("user_vars",{}).get("claude_scorebar")]
    cur=next((w.get("columns",0) for w in wins if w.get("user_vars",{}).get("claude_mirror")==sid),0)
    if not cur: continue
    total=sum(w.get("columns",0) for w in wins)
    if total: print(round(100*cur/total))
    sys.exit(0)'
}

save_size() {  # $1=sid — remember the mirror's current % for this project
  local pct; pct="$(current_pct "$1")"
  case "$pct" in ''|*[!0-9]*) return ;; esac
  mkdir -p "$SIZE_DIR" 2>/dev/null
  printf '%s' "$pct" > "$(size_file)" 2>/dev/null
}

# --- pane ops, all scoped to ONE session's mirror (var:claude_mirror=<sid>) ----
# The mirror pane carries a small companion: the SCOREBOARD BAR (claude-scorebar.py),
# a ~2-row window hsplit under it (var:claude_scorebar=<sid>). Its own window — not
# lines pinned inside the mirror — so scrolling the mirror's history can't scroll it
# away. Opened/closed with the mirror; excluded from the width math below (it shares
# the mirror's column, so counting its columns would double-count that column).
window_exists() {  # $1 = user-var name  $2 = sid
  "$kitten" @ ls 2>/dev/null | VAR="$1" SID="$2" python3 -c '
import json,os,sys
var,sid=os.environ["VAR"],os.environ["SID"]
for osw in json.load(sys.stdin):
  for t in osw["tabs"]:
    for w in t["windows"]:
      if w.get("user_vars",{}).get(var)==sid: sys.exit(0)
sys.exit(1)'
}

mirror_exists() { window_exists claude_mirror "$1"; }  # $1 = sid

# Close any STALE mirror/scoreboard in the tab whose sid differs from $1. A session's
# id changes on --resume/--continue (and often /clear): SessionStart then re-tags the
# Claude pane and opens a mirror keyed by the NEW sid, while the OLD-sid mirror lingers
# in the same tab — tailing a log nothing writes to anymore (frozen) and doubling the
# pane. One tab holds exactly one Claude session, so a mirror there with a different sid
# is always stale. Anchored to KITTY_WINDOW_ID (the hook's Claude pane) when present,
# else the focused tab (keybinding). No-op when there's nothing stale to close.
close_stale_mirrors() {  # $1 = sid to KEEP
  local ids id
  ids="$("$kitten" @ ls 2>/dev/null | KEEP="$1" ANCHOR="${KITTY_WINDOW_ID:-}" python3 -c '
import json,os,sys
keep=os.environ["KEEP"]; anchor=os.environ.get("ANCHOR","")
for osw in json.load(sys.stdin):
  for t in osw["tabs"]:
    if anchor:
      if not any(str(w.get("id"))==anchor for w in t["windows"]): continue
    elif not (osw.get("is_focused") and t.get("is_focused")):
      continue
    for w in t["windows"]:
      uv=w.get("user_vars",{})
      sid=uv.get("claude_mirror") or uv.get("claude_scorebar")
      if sid and sid!=keep: print(w.get("id"))
    sys.exit(0)' 2>/dev/null)"
  for id in $ids; do
    "$kitten" @ close-window --match "id:$id" >/dev/null 2>&1
  done
}

# kitty bias is approximate ("you cannot use this method to create windows of fixed
# sizes"), so after launching the bar, iterate relative resizes until it is exactly
# BAR_ROWS tall (or kitty's minimum stops shrinking it).
BAR_ROWS=4   # ⬡ session id + ✉ message census + 2 session-stats rows
bar_delta() {  # $1=sid -> (BAR_ROWS - current bar rows)
  "$kitten" @ ls 2>/dev/null | SID="$1" ROWS="$BAR_ROWS" python3 -c '
import json,os,sys
sid=os.environ["SID"]
for osw in json.load(sys.stdin):
  for t in osw["tabs"]:
    for w in t["windows"]:
      if w.get("user_vars",{}).get("claude_scorebar")==sid:
        print(int(os.environ["ROWS"])-int(w.get("lines") or 0)); sys.exit(0)'
}

size_bar() {  # $1=sid
  local d i
  for i in 1 2 3; do
    d="$(bar_delta "$1")"
    case "$d" in ''|0|*[!0-9-]*) return ;; esac
    "$kitten" @ resize-window --match "var:claude_scorebar=$1" \
      --axis vertical --increment "$d" >/dev/null 2>&1
    sleep 0.08                              # let kitty apply before re-measuring
  done
}

open_mirror() {  # $1=sid  $2=log  $3=bias%  (does NOT truncate — caller decides)
  if ! mirror_exists "$1"; then
    # vsplit sizing only works in the splits layout; switch the active tab to it.
    "$kitten" @ goto-layout splits >/dev/null 2>&1
    "$kitten" @ launch \
      --location=vsplit --bias "${3:-$BIAS}" --keep-focus --cwd current \
      --var "claude_mirror=$1" --title "◧ cmd mirror" \
      "$DIR/claude-mirror.sh" "$2" >/dev/null 2>&1
  fi
  if ! window_exists claude_scorebar "$1"; then   # checked separately so a crashed/
    "$kitten" @ launch \
      --location=hsplit --next-to "var:claude_mirror=$1" --bias 5 \
      --keep-focus --cwd current \
      --var "claude_scorebar=$1" --title "▪ session" \
      "$DIR/claude-scorebar.py" "$2" >/dev/null 2>&1   # closed bar comes back on toggle
    size_bar "$1"
  fi
}

close_mirror() {  # $1=sid — the bar rides along with the mirror
  "$kitten" @ close-window --match "var:claude_scorebar=$1" >/dev/null 2>&1
  "$kitten" @ close-window --match "var:claude_mirror=$1" >/dev/null 2>&1
}

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
    wins=[w for w in t["windows"] if not w.get("user_vars",{}).get("claude_scorebar")]
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
    payload="$(cat 2>/dev/null)"
    sid="$(printf '%s' "$payload" | sid_from_stdin)"
    # Register the session in the audit DB (always on; CLAUDE_AUDIT=0 disables).
    [ "${CLAUDE_AUDIT:-1}" = "0" ] || \
      printf '%s' "$payload" | python3 "$DIR/claude_audit.py" session-start >/dev/null 2>&1 || true
    log="$(log_for "$sid")"
    [ -n "$log" ] || exit 0
    : > "$log"                                       # fresh log for this session
    tag_window "$sid"
    close_stale_mirrors "$sid"                       # drop a prior-sid mirror (resume/clear) so it can't double up
    open_mirror "$sid" "$log" "$(project_bias)"      # restore this project's remembered size
    # Verify the panes actually exist now — open_mirror's kitten calls are silent.
    if mirror_exists "$sid"; then
      if window_exists claude_scorebar "$sid"; then
        audit_pane "$sid" "open" 1 "bias=$(project_bias)%"
      else
        audit_pane "$sid" "open" 0 "mirror opened but scoreboard bar absent"
      fi
    else
      audit_pane "$sid" "open" 0 "mirror window absent after launch"
    fi
    # Stream any codex run (companion job OR raw `codex`/`codex exec`) into this
    # session's mirror. The launcher Popens the watcher DETACHED (start_new_session)
    # and exits in a few ms, so it can never hang SessionStart — never a bash `&`,
    # which would leave the long-lived watcher in the hook's process group. The
    # watcher exits on its own when this log is removed at SessionEnd.
    [ -f "$DIR/claude-codex-launch.py" ] && \
      python3 "$DIR/claude-codex-launch.py" "$log" "$PWD" "$sid" >/dev/null 2>&1 || true
    ;;
  close)                                             # SessionEnd (payload on stdin)
    payload="$(cat 2>/dev/null)"
    sid="$(printf '%s' "$payload" | sid_from_stdin)"
    # Stamp the session's end in the audit DB (also prunes sessions > 30 days old).
    [ "${CLAUDE_AUDIT:-1}" = "0" ] || \
      printf '%s' "$payload" | python3 "$DIR/claude_audit.py" session-end >/dev/null 2>&1 || true
    [ -n "$sid" ] && close_mirror "$sid"
    audit_pane "$sid" "close" 1 "session end"
    log="$(log_for "$sid")"
    [ -n "$log" ] && rm -f "$log" "$log.stats.json" "$log.msgs.json"  # log + score + msg sidecars
    ;;
  toggle)                                            # keybinding
    sid="$(sid_from_focus)"; [ -n "$sid" ] || exit 0
    if mirror_exists "$sid"; then
      close_mirror "$sid"                            # keep the log -> history preserved
      audit_pane "$sid" "toggle-off" 1 ""
    else
      close_stale_mirrors "$sid"                     # clear any prior-sid pane in this tab first
      open_mirror "$sid" "$(log_for "$sid")" "$(project_bias)"   # remembered size, keep history
      if mirror_exists "$sid"; then
        audit_pane "$sid" "toggle-on" 1 "bias=$(project_bias)%"
      else
        audit_pane "$sid" "toggle-on" 0 "mirror window absent after launch"
      fi
    fi
    ;;
  # Resize, then remember the resulting % for this project. grow/shrink settle a
  # moment after the async resize, so pause briefly before measuring. The audited
  # detail carries the RESULTING width — a resize that changed nothing is visible.
  grow)   sid="$(sid_from_focus)"; [ -n "$sid" ] && { resize_mirror "${2:-$STEP}" "$sid"; sleep 0.2; save_size "$sid"; audit_pane "$sid" "grow" 1 "+${2:-$STEP} cells -> $(current_pct "$sid")%"; } ;;
  shrink) sid="$(sid_from_focus)"; [ -n "$sid" ] && { resize_mirror "-${2:-$STEP}" "$sid"; sleep 0.2; save_size "$sid"; audit_pane "$sid" "shrink" 1 "-${2:-$STEP} cells -> $(current_pct "$sid")%"; } ;;
  reset)  sid="$(sid_from_focus)"; [ -n "$sid" ] && { size_to "$BIAS" "$sid"; save_size "$sid"; audit_pane "$sid" "reset" 1 "target=$BIAS% -> $(current_pct "$sid")%"; } ;;
  setpct) sid="$(sid_from_focus)"; [ -n "$sid" ] && { size_to "${2:-$BIAS}" "$sid"; save_size "$sid"; audit_pane "$sid" "setpct" 1 "target=${2:-$BIAS}% -> $(current_pct "$sid")%"; } ;;
esac
exit 0
