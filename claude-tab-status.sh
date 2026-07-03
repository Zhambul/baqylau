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
#             a background job/teammate is still running -> awaiting-bg (blue), or the
#             tab was already awaiting-bg (a teammate just finished and the main is
#             being re-invoked to process it) -> working (magenta), not your turn
#   stop      awaiting-response (green), or awaiting-bg (blue) when a background
#             command / monitor this session launched is still running
#   bg-recheck / bg-watch   flip the stale bg-running blue when the background job
#             finishes (there is no "background finished" hook): to green for an
#             untracked shell job (fg/bg/monitor), but to working (magenta) for a
#             finishing SUBAGENT/TEAMMATE (kind=sub) — Claude Code re-invokes the main
#             to process its result, so the main is taking over, not handing back
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
#                           so this is what lets bg-watch (and a Ctrl+B conversion)
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

# Absolute path to this script (for spawning the detached self-healing watcher).
self="$(cd "$(dirname "$0")" 2>/dev/null && pwd)/$(basename "$0")"
DIR="$(dirname "$self")"

# --- audit trail (always on; CLAUDE_AUDIT=0 disables) --------------------------
# Every state decision this script makes — applied, skipped, or an early bail —
# is recorded in the SQLite audit DB (see claude_audit.py) as a tab_transitions
# row, replacing the old opt-in CLAUDE_TAB_DEBUG flat-file logs. Fired detached
# in the background so the latency-sensitive tab path is never blocked, and
# always || true so auditing can never break a hook.
dispatch="$state"      # the raw arg, before the dispatch blocks rewrite $state
AUDIT_SID=""           # set by dispatches that learn the session_id
REASON=""              # why the final state was chosen (set by dispatch blocks)
audit_tx() {  # $1=prev $2=new $3=applied(0|1) $4=reason
  [ "${CLAUDE_AUDIT:-1}" = "0" ] && return 0
  ( nohup python3 "$DIR/claude_audit.py" transition \
      "$AUDIT_SID" "${KITTY_WINDOW_ID:-}" "$dispatch" "$1" "$2" "$3" "$4" \
      >/dev/null 2>&1 & ) 2>/dev/null || true
}
sid_from_slots() {  # /tmp/claude-mirror-<sid>.log.slots -> <sid>
  printf '%s' "$1" | sed -E 's#.*/claude-mirror-(.*)\.log\.slots$#\1#'
}

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

# Spawn ONE detached interrupt-watch per window (if not already running): the
# recovery for a cancelled turn that never ran a Bash/subagent tool (so has no
# marker/pid of its own to liveness-check) — a plain text reply or an Edit/Read/MCP
# tool call killed mid-flight leaves the tab stuck on magenta (thinking/working
# merged) otherwise. Claude Code appends a synthetic "[Request interrupted by user]"
# line to the session transcript the instant that happens (confirmed empirically,
# same as the subagent-cancel case) — this watcher tails the transcript for that
# line and flips green within ~0.5s.
#
# KNOWN GAP (deliberate): cancelling BEFORE the model has produced anything at all
# (mid-thinking) leaves no trace anywhere — no hook, no transcript line, nothing
# (confirmed empirically) — so the tab stays magenta until the next interaction
# resets it. A timeout backstop for that case (idle-watch, "fully quiet for N secs
# -> green") was removed: long thinking fires zero hooks and writes nothing, which
# is EXACTLY the same signature as the cancel, so any timeout short enough to be
# useful false-positived on every long thinking stretch (tab lied "done" mid-turn).
# The stale magenta after a mid-thinking cancel is rarer and self-corrects at the
# next prompt, which the cancelling user is typically about to type anyway.
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
  # A Stop with an agent_id is an AGENT's stop, never the lead's -> ignore, same as
  # pretool/posttool. agent_type is NOT such a signal: a main session whose whole
  # thread runs a custom agent (settings `agent` / --agent, e.g. a "task-manager"
  # orchestrator tab) carries agent_type on its own genuine turn-end Stops —
  # filtering on it left that tab permanently stuck on magenta (confirmed live).
  if printf '%s' "$_sp" | grep -q '"agent_id"[[:space:]]*:[[:space:]]*"[^"]'; then
    audit_tx "" "" 0 "ignored: agent stop, not the lead's"; exit 0
  fi
  _sid="$(printf '%s' "$_sp" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  AUDIT_SID="$_sid"
  if [ -n "$_sid" ]; then SLOTS="$(slots_for_sid "$_sid")"; fi
  if bg_command_running; then
    # A background command / monitor is still running — Claude is awaiting it (not
    # waiting on you), shown BLUE (same as a running foreground command), via a
    # distinct state name so the recheck/watch below can target it.
    state="awaiting-bg"
    REASON="stop: live tailer slot marker(s) in $SLOTS"
    # There's no "background finished" hook, and the per-job bg-recheck only fires
    # from that job's claude-stream.py tailer — so an UNTRACKED job (tailer died, or
    # a job with none) finishing would leave the tab stuck blue. The detached watcher
    # polls until no bg job remains, then flips this stale blue green.
    ensure_bgwatch
  elif printf '%s' "$_sp" | grep -q '"status"[[:space:]]*:[[:space:]]*"running"'; then
    # No live tailer marker, but the Stop payload's own background_tasks list says a
    # teammate/background task is still RUNNING. Markers are burst-scoped — a teammate
    # idling between tasks has released its streamer — so the payload is the more
    # truthful signal here: Claude is awaiting the team, not you. Stay blue.
    state="awaiting-bg"
    REASON="stop: payload background_tasks reports status=running"
    ensure_bgwatch
  else
    state="awaiting-response"
    REASON="stop: nothing running"
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
  AUDIT_SID="$(sid_from_slots "$SLOTS")"
  state="awaiting-bg"
  REASON="agent-start: main session now awaiting a subagent/teammate"
  ensure_bgwatch
