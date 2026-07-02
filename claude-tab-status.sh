#!/usr/bin/env bash
# claude-tab-status.sh — color the kitty tab to reflect Claude Code's status.
#
# Usage: claude-tab-status.sh <state>
#   idle              session ready, nothing running             (grey)
#   thinking|working  Claude busy: reasoning / non-shell tool     (magenta, merged)
#   executing         a foreground shell command is running       (blue)
#   awaiting-bg       a background command / monitor / agent is running (blue)
#   awaiting-command  Claude is asking YOU a question                    (red)
#   awaiting-response Claude finished — your turn                 (green)
#   clear|reset       revert to the theme's default colors
#
# Colour intent: BLUE = something is running (a foreground command, a subagent —
# foreground or background, or a background command/monitor Claude awaits);
# RED = Claude is asking you a question; GREEN = done, your turn; MAGENTA = busy.
#
# Dispatch modes hooks pass instead of a literal state:
#   pretool   read the hook's stdin JSON. The tab tracks the MAIN session only, so
#             agent_id present (a subagent's / teammate's own tool call) -> IGNORED
#             (no change). Else by tool: AskUserQuestion/ExitPlanMode ->
#             awaiting-command (red), Bash/Task/Agent -> executing (blue), other ->
#             working (magenta). (Awaiting a FOREGROUND agent stays blue because the
#             main turn is blocked after its Task/Agent pretool; a BACKGROUND agent
#             is handled by stop -> awaiting-bg.)
#   posttool  (PostToolUse/Failure) agent_id present -> IGNORED; else working (magenta)
#   notify    read the Notification message; permission/approval -> awaiting-command
#             (red); else "waiting for your input" -> awaiting-response (green), UNLESS
#             a background job/teammate is still running -> awaiting-bg (blue)
#   stop      awaiting-response (green), or awaiting-bg (blue) when a background
#             command / monitor this session launched is still running
#   bg-recheck / bg-watch   flip the stale bg-running blue back to green when the
#             background job finishes (there is no "background finished" hook)
#
# Wired up via Claude Code hooks in ~/.claude/settings.json. Uses kitty remote
# control over the socket in $KITTY_LISTEN_ON, targeting the tab that contains
# the window Claude Code runs in ($KITTY_WINDOW_ID), so it works correctly even
# with many tabs / OS windows open. Always exits 0 so it can never block a hook.

set -u

# Returns 0 if a Claude Code background command / monitor / agent — OR a still-
# running FOREGROUND command (including one Ctrl+B'd into the background, which
# fires no distinct hook of its own) — launched from this project is still being
# streamed. Detection is via the command mirror's live tailer **slot markers**:
#   bg.<n> / monitor.<n>  — a claude-stream.py tailer for a background command/monitor
#   fg.<n>                — a claude-stream.py tailer for a LIVE-STREAMED FOREGROUND
#                           command (claude-cmd-pre.py); it keeps tailing for as long
#                           as the command's process is still writing, Ctrl+B or not,
#                           so this is what lets idle-watch (and a Ctrl+B conversion)
#                           correctly stay blue instead of flipping green underneath
#                           a command that's still running
#   sub.pid.<agent_id>    — a claude-substream.py tailer for a background SUBAGENT
# each holds its tailer's pid and is removed when the tailer exits, so a marker
# with a live pid == that job/agent is still running. (A foreground subagent's
# tailer also has a sub.pid marker, but it has already exited by Stop time — the
# main turn blocked on it — so only background agents remain.)
#
# (Earlier this scanned tasks/<id>.output write-holders via lsof, but FOREGROUND
# commands also hold those files while they run — so an async bg-recheck that
# coincided with a foreground command would mis-count it and refuse to clear the
# colour. Slot markers are created only by tailers, never by foreground commands,
# so they can't be fooled.) The mirror log — and thus its `.slots` dir — is now
# keyed PER SESSION (/tmp/claude-mirror-<session_id>.log.slots), so we must scan
# THIS session's slots, not a cwd-derived one (else a teammate/bg job goes
# undetected and the tab wrongly turns green). $SLOTS is resolved per dispatch
# from the session_id (stop payload) or passed in (bg-watch/bg-recheck); it falls
# back to the cwd slug to stay correct if a session_id is ever unavailable.
SLOTS=""
bg_command_running() {
  local slots slug f pid markers
  slots="$SLOTS"
  if [ -z "$slots" ]; then
    slug="$(pwd -P 2>/dev/null | sed 's#[/.]#-#g')"
    [ -n "$slug" ] && slots="/tmp/claude-mirror-${slug}.log.slots"
  fi
  [ -n "$slots" ] && [ -d "$slots" ] || return 1
  shopt -s nullglob
  markers=( "$slots"/bg.[0-9]* "$slots"/monitor.[0-9]* "$slots"/fg.[0-9]* "$slots"/sub.pid.* )
  shopt -u nullglob
  for f in "${markers[@]}"; do
    pid="$(cat "$f" 2>/dev/null)"
    case "$pid" in ''|*[!0-9]*) continue ;; esac   # not a pid (e.g. bg.next counter)
    kill -0 "$pid" 2>/dev/null && return 0          # a live tailer -> a job/agent is running
  done
  return 1
}