fi

# bg-watch dispatch (the detached watcher spawned above): poll until no background
# job remains — or the state is no longer the bg-running blue (a new turn started)
# — then fall through to set that stale blue green. Self-removes its lock on exit.
if [ "$state" = "bg-watch" ]; then
  SLOTS="${2:-}"                         # this session's slots dir (from the stop spawn)
  AUDIT_SID="$(sid_from_slots "$SLOTS")"
  win="${KITTY_WINDOW_ID:-}"
  [ -n "$win" ] || exit 0
  trap 'rm -f "/tmp/claude-tab-bgwatch-${win}" 2>/dev/null' EXIT
  cleared=1; misses=0
  for _ in $(seq 1 1800); do
    sleep 2
    if [ "$(cat "/tmp/claude-tab-state-${win}" 2>/dev/null)" != "awaiting-bg" ]; then
      audit_tx "" "" 0 "bg-watch: state moved on, watcher exiting"; exit 0
    fi
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
  REASON="bg-watch: no live markers across ~8s of checks"
fi

# interrupt-watch dispatch (the detached watcher spawned at turn start by the
# thinking dispatch below): recovery for a turn cancelled before any Bash/subagent
# tool ran — those have their own fast self-heal (writer-liveness / meta.json
# polling) via a marker/pid this watcher doesn't need, but a plain text reply or an
# Edit/Read/MCP call killed mid-flight has neither, so it would otherwise sit on
# magenta until the next interaction. Tails the
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
    *) audit_tx "" "" 0 "interrupt-watch: interrupt seen but state moved on"; exit 0 ;;
  esac
  state="awaiting-response"
  REASON="interrupt-watch: [Request interrupted by user] in transcript"
fi

# bg-recheck dispatch (called by claude-stream.py when a background job/monitor/live
# foreground stream finishes): there's no "background finished" hook, so the
# bg-running blue would linger until the next exchange. Here we flip that *stale*
# colour to green — but ONLY if the tab is currently awaiting-bg OR executing (so we
# never override working/idle/awaiting-command) and nothing else is still running.
#
# executing matters for a MANUALLY CANCELLED foreground command: cancelling one fires
# NO hook at all (the same no-hook-on-interrupt gap noted above), so "executing"
# would otherwise stick until the next interaction. But the fg tailer
# (claude-cmd-pre.py) DOES notice its process died (has_writer goes false) and calls
# bg-recheck right then — a fast, reliable signal for exactly this case, so we
# honour it here too.
if [ "$state" = "bg-recheck" ]; then
  SLOTS="${2:-}"                         # this session's slots dir (passed by the tailer)
  kind="${3:-}"                          # which tailer is calling: fg / bg / monitor / sub
  AUDIT_SID="$(sid_from_slots "$SLOTS")"
  cur=""
  [ -n "${KITTY_WINDOW_ID:-}" ] && cur="$(cat "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null)"
  # Clearing "executing" exists SOLELY for the cancelled-foreground-command case,
  # where the caller is that command's own fg tailer noticing its writer died. Any
  # OTHER tailer (a finishing teammate/subagent/bg job) calling in while the tab
  # shows executing means the MAIN session is running its own command — flipping
  # that green painted "done" over a still-working lead. Only fg may clear it.
  case "$cur" in
    awaiting-bg) ;;
    executing)   [ "$kind" = "fg" ] || { audit_tx "$cur" "" 0 "bg-recheck($kind): only fg may clear executing"; exit 0; } ;;
    *)           audit_tx "$cur" "" 0 "bg-recheck($kind): tab not on a bg-running colour"; exit 0 ;;
  esac
  bg_command_running && { audit_tx "$cur" "" 0 "bg-recheck($kind): another job still running"; exit 0; }
  # GRACE: a teammate finishing one task usually starts the next within a second or
  # two. Wait briefly and re-check so we don't flip green in that gap; if a new
  # marker appeared (next task started), stay blue. Also bail if the state changed.
  sleep 4
  bg_command_running && { audit_tx "$cur" "" 0 "bg-recheck($kind): a new job started in the grace gap"; exit 0; }
  cur2=""
  [ -n "${KITTY_WINDOW_ID:-}" ] && cur2="$(cat "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null)"
  case "$cur2" in                                              # state moved on meanwhile -> leave it alone
    awaiting-bg) ;;
    executing)   [ "$kind" = "fg" ] || { audit_tx "$cur2" "" 0 "bg-recheck($kind): state moved on in the gap"; exit 0; } ;;
    *)           audit_tx "$cur2" "" 0 "bg-recheck($kind): state moved on in the gap"; exit 0 ;;
  esac
  REASON="bg-recheck($kind): no live markers remain"
  # A finishing SUBAGENT/TEAMMATE (kind=sub) does NOT mean it's your turn: Claude Code
  # re-invokes the main session to process the teammate's result the instant it
  # completes, so the main is about to TAKE OVER, not hand back to you. Painting green
  # here produced a visible green flash before the main's own hooks (or its next Stop)
  # repainted magenta. Go straight to "working" (magenta) so the tab reflects the main
  # resuming; its subsequent Stop sets green once that follow-up turn genuinely ends.
  # Untracked shell jobs (fg/bg/monitor) don't re-invoke the main, so those still go green.
  if [ "$kind" = "sub" ]; then state="working"; else state="awaiting-response"; fi