# Slots dir for a given session key (sanitised session_id), matching
# claude_ops.log_path so it points at exactly where the tailers write markers.
slots_for_sid() { printf '/tmp/claude-mirror-%s.log.slots' "$1"; }

state="${1:-}"

# Record the time of every REAL hook event (any dispatch except the internal
# watchers) so the idle watcher can tell "busy but quiet for a legit reason"
# (subagent running, long thinking — these still fire SOME hook within a couple
# minutes) from "interrupted and abandoned" (no hook at all for a long stretch).
# This must run before the pretool/posttool agent_id early-exits, so a teammate's
# inner tool calls also count as activity.
case "$state" in
  bg-watch|idle-watch|bg-recheck|interrupt-watch) ;;
  *) [ -n "${KITTY_WINDOW_ID:-}" ] && date +%s > "/tmp/claude-tab-activity-${KITTY_WINDOW_ID}" 2>/dev/null ;;
esac

# Absolute path to this script (for spawning the detached self-healing watcher).
self="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")"

# Spawn ONE detached bg-watch for this window (if not already running) that polls
# $SLOTS until no background job/agent remains, then flips the stale awaiting-bg
# blue back to green. Shared by the stop and agent-start dispatches.
ensure_bgwatch() {
  [ -n "${KITTY_WINDOW_ID:-}" ] && [ -x "$self" ] || return 0
  local wf="/tmp/claude-tab-bgwatch-${KITTY_WINDOW_ID}"
  if ! { [ -e "$wf" ] && kill -0 "$(cat "$wf" 2>/dev/null)" 2>/dev/null; }; then
    nohup "$self" bg-watch "$SLOTS" >/dev/null 2>&1 &   # pass this session's slots dir
    echo $! > "$wf" 2>/dev/null
  fi
}

# Spawn ONE detached idle-watch per window (if not already running). It clears a
# tab stuck in a BUSY colour (thinking/working/executing) after a long quiet
# stretch — the only way to recover from a user INTERRUPT, since Claude Code fires
# no hook on interruption (Stop/StopFailure don't fire), so nothing else resets it.
ensure_idlewatch() {
  [ -n "${KITTY_WINDOW_ID:-}" ] && [ -x "$self" ] || return 0
  local wf="/tmp/claude-tab-idlewatch-${KITTY_WINDOW_ID}"
  if ! { [ -e "$wf" ] && kill -0 "$(cat "$wf" 2>/dev/null)" 2>/dev/null; }; then
    nohup "$self" idle-watch "$SLOTS" >/dev/null 2>&1 &
    echo $! > "$wf" 2>/dev/null
  fi
}

# Spawn ONE detached interrupt-watch per window (if not already running): a FAST
# alternative to idle-watch's ~30s fallback for a cancelled turn that never ran a
# Bash/subagent tool (so has no marker/pid of its own to liveness-check) — a plain
# text reply or an Edit/Read/MCP tool call killed mid-flight leaves the tab stuck on
# magenta (thinking/working merged) otherwise. Claude Code appends a synthetic
# "[Request interrupted by user]" line to the session transcript the instant that
# happens (confirmed empirically, same as the subagent-cancel case) — this watcher
# tails the transcript for that line instead of waiting on idle-watch's long timer.
ensure_interruptwatch() {
  local transcript="$1"
  [ -n "${KITTY_WINDOW_ID:-}" ] && [ -x "$self" ] && [ -n "$transcript" ] || return 0
  local wf="/tmp/claude-tab-interruptwatch-${KITTY_WINDOW_ID}"
  if ! { [ -e "$wf" ] && kill -0 "$(cat "$wf" 2>/dev/null)" 2>/dev/null; }; then
    nohup "$self" interrupt-watch "$transcript" >/dev/null 2>&1 &
    echo $! > "$wf" 2>/dev/null
  fi
}

# Stop dispatch: it's your turn (green) — unless a background command/monitor
# Claude launched is still running, in which case Claude is awaiting that job,
# not you, so show blue (awaiting-bg). Red is reserved for Claude asking you a
# question (the notify dispatch), never for the turn merely ending.
if [ "$state" = "stop" ]; then
  # The Stop hook pipes its JSON on stdin — read session_id to scan THIS session's
  # slots (the mirror log is per-session now). Fall back to the cwd slug.
  _sp="$(cat 2>/dev/null)"
  _sid="$(printf '%s' "$_sp" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  if [ -n "$_sid" ]; then SLOTS="$(slots_for_sid "$_sid")"; fi
  if bg_command_running; then
    # A background command / monitor is still running — Claude is awaiting it (not
    # waiting on you), shown BLUE (same as a running foreground command), via a
    # distinct state name so the recheck/watch below can target it.
    state="awaiting-bg"
    # There's no "background finished" hook, and the per-job bg-recheck only fires
    # from that job's claude-stream.py tailer — so an UNTRACKED job (tailer died, or
    # a job with none) finishing would leave the tab stuck blue. The detached watcher
    # polls until no bg job remains, then flips this stale blue green.
    ensure_bgwatch
  else
    state="awaiting-response"
  fi
fi

# agent-start dispatch (called by claude-subagent-fmt.py when a background TEAMMATE
# begins a task): the main session is now awaiting that teammate, so the tab goes
# BLUE — even if the lead's turn had already ended (green). Without this, a teammate
# starting a new task between the lead's turns would leave the tab stuck green while
# the teammate works (SubagentStart otherwise never touches the tab). We also ensure
# the watcher is running so the blue clears once the team goes quiet.
if [ "$state" = "agent-start" ]; then
  SLOTS="${2:-}"
  state="awaiting-bg"
  ensure_bgwatch
fi

# bg-watch dispatch (the detached watcher spawned above): poll until no background
# job remains — or the state is no longer the bg-running blue (a new turn started)
# — then fall through to set that stale blue green. Self-removes its lock on exit.
if [ "$state" = "bg-watch" ]; then
  SLOTS="${2:-}"                         # this session's slots dir (from the stop spawn)
  win="${KITTY_WINDOW_ID:-}"
  [ -n "$win" ] || exit 0
  trap 'rm -f "/tmp/claude-tab-bgwatch-${win}" 2>/dev/null' EXIT
  cleared=1; misses=0
  for _ in $(seq 1 1800); do
    sleep 2
    [ "$(cat "/tmp/claude-tab-state-${win}" 2>/dev/null)" = "awaiting-bg" ] || exit 0
    if bg_command_running; then
      misses=0                       # something running -> reset
    else
      # GRACE: a teammate working in bursts drops its marker between tasks. Require
      # the team to stay quiet across several checks (~8s) before declaring green,
      # so an inter-task gap doesn't flip the tab green while the team is still going.
      misses=$((misses + 1))
      if [ "$misses" -ge 4 ]; then cleared=0; break; fi
    fi
  done
  [ "$cleared" -eq 0 ] || exit 0
  state="awaiting-response"