fi

# UserPromptSubmit dispatch ("thinking"): besides the literal colour (handled by
# the plain case at the bottom, as before), starts this turn's interrupt-watch —
# see its dispatch above — so a cancel with no Bash/subagent tool involved still
# clears the tab promptly.
if [ "$state" = "thinking" ]; then
  _up="$(cat 2>/dev/null)"
  _tp="$(printf '%s' "$_up" | grep -o '"transcript_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  AUDIT_SID="$(printf '%s' "$_up" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  REASON="prompt submitted"
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
  AUDIT_SID="$_sid"
  [ -n "$_sid" ] && SLOTS="$(slots_for_sid "$_sid")"
  case "$msg" in
    *[Pp]ermission*|*[Aa]pprov*|*confirmation*)
      state="awaiting-command"; REASON="notify: permission/approval prompt: $msg" ;;   # -> red (wins over bg)
    *)
      # If the MAIN session is mid-turn (busy/executing), this notification is a
      # teammate ping ("finished", "idle", mail) — NOT your turn. The last teammate
      # finishing used to slip through the bg check below and paint green over a
      # still-working lead; when the lead is truly waiting, Stop has already set the
      # state, so skipping here loses nothing.
      _cur=""
      [ -n "${KITTY_WINDOW_ID:-}" ] && _cur="$(cat "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null)"
      case "$_cur" in thinking|working|executing)
        audit_tx "$_cur" "" 0 "notify: main mid-turn, teammate ping ignored: $msg"; exit 0 ;; esac
      if bg_command_running; then
        state="awaiting-bg"; REASON="notify: bg/teammates still running: $msg"
        ensure_bgwatch                        # teammates/bg still running -> blue, not green
      elif [ "$_cur" = "awaiting-bg" ]; then
        # The tab was blue (awaiting the team) and a bg job just finished, firing this
        # notification. In an agent team the main session is re-invoked to process the
        # finished teammate's result -> it's TAKING OVER, not your turn. Go magenta
        # (working); the main's next Stop sets green once it truly hands back to you.
        state="working"
        REASON="notify: bg finished, main taking over: $msg"
      else
        state="awaiting-response"             # genuinely your turn -> green
        REASON="notify: your turn: $msg"
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
  AUDIT_SID="$(printf '%s' "$payload" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  agent_id="$(printf '%s' "$payload" | grep -o '"agent_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  [ -n "$agent_id" ] && exit 0                # subagent/teammate inner call -> don't touch the tab
  tool="$(printf '%s' "$payload" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  REASON="pretool: $tool"
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
  _pp="$(cat 2>/dev/null)"
  AUDIT_SID="$(printf '%s' "$_pp" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  agent_id="$(printf '%s' "$_pp" | grep -o '"agent_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed -E 's/.*"([^"]*)"$/\1/')"
  [ -n "$agent_id" ] && exit 0                # subagent/teammate inner call -> don't touch the tab
  state="working"
  REASON="posttool: main agent between tools"
fi

# Must be inside kitty with socket remote control available, else no-op silently.
# (Audited so the audit trail shows hooks fired even where the tab can't be set.)
if [ -z "${KITTY_WINDOW_ID:-}" ] || [ -z "${KITTY_LISTEN_ON:-}" ]; then
  audit_tx "" "$state" 0 "skipped: not inside kitty / no remote-control socket"
  exit 0
fi

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
  *)              [ "$state" = "$prev_state" ] && { audit_tx "$prev_state" "$state" 0 "skipped: colour already shown"; exit 0; } ;;
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
audit_tx "$prev_state" "$state" 1 "$REASON"
if [ -n "${KITTY_WINDOW_ID:-}" ]; then
  case "$state" in
    idle|thinking|working|executing|awaiting-bg|awaiting-command|awaiting-response)
      printf '%s' "$state" > "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null ;;
    clear|reset|"")
      rm -f "/tmp/claude-tab-state-${KITTY_WINDOW_ID}" 2>/dev/null ;;
  esac
fi
exit 0