fi

# idle-watch dispatch (the detached watcher spawned at turn start): recovers a tab
# stuck in a BUSY colour after a user INTERRUPT (which fires no hook). It does NOT
# reset while work is happening — any active turn (incl. a running subagent/teammate)
# fires SOME hook within the window, refreshing the activity timestamp — only after a
# long fully-quiet stretch with nothing running does it conclude the turn was
# abandoned and flip to green (your turn). Exits as soon as the turn ends normally.
#
# This is now the backstop of LAST resort: Bash/background/foreground/subagent
# cancellation all have their own fast, targeted self-heal elsewhere (writer-
# liveness, meta.json's stoppedByUser, interrupt-watch's transcript tail below) that
# don't depend on this timer at all. What's LEFT for idle-watch to catch is
# cancelling before the model has produced anything (mid-thinking, before the first
# hook of the turn) — the harness silently rewinds that with no trace whatsoever
# (confirmed empirically: no transcript line, nothing), so a timeout is the only
# option. `bg_command_running` still guards it, so a genuinely long-running
# Bash/bg/fg/subagent job is NEVER a false positive here regardless of IDLE_SECS —
# the real exposure is narrower: a bare thinking stretch or a slow non-Bash tool
# (Read/Edit/Write/MCP) with zero hook activity for the whole window. 30s balances
# "recovers reasonably fast after a cancel" against "an ordinary thinking pause
# rarely runs long enough to false-positive" — raise CLAUDE_TAB_IDLE_SECS if a
# slow MCP tool or heavy-effort thinking starts tripping it.
if [ "$state" = "idle-watch" ]; then
  SLOTS="${2:-}"
  win="${KITTY_WINDOW_ID:-}"
  [ -n "$win" ] || exit 0
  trap 'rm -f "/tmp/claude-tab-idlewatch-${win}" 2>/dev/null' EXIT
  IDLE_SECS="${CLAUDE_TAB_IDLE_SECS:-30}"   # tunable; how long fully-quiet before "your turn"
  detected=1
  for _ in $(seq 1 1440); do
    sleep 5
    case "$(cat "/tmp/claude-tab-state-${win}" 2>/dev/null)" in
      thinking|working|executing) ;;     # still busy -> keep watching
      *) exit 0 ;;                        # turn ended / colour changed -> done
    esac
    act="$(cat "/tmp/claude-tab-activity-${win}" 2>/dev/null)"
    case "$act" in ''|*[!0-9]*) continue ;; esac
    if [ "$(( $(date +%s) - act ))" -ge "$IDLE_SECS" ] && ! bg_command_running; then
      detected=0; break                   # busy + quiet for IDLE_SECS + nothing running
    fi
  done
  [ "$detected" -eq 0 ] || exit 0
  state="awaiting-response"
fi

# interrupt-watch dispatch (the detached watcher spawned at turn start by the
# thinking dispatch below): a FAST alternative to idle-watch above for a turn
# cancelled before any Bash/subagent tool ran — those have their own fast self-heal
# (writer-liveness / meta.json polling) via a marker/pid this watcher doesn't need,
# but a plain text reply or an Edit/Read/MCP call killed mid-flight has neither, so
# it would otherwise sit on magenta for idle-watch's whole ~30s. Tails the
# transcript for the synthetic "[Request interrupted by user]" line Claude Code
# appends the instant a cancel happens, and flips green within one ~0.5s tick.
if [ "$state" = "interrupt-watch" ]; then
  transcript="${2:-}"
  win="${KITTY_WINDOW_ID:-}"
  [ -n "$win" ] && [ -n "$transcript" ] || exit 0
  trap 'rm -f "/tmp/claude-tab-interruptwatch-${win}" 2>/dev/null' EXIT
  pos="$(wc -c < "$transcript" 2>/dev/null || echo 0)"
  detected=1
  for _ in $(seq 1 3600); do
    sleep 0.5
    case "$(cat "/tmp/claude-tab-state-${win}" 2>/dev/null)" in
      thinking|working) ;;                # still busy in the magenta phase -> keep watching
      *) exit 0 ;;                         # moved to blue/red/green, or turn ended -> nothing to do
    esac
    size="$(wc -c < "$transcript" 2>/dev/null || echo "$pos")"
    if [ "$size" -gt "$pos" ]; then
      if tail -c "+$((pos + 1))" "$transcript" 2>/dev/null | grep -q '\[Request interrupted by user\]'; then
        detected=0; break
      fi
      pos="$size"
    fi
  done
  [ "$detected" -eq 0 ] || exit 0
  case "$(cat "/tmp/claude-tab-state-${win}" 2>/dev/null)" in
    thinking|working) ;;                  # re-check: still stuck busy right now -> safe to flip
    *) exit 0 ;;
  esac
  state="awaiting-response"
fi

# bg-recheck dispatch (called by claude-stream.py when a background job/monitor/live
# foreground stream finishes): there's no "background finished" hook, so the
# bg-running blue would linger until the next exchange. Here we flip that *stale*
# colour to green — but ONLY if the tab is currently awaiting-bg OR executing (so we
# never override working/idle/awaiting-command) and nothing else is still running.
#
# executing matters for a MANUALLY CANCELLED foreground command: cancelling one fires
# NO hook at all (same gap idle-watch exists for), so "executing" would otherwise
# stick until idle-watch's slow ~30s fallback. But the fg tailer (claude-cmd-pre.py)
# DOES notice its process died (has_writer goes false) and calls bg-recheck right
# then — a fast, reliable signal for exactly this case, so we honour it here too.
if [ "$state" = "bg-recheck" ]; then
  SLOTS="${2:-}"                         # this session's slots dir (passed by the tailer)
  cur=""
  [ -n "${KITTY_WINDOW_ID:-}" ] && cur="$(cat "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null)"
  case "$cur" in awaiting-bg|executing) ;; *) exit 0 ;; esac
  bg_command_running && exit 0
  # GRACE: a teammate finishing one task usually starts the next within a second or
  # two. Wait briefly and re-check so we don't flip green in that gap; if a new
  # marker appeared (next task started), stay blue. Also bail if the state changed.
  sleep 4
  bg_command_running && exit 0           # a new task started in the gap -> stay blue
  cur2=""
  [ -n "${KITTY_WINDOW_ID:-}" ] && cur2="$(cat "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null)"
  case "$cur2" in awaiting-bg|executing) ;; *) exit 0 ;; esac  # state moved on meanwhile -> leave it alone
  state="awaiting-response"
fi

# UserPromptSubmit dispatch ("thinking"): besides the literal colour (handled by
# the plain case at the bottom, as before), starts this turn's interrupt-watch —
# see its dispatch above — so a cancel with no Bash/subagent tool involved still
# clears the tab promptly instead of riding idle-watch's slow fallback.
if [ "$state" = "thinking" ]; then
  _up="$(cat 2>/dev/null)"
  _tp="$(printf '%s' "$_up" | grep -o '"transcript_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  ensure_interruptwatch "$_tp"
fi

# Notification dispatch: the hook pipes its JSON on stdin. A notification means
# Claude wants your attention. If it's asking you for a DECISION (a permission /
# tool-approval prompt), that's awaiting-command (red). Otherwise it's just
# "waiting for your input" — your turn — which is awaiting-response (green)... UNLESS
# a background job / teammate is still running, in which case Claude is awaiting THEM,
# not you, so it must stay blue (awaiting-bg). In an agent team, teammate messages /
# idle pings fire notifications constantly, and treating those as "your turn" was
# what turned the tab green while teammates were clearly still working.
if [ "$state" = "notify" ]; then
  _np="$(cat 2>/dev/null)"
  msg="$(printf '%s' "$_np" | grep -o '"message"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*:[[:space:]]*"([^"]*)"$/\1/')"
  _sid="$(printf '%s' "$_np" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  [ -n "$_sid" ] && SLOTS="$(slots_for_sid "$_sid")"
  case "$msg" in
    *[Pp]ermission*|*[Aa]pprov*|*confirmation*)
      state="awaiting-command" ;;   # a permission / approval prompt -> red (wins over bg)
    *)
      if bg_command_running; then
        state="awaiting-bg"; ensure_bgwatch   # teammates/bg still running -> blue, not green
      else
        state="awaiting-response"             # genuinely your turn -> green
      fi ;;
  esac
fi

# PreToolUse dispatch: the hook pipes its JSON on stdin. The tab tracks the MAIN
# session ONLY, so an event carrying an agent_id (a SUBAGENT's / TEAMMATE's own inner
# tool call) is IGNORED — it must not flip the tab while the main session is doing
# something else (thinking, or handed back to you). The main session still goes blue
# while it *awaits* an agent: a FOREGROUND agent keeps the main turn blocked after
# its Task/Agent pretool below sets blue (so blue persists), and a BACKGROUND agent
# is picked up by the Stop -> awaiting-bg path (a live sub.pid.* marker). For the
# main session's own tools:
#   - the Bash tool                   -> a shell command is running -> blue.
#   - the Task/Agent tool             -> launching/awaiting an agent -> blue.
#   - AskUserQuestion / ExitPlanMode  -> Claude is asking YOU -> red.
#   - every other tool (Edit/Read/Write/MCP/...) -> "working" (magenta).
if [ "$state" = "pretool" ]; then
  payload="$(cat 2>/dev/null)"
  agent_id="$(printf '%s' "$payload" | grep -o '"agent_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  [ -n "$agent_id" ] && exit 0                # subagent/teammate inner call -> don't touch the tab
  tool="$(printf '%s' "$payload" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  case "$tool" in
    AskUserQuestion|ExitPlanMode) state="awaiting-command" ;;  # Claude is asking YOU -> red
    Bash|Task|Agent)              state="executing"        ;;  # shell command / awaiting an agent -> blue
    *)                            state="working"          ;;  # other tool -> magenta (busy)
  esac
fi

# PostToolUse / PostToolUseFailure dispatch: after a tool finishes. An event with an
# agent_id is a SUBAGENT's / TEAMMATE's own tool finishing -> IGNORE it (the tab
# tracks the main session only). Otherwise it's the main agent between tools ->
# "working" (magenta).
if [ "$state" = "posttool" ]; then
  agent_id="$(grep -o '"agent_id"[[:space:]]*:[[:space:]]*"[^"]*"' 2>/dev/null | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  [ -n "$agent_id" ] && exit 0                # subagent/teammate inner call -> don't touch the tab
  state="working"
fi

# Whenever the tab enters a BUSY colour, make sure the idle watcher is running so an
# interrupt (which fires no hook) can't leave it stuck there forever.
case "$state" in thinking|working|executing) ensure_idlewatch ;; esac

# --- debug log: OFF by default. Set CLAUDE_TAB_DEBUG=1 to append every
#     invocation (state, window, socket) to claude-tab-status.log — handy for
#     verifying which hooks fire. Logged before the kitty guards so it captures
#     hooks even when remote control is unavailable.
if [ "${CLAUDE_TAB_DEBUG:-0}" = "1" ]; then
  printf '%s  %-18s win=%s listen=%s\n' \
    "$(date '+%H:%M:%S')" "${state:-<none>}" "${KITTY_WINDOW_ID:-?}" "${KITTY_LISTEN_ON:+set}" \
    >> /Users/z.yermagambet/code/personal/kitty/claude-tab-status.log 2>/dev/null
fi

# Must be inside kitty with socket remote control available, else no-op silently.
[ -n "${KITTY_WINDOW_ID:-}" ] || exit 0
[ -n "${KITTY_LISTEN_ON:-}" ] || exit 0

# Skip the work entirely when the tab is ALREADY showing this state. Tool-heavy
# turns fire many hooks that all resolve to the same colour (a run of Read/Edit/MCP
# calls all become "working"), and re-applying an identical colour is a wasted
# `kitten @` socket round-trip. The persisted state file (written at the end of every
# applied change) is our record of what's currently shown: if it matches, there's
# nothing to do — bail before locating the kitten binary or touching the socket.
# (clear/reset removes the file, so an empty prev_state means "already cleared".)
prev_state="$(cat "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null)"
case "$state" in
  clear|reset|"") [ -z "$prev_state" ] && exit 0 ;;
  *)              [ "$state" = "$prev_state" ] && exit 0 ;;
esac

# Locate the kitten binary (PATH first, then the macOS app bundle).
kitten="${KITTY_KITTEN_BIN:-}"
if [ -z "$kitten" ]; then
  if command -v kitten >/dev/null 2>&1; then
    kitten="kitten"
  elif [ -x /Applications/kitty.app/Contents/MacOS/kitten ]; then
    kitten="/Applications/kitty.app/Contents/MacOS/kitten"
  else
    exit 0
  fi
fi

# $1=active background  $2=active foreground  $3=inactive (dimmed) background.
# The status hue is shown on BOTH active and inactive tabs (so it's visible even
# when the tab isn't focused), but the INACTIVE background is a darkened variant
# of the same hue so the focused tab clearly stands out — otherwise active and
# inactive tabs share one background and only the bold font-style tells them apart.
# The inactive foreground is a fixed light grey that reads on every dim background.
set_color() {
  "$kitten" @ --to "$KITTY_LISTEN_ON" set-tab-color \
    --match "window_id:${KITTY_WINDOW_ID}" \
    active_bg="$1" active_fg="$2" inactive_bg="$3" inactive_fg="#c0c4cc" \
    >/dev/null 2>&1
}

case "$state" in
  idle)              set_color "#5c6370" "#e6e9ef" "#33373f" ;;  # grey  — ready, nothing running
  # thinking + working are merged: there's no signal to tell reasoning apart
  # from non-shell tool use / reply-writing, so both are one "busy" colour.
  thinking|working)  set_color "#c678dd" "#1a0620" "#4a2b52" ;;  # magenta — Claude busy (thinking/working)
  # blue — a command is running: a foreground shell command (executing), or a
  # background command / monitor Claude is awaiting (awaiting-bg). Same colour.
  executing|awaiting-bg) set_color "#61afef" "#06121f" "#2c4a63" ;;  # blue
  awaiting-command)  set_color "#e06c75" "#2a0608" "#5e2d31" ;;  # red — Claude is asking you a question
  awaiting-response) set_color "#98c379" "#07180a" "#445733" ;;  # green — done, your turn
  clear|reset|"")
    "$kitten" @ --to "$KITTY_LISTEN_ON" set-tab-color \
      --match "window_id:${KITTY_WINDOW_ID}" \
      active_bg=NONE active_fg=NONE inactive_bg=NONE inactive_fg=NONE \
      >/dev/null 2>&1
    ;;
  *) exit 0 ;;
esac

# Persist the resolved state so bg-recheck / bg-watch can tell whether a finishing
# background job should flip the stale bg-running blue back to green.
if [ -n "${KITTY_WINDOW_ID:-}" ]; then
  case "$state" in
    idle|thinking|working|executing|awaiting-bg|awaiting-command|awaiting-response)
      printf '%s' "$state" > "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null ;;
    clear|reset|"")
      rm -f "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null ;;
  esac
fi
exit 0
