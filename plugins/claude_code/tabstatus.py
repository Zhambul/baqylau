# plugins/claude_code/tabstatus.py — colour the terminal tab to reflect
# Claude Code's status. Entry point: claude-tab-status.py (a thin shim — the
# entry FILENAME is the audit vocabulary AND the argv contract streamers use:
# `claude-tab-status.py bg-recheck <log> <kind>` etc).
#
# The tab-state VOCABULARY (state constants, the COLORS hex table, the global
# window-keyed tab DB + watcher pid locks) lives in core/tabs.py — it is
# host-tool-agnostic. This module is the Claude-Code DISPATCH: it maps hook
# payloads (pretool/posttool/notify/stop) and streamer callbacks
# (bg-recheck/bg-watch/agent-start) onto those states, and paints through the
# active Frontend (frontends/).
# Usage: claude-tab-status.py <state>
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

import json
import os
import re
import subprocess
import sys
import time

from core.paths import ROOT  # the repo root, where the sibling ENTRY scripts live
# SELF must be the ENTRY script: the detached watchers re-invoke it by
# filename, and argv[0] is what the audit records.
SELF = os.path.join(ROOT, "claude-tab-status.py")
import frontends  # noqa: E402  (the terminal adapter — kitty today)
from core.noaudit import load_audit  # noqa: E402  (in-process; every write swallows + spools)

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)
from core import paths as P  # noqa: E402  (the one owner of the mirror-log path format)
from core import state as St  # noqa: E402  (pid_alive only — DB reads stay mode=ro via sq())
from plugins.claude_code import hookkit as HK  # noqa: E402  (the injected-payload accessor)
from core.tabs import (  # noqa: E402  (the core tab vocabulary + tab DB — see core/tabs.py)
    IDLE, THINKING, WORKING, EXECUTING, AWAITING_BG, AWAITING_COMMAND,
    AWAITING_RESPONSE, COLORS, sq, tab_get, tab_set, tab_clear,
    watcher_pid, watcher_set, watcher_del)

# resolve=True: a daemon-origin session's hook processes carry a SCRUBBED env
# (no KITTY_LISTEN_ON), same as split.py's keybinding launches — the kitty
# frontend walks the ppid chain / lone-socket fallback only when the env var is
# absent, so the normal interactive path pays nothing.
#
# FE/WIN are resolved LAZILY (memoized on first use) rather than at import:
# dispatch.py imports this module for every hook event, including ones that
# never touch the tab, and eagerly resolving the frontend + current window
# there was per-invocation work paid by everything sharing the process.
# None = not-yet-resolved; a resolved-but-absent window is "" (tests may also
# pre-seed FE/WIN directly, which the accessors honour).
FE = None
WIN = None


def _fe():
    global FE
    if FE is None:
        FE = frontends.get(resolve=True)
    return FE


def _win():
    global WIN
    if WIN is None:
        WIN = _fe().current_window() or ""
    return WIN

# Test-suite-only cadence override (docs/testing.md): one value that replaces
# every watcher/grace sleep below (bg-watch 2s, interrupt-watch 0.5s, bg-recheck
# grace 4s). Unset (the shipped default) leaves each sleep its literal value —
# written as `time.sleep(WATCH_POLL_S or <literal>)` so the defaults stay
# greppable at their use sites.
WATCH_POLL_S = float(os.environ.get("CLAUDE_WATCH_POLL_S") or 0)

# The state decisions below record to the audit DB (see claude_audit.py) as
# tab_transitions rows — applied, skipped, and early bails alike. claude_audit's
# writers never raise and spool on a locked/unreachable DB, so calling them
# in-process can't block or break a hook (the bash predecessor had to spawn a
# detached python for this; in-process is both faster and still safe).

# DISPATCH is the raw dispatch mode/state this invocation runs — set by entry()
# (argv[1], the standalone-shim contract) or dispatch() (the in-process path).
# argv is deliberately NOT read at import: dispatch.py imports this module for
# every hook event, whose argv belongs to claude-hook.py, not this shim.
DISPATCH = ""             # the raw arg, before the dispatch blocks rewrite it
AUDIT_SID = ""            # set by dispatches that learn the session_id
REASON = ""               # why the final state was chosen (set by dispatch blocks)
MLOG = ""                 # this session's mirror-log KEY (state DB derives from it)


def audit_tx(prev, new, applied, reason):
    try:
        A.transition(AUDIT_SID, _win(), DISPATCH, prev, new, applied, reason)
    except Exception:
        pass


def bg_command_running():
    """True if a Claude Code background command / monitor / agent — OR a still-
    running FOREGROUND command (including one Ctrl+B'd into the background, which
    fires no distinct hook of its own) — launched from this project is still being
    streamed. Detection is via the live-tailer rows in the session's STATE DB
    (`live` table, claude_slots):
      kind bg / monitor     — a claude-stream.py tailer for a background command/monitor
      kind fg               — a claude-stream.py tailer for a LIVE-STREAMED FOREGROUND
                              command (claude-cmd-pre.py); it keeps tailing for as long
                              as the command's process is still writing, Ctrl+B or not,
                              so this is what lets bg-watch (and a Ctrl+B conversion)
                              correctly stay blue instead of flipping green underneath
                              a command that's still running
      kind sub.pid          — a claude-substream.py tailer for a background SUBAGENT
    each row holds its tailer's pid and is deleted when the tailer exits, so a row
    with a live pid == that job/agent is still running. (A foreground subagent's
    tailer also has a sub.pid row, but it has already exited by Stop time — the
    main turn blocked on it — so only background agents remain.)

    (Earlier this scanned tasks/<id>.output write-holders via lsof, but FOREGROUND
    commands also hold those files while they run — so an async bg-recheck that
    coincided with a foreground command would mis-count it and refuse to clear the
    colour. Live rows are created only by tailers, never by foreground commands,
    so they can't be fooled.) The state DB is keyed PER SESSION
    (/tmp/claude-mirror-<session_id>.log.state.db), so we must scan THIS session's
    DB, not a cwd-derived one (else a teammate/bg job goes undetected and the tab
    wrongly turns green). MLOG (the mirror-log KEY the DB path derives from) is
    resolved per dispatch from the session_id (stop payload) or passed in
    (bg-watch/bg-recheck); it falls back to the cwd slug to stay correct if a
    session_id is ever unavailable."""
    log = MLOG
    if not log and P.cwd_slug():
        log = P.mirror_log()                # cwd-slug fallback, same as claude_ops
    if not log:
        return False
    for pid in sq(P.state_db(log), "SELECT pid FROM live WHERE pid IS NOT NULL "
                                   "AND kind IN ('bg','monitor','fg','sub.pid')"):
        if _alive(pid):
            return True                     # a live tailer -> a job/agent is running
    return False


def log_for_sid(sid):
    """Mirror-log KEY for a given session key (an ALREADY-sanitised session_id or
    cwd slug — appended verbatim), matching claude_ops.log_path so it points at
    exactly the state DB the tailers write."""
    return P.PREFIX + sid + ".log"


def sid_from_key(key):  # /tmp/claude-mirror-<sid>.log -> <sid>
    return P.sid_from_log(key)


# The global tab DB (per-window shown-state + watcher pid locks) is core
# vocabulary — schema + accessors live in core/tabs.py.
_alive = St.pid_alive               # canonical probe: EPERM (foreign-owned) = alive


def _spawn_watcher(kind, args):
    """Detached self re-invocation (start_new_session so the long-lived watcher
    never sits in the hook's process group, which Claude Code waits to drain).
    The resolved window + socket are passed explicitly: a detached watcher is
    re-parented, so the ppid walk can't find the socket, and WIN may have been
    fallback-resolved (_ensure_win) rather than inherited from the env."""
    try:
        fe, win = _fe(), _win()
        fe.export_env()   # stamp terminal-reach env (kitty: KITTY_LISTEN_ON);
        env = dict(os.environ)  # no-op on the inert stub — no frontend attrs read
        if win:
            env["KITTY_WINDOW_ID"] = str(win)
        p = subprocess.Popen([sys.executable or "python3", SELF] + args,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True,
                             env=env)
        watcher_set(kind, win, p.pid)
    except Exception:
        pass


def ensure_bgwatch():
    """Spawn ONE detached bg-watch for this window (if not already running) that
    polls MLOG's state DB until no background job/agent remains, then flips the
    stale awaiting-bg blue back to green. Shared by stop and agent-start."""
    if not _win():
        return
    wpid = watcher_pid("bgwatch", _win())
    if not (wpid and _alive(wpid)):
        _spawn_watcher("bgwatch", ["bg-watch", MLOG])   # pass this session's log key


def ensure_interruptwatch(transcript):
    """Spawn ONE detached interrupt-watch per window (if not already running): the
    recovery for a cancel at any point in the turn where no marker/pid of its own
    exists to liveness-check — a plain text reply, an Edit/Read/MCP tool call, a
    permission prompt, or the stretch AFTER a command finished, killed mid-flight,
    leaves the tab stuck on magenta/red otherwise. (A cancel while a command RUNS
    is covered faster by the fg tailer's writer-liveness; the watcher defers to it
    on blue.) Claude Code appends a synthetic "[Request interrupted by user]"
    line to the session transcript the instant that happens (confirmed empirically,
    same as the subagent-cancel case) — this watcher tails the transcript for that
    line, for the whole turn, and flips green within ~0.5s.

    KNOWN GAP (deliberate): cancelling BEFORE the model has produced anything at all
    (mid-thinking) leaves no trace anywhere — no hook, no transcript line, nothing
    (confirmed empirically) — so the tab stays magenta until the next interaction
    resets it. A timeout backstop for that case (idle-watch, "fully quiet for N secs
    -> green") was removed: long thinking fires zero hooks and writes nothing, which
    is EXACTLY the same signature as the cancel, so any timeout short enough to be
    useful false-positived on every long thinking stretch (tab lied "done" mid-turn).
    The stale magenta after a mid-thinking cancel is rarer and self-corrects at the
    next prompt, which the cancelling user is typically about to type anyway."""
    if not (_win() and transcript):
        return
    wpid = watcher_pid("interruptwatch", _win())
    if not (wpid and _alive(wpid)):
        _spawn_watcher("interruptwatch", ["interrupt-watch", transcript])


def read_payload():
    """The hook's stdin JSON; {} on anything unparsable (a hook must never fail).
    The inject/parse/cache mechanics live in hookkit.payload_or_stdin(): when
    the single per-event dispatcher (dispatch.py) drives this in-process it has
    already consumed stdin and injected the parsed payload (dispatch() below
    re-injects for direct callers), and the standalone-shim stdin parse is
    cached — a second caller must not re-read a drained stdin and get {}."""
    return HK.payload_or_stdin()


def _ensure_win():
    """Resolve WIN when KITTY_WINDOW_ID is absent (a daemon-origin session's
    hook processes carry a scrubbed env — same sessions whose sid can fork on
    resume, see adopt.py): the pane split.py tagged claude_session=<sid> at
    SessionStart (or adopt retagged) IS this session's window. Must run before
    resolve() — the dispatch handlers themselves consult WIN (d_notify's
    mid-turn check, d_stop's bg path, the watchers' tab_get loops)."""
    global WIN
    if _win() or not _fe().usable():
        return
    sid = ""
    if DISPATCH in ("bg-recheck", "bg-watch", "agent-start"):
        sid = sid_from_key(sys.argv[2] if len(sys.argv) > 2 else "")
    elif DISPATCH == "interrupt-watch":
        t = sys.argv[2] if len(sys.argv) > 2 else ""
        sid = os.path.basename(t)[:-len(".jsonl")] if t.endswith(".jsonl") else ""
    else:
        try:
            if HK.has_payload():
                sid = (read_payload().get("session_id") or "").strip()
        except Exception:
            sid = ""
    if sid:
        WIN = _fe().window_for_session(sid) or ""


# --- long-lived watcher dispatches (each runs detached, spawned above) -----------

def run_bgwatch(mlog):
    """bg-watch: poll until no background job remains — or the state is no longer
    the bg-running blue (a new turn started) — then return the stale blue's
    replacement state. Registers its lifetime in the audit `streams` table — a
    bg-watch that dies mid-poll is exactly the "tab stuck blue forever" bug, and
    without a stream row its death was invisible. SIGKILL leaves the row open,
    which the `streams that never ended` anomaly then flags."""
    global MLOG, AUDIT_SID, REASON
    MLOG = mlog
    AUDIT_SID = sid_from_key(MLOG)
    if not _win():
        return None
    watch_id = A.stream_start(AUDIT_SID, "bg-watch", src_path=MLOG)
    reason = "killed-or-crashed"
    try:
        misses = 0
        for _ in range(1800):
            time.sleep(WATCH_POLL_S or 2)
            if tab_get(_win()) != AWAITING_BG:
                reason = "state-moved-on"
                audit_tx("", "", 0, "bg-watch: state moved on, watcher exiting")
                return None
            if bg_command_running():
                misses = 0                  # something running -> reset
            else:
                # GRACE: a teammate working in bursts drops its marker between
                # tasks. Require the team to stay quiet across several checks
                # (~8s) before declaring green, so an inter-task gap doesn't flip
                # the tab green while the team is still going.
                misses += 1
                if misses >= 4:
                    break
        else:
            reason = "gave-up-after-1h (markers still live)"
            return None
        reason = "cleared-to-green"
        REASON = "bg-watch: no live markers across ~8s of checks"
        return AWAITING_RESPONSE
    finally:
        watcher_del("bgwatch", _win())
        try:
            A.stream_end(watch_id, reason)
        except Exception:
            pass


def run_interruptwatch(transcript):
    """interrupt-watch: recovery for a cancel anywhere in the turn that leaves no
    other signal. Live commands/agents have their own fast self-heal (writer-
    liveness / meta.json polling) via a marker/pid this watcher doesn't need — so
    it defers to those on blue — but a plain text reply, an Edit/Read/MCP call, a
    permission prompt, or the reply written AFTER a command finished has neither,
    and killed mid-flight would otherwise sit on magenta/red until the next
    interaction. Tails the transcript for the synthetic "[Request interrupted by
    user]" line Claude Code appends the instant a cancel happens, for the whole
    turn (exits on green/idle/cleared), and flips green within one ~0.5s tick."""
    global AUDIT_SID, REASON
    if not (_win() and transcript):
        return None
    # The transcript filename IS the session id (~/.claude/projects/<slug>/<sid>.jsonl).
    AUDIT_SID = os.path.basename(transcript)[:-len(".jsonl")] \
        if transcript.endswith(".jsonl") else os.path.basename(transcript)
    # Same lifecycle registration as bg-watch: a dead interrupt-watch means a
    # cancelled turn leaves the tab stuck magenta with no evidence of why.
    watch_id = A.stream_start(AUDIT_SID, "interrupt-watch", src_path=transcript)
    reason = "killed-or-crashed"
    try:
        try:
            pos = os.path.getsize(transcript)
        except OSError:
            pos = 0
        for _ in range(3600):
            time.sleep(WATCH_POLL_S or 0.5)
            # Keep watching through the WHOLE turn (magenta/blue/red are all
            # mid-turn). Exiting the moment the state left thinking/working
            # meant the first Bash/Task pretool (-> executing) killed the
            # watcher, and a cancel later in the same turn — e.g. Esc while the
            # model writes its long post-command reply — had no recovery at all
            # (the fg tailer only covers a cancel while its command runs):
            # stuck magenta until the next interaction. Only green/idle/cleared
            # mean the turn is over and there is nothing left to recover.
            if tab_get(_win()) in (AWAITING_RESPONSE, IDLE, ""):
                reason = "turn-over"        # green/idle/cleared -> nothing to do
                return None
            try:
                size = os.path.getsize(transcript)
            except OSError:
                size = pos
            if size > pos:
                try:
                    with open(transcript, "rb") as f:
                        f.seek(pos)
                        chunk = f.read(size - pos)
                except OSError:
                    chunk = b""
                if b"[Request interrupted by user]" in chunk:
                    break
                pos = size
        else:
            reason = "no-interrupt-within-30m"
            return None
        cur = tab_get(_win())
        if cur in (AWAITING_RESPONSE, IDLE, ""):
            # re-check: the turn already resolved on its own right now
            reason = "interrupt-seen-but-turn-already-over"
            audit_tx(cur, "", 0, "interrupt-watch: interrupt seen but turn already over")
            return None
        if cur in (EXECUTING, AWAITING_BG):
            # blue: the cancelled command/agent has its own faster recovery
            # (writer-liveness -> bg-recheck / bg-watch); flipping green here
            # would race it and could paint "done" over a still-live bg job.
            reason = "interrupt-seen-deferred-to-bg-recheck"
            audit_tx(cur, "", 0,
                     "interrupt-watch: interrupt seen on blue — writer-liveness self-heals")
            return None
        # magenta (thinking/working) or red (awaiting-command): no other signal
        # covers a cancel here -> flip green.
        reason = "interrupt-detected-flipped-green"
        REASON = "interrupt-watch: [Request interrupted by user] in transcript"
        return AWAITING_RESPONSE
    finally:
        watcher_del("interruptwatch", _win())
        try:
            A.stream_end(watch_id, reason)
        except Exception:
            pass


# --- dispatch -> resolved state ---------------------------------------------------
# One handler per dispatch mode, wired in the DISPATCHES table at the bottom (was a
# single 215-line if-ladder). Each returns the literal state to paint, or None for
# 'no change / exit silently' (all bail paths audit themselves first). Handlers set
# the module globals MLOG / AUDIT_SID / REASON that main()'s paint + audit path uses.

def d_stop():
    """Stop: it's your turn (green) — unless a background command/monitor Claude
    launched is still running, in which case Claude is awaiting that job, not you,
    so show blue (awaiting-bg). Red is reserved for Claude asking you a question
    (the notify dispatch), never for the turn merely ending."""
    global MLOG, AUDIT_SID, REASON
    p = read_payload()
    # A Stop with an agent_id is an AGENT's stop, never the lead's -> ignore,
    # same as pretool/posttool. agent_type is NOT such a signal: a main session
    # whose whole thread runs a custom agent (settings `agent` / --agent, e.g.
    # a "task-manager" orchestrator tab) carries agent_type on its own genuine
    # turn-end Stops — filtering on it left that tab permanently stuck on
    # magenta (confirmed live).
    if p.get("agent_id"):
        audit_tx("", "", 0, "ignored: agent stop, not the lead's")
        return None
    AUDIT_SID = (p.get("session_id") or "").strip()
    if AUDIT_SID:
        MLOG = log_for_sid(AUDIT_SID)
    if bg_command_running():
        # A background command / monitor is still running — Claude is awaiting
        # it (not waiting on you), shown BLUE (same as a running foreground
        # command), via a distinct state name so the recheck/watch can target it.
        REASON = f"stop: live tailer row(s) in {MLOG}.state.db"
        # There's no "background finished" hook, and the per-job bg-recheck only
        # fires from that job's claude-stream.py tailer — so an UNTRACKED job
        # (tailer died, or a job with none) finishing would leave the tab stuck
        # blue. The detached watcher polls until no bg job remains, then flips
        # this stale blue green.
        ensure_bgwatch()
        return AWAITING_BG
    if re.search(r'"status"\s*:\s*"running"', json.dumps(p)):
        # No live tailer marker, but the Stop payload's own background_tasks list
        # says a teammate/background task is still RUNNING. Markers are burst-
        # scoped — a teammate idling between tasks has released its streamer —
        # so the payload is the more truthful signal here: Claude is awaiting
        # the team, not you. Stay blue.
        REASON = "stop: payload background_tasks reports status=running"
        ensure_bgwatch()
        return AWAITING_BG
    REASON = "stop: nothing running"
    return AWAITING_RESPONSE


def d_agent_start():
    """agent-start (called by claude-subagent-fmt.py when a background TEAMMATE
    begins a task): the main session is now awaiting that teammate, so the tab
    goes BLUE — even if the lead's turn had already ended (green). Without this,
    a teammate starting a new task between the lead's turns would leave the tab
    stuck green while the teammate works (SubagentStart otherwise never touches
    the tab). Also ensures the watcher so the blue clears once the team quiets.

    EXCEPTION: red (awaiting-command) wins. Red means Claude is blocked on YOUR
    answer (permission prompt / AskUserQuestion) — a teammate starting its next
    task in the background must not erase the one visual cue that you're needed
    (d_notify makes red win over its bg check for the same reason). No watcher is
    needed while red: answering the prompt resumes the normal state flow."""
    global MLOG, AUDIT_SID, REASON
    MLOG = sys.argv[2] if len(sys.argv) > 2 else ""
    AUDIT_SID = sid_from_key(MLOG)
    cur = tab_get(_win()) if _win() else ""
    if cur == AWAITING_COMMAND:
        audit_tx(cur, "", 0,
                 "agent-start: red (awaiting-command) wins — user's answer still needed")
        return None
    REASON = "agent-start: main session now awaiting a subagent/teammate"
    ensure_bgwatch()
    return AWAITING_BG


def d_bg_watch():
    return run_bgwatch(sys.argv[2] if len(sys.argv) > 2 else "")


def d_interrupt_watch():
    return run_interruptwatch(sys.argv[2] if len(sys.argv) > 2 else "")


def d_bg_recheck():
    """bg-recheck (called by claude-stream.py when a background job/monitor/live
    foreground stream finishes): there's no "background finished" hook, so the
    bg-running blue would linger until the next exchange. Flip that *stale*
    colour to green — but ONLY if the tab is currently awaiting-bg OR executing
    (so we never override working/idle/awaiting-command) and nothing else is
    still running.

    executing matters for a MANUALLY CANCELLED foreground command: cancelling one
    fires NO hook at all (the same no-hook-on-interrupt gap noted above), so
    EXECUTING would otherwise stick until the next interaction. But the fg
    tailer (claude-cmd-pre.py) DOES notice its process died (has_writer goes
    false) and calls bg-recheck right then — a fast, reliable signal for exactly
    this case, so we honour it here too."""
    global MLOG, AUDIT_SID, REASON
    MLOG = sys.argv[2] if len(sys.argv) > 2 else ""   # this session's log key
    kind = sys.argv[3] if len(sys.argv) > 3 else ""   # fg / bg / monitor / sub
    AUDIT_SID = sid_from_key(MLOG)
    cur = tab_get(_win()) if _win() else ""
    # Clearing EXECUTING exists SOLELY for the cancelled-foreground-command
    # case, where the caller is that command's own fg tailer noticing its writer
    # died. Any OTHER tailer (a finishing teammate/subagent/bg job) calling in
    # while the tab shows executing means the MAIN session is running its own
    # command — flipping that green painted "done" over a still-working lead.
    # Only fg may clear it.
    if cur == EXECUTING and kind != "fg":
        audit_tx(cur, "", 0, f"bg-recheck({kind}): only fg may clear executing")
        return None
    if cur not in (AWAITING_BG, EXECUTING):
        audit_tx(cur, "", 0, f"bg-recheck({kind}): tab not on a bg-running colour")
        return None
    if bg_command_running():
        audit_tx(cur, "", 0, f"bg-recheck({kind}): another job still running")
        return None
    # GRACE: a teammate finishing one task usually starts the next within a
    # second or two. Wait briefly and re-check so we don't flip green in that
    # gap; if a new marker appeared (next task started), stay blue. Also bail
    # if the state changed.
    time.sleep(WATCH_POLL_S or 4)
    if bg_command_running():
        audit_tx(cur, "", 0, f"bg-recheck({kind}): a new job started in the grace gap")
        return None
    cur2 = tab_get(_win()) if _win() else ""
    if cur2 not in (AWAITING_BG, EXECUTING) or \
       (cur2 == EXECUTING and kind != "fg"):
        audit_tx(cur2, "", 0, f"bg-recheck({kind}): state moved on in the gap")
        return None
    REASON = f"bg-recheck({kind}): no live markers remain"
    # A finishing SUBAGENT/TEAMMATE (kind=sub) does NOT mean it's your turn:
    # Claude Code re-invokes the main session to process the teammate's result
    # the instant it completes, so the main is about to TAKE OVER, not hand back
    # to you. Painting green here produced a visible green flash before the
    # main's own hooks (or its next Stop) repainted magenta. Go straight to
    # WORKING (magenta) so the tab reflects the main resuming; its subsequent
    # Stop sets green once that follow-up turn genuinely ends. Untracked shell
    # jobs (fg/bg/monitor) don't re-invoke the main, so those still go green.
    return WORKING if kind == "sub" else AWAITING_RESPONSE


def d_thinking():
    """UserPromptSubmit: besides the literal colour (handled by the paint table
    at the bottom, as before), starts this turn's interrupt-watch — see its
    dispatch above — so a cancel with no Bash/subagent tool involved still
    clears the tab promptly."""
    global AUDIT_SID, REASON
    p = read_payload()
    AUDIT_SID = (p.get("session_id") or "").strip()
    REASON = "prompt submitted"
    ensure_interruptwatch(p.get("transcript_path") or "")
    return THINKING


def d_notify():
    """Notification: Claude wants your attention. If it's asking you for a
    DECISION (a permission / tool-approval prompt), that's awaiting-command
    (red). Otherwise it's just "waiting for your input" — your turn — which is
    awaiting-response (green)... UNLESS a background job / teammate is still
    running, in which case Claude is awaiting THEM, not you, so it must stay
    blue (awaiting-bg). In an agent team, teammate messages / idle pings fire
    notifications constantly, and treating those as "your turn" was what turned
    the tab green while teammates were clearly still working."""
    global MLOG, AUDIT_SID, REASON
    p = read_payload()
    msg = str(p.get("message") or "")
    AUDIT_SID = (p.get("session_id") or "").strip()
    if AUDIT_SID:
        MLOG = log_for_sid(AUDIT_SID)
    if re.search(r"[Pp]ermission|[Aa]pprov|confirmation", msg):
        REASON = f"notify: permission/approval prompt: {msg}"
        return AWAITING_COMMAND       # -> red (wins over bg)
    # If the MAIN session is mid-turn (busy/executing), this notification is a
    # teammate ping ("finished", IDLE, mail) — NOT your turn. The last
    # teammate finishing used to slip through the bg check below and paint
    # green over a still-working lead; when the lead is truly waiting, Stop has
    # already set the state, so skipping here loses nothing.
    cur = tab_get(_win()) if _win() else ""
    if cur in (THINKING, WORKING, EXECUTING):
        audit_tx(cur, "", 0, f"notify: main mid-turn, teammate ping ignored: {msg}")
        return None
    if bg_command_running():
        REASON = f"notify: bg/teammates still running: {msg}"
        ensure_bgwatch()                # teammates/bg still running -> blue, not green
        return AWAITING_BG
    if cur == AWAITING_BG:
        # The tab was blue (awaiting the team) and a bg job just finished,
        # firing this notification. In an agent team the main session is
        # re-invoked to process the finished teammate's result -> it's TAKING
        # OVER, not your turn. Go magenta (working); the main's next Stop sets
        # green once it truly hands back to you.
        REASON = f"notify: bg finished, main taking over: {msg}"
        return WORKING
    REASON = f"notify: your turn: {msg}"
    return AWAITING_RESPONSE          # genuinely your turn -> green


def d_pretool():
    """PreToolUse: the tab tracks the MAIN session ONLY, so an event carrying an
    agent_id (a SUBAGENT's / TEAMMATE's own inner tool call) is IGNORED — it must
    not flip the tab while the main session is doing something else (thinking, or
    handed back to you). The main session still goes blue while it *awaits* an
    agent: a FOREGROUND agent keeps the main turn blocked after its Task/Agent
    pretool below sets blue (so blue persists), and a BACKGROUND agent is picked
    up by the Stop -> awaiting-bg path (a live sub.pid row). For the main
    session's own tools:
      - the Bash tool                   -> a shell command is running -> blue.
      - the Task/Agent tool             -> launching/awaiting an agent -> blue.
      - AskUserQuestion / ExitPlanMode  -> Claude is asking YOU -> red.
      - every other tool (Edit/Read/Write/MCP/...) -> WORKING (magenta)."""
    global AUDIT_SID, REASON
    p = read_payload()
    AUDIT_SID = (p.get("session_id") or "").strip()
    if p.get("agent_id"):
        return None                     # subagent/teammate inner call -> don't touch the tab
    tool = p.get("tool_name") or ""
    REASON = f"pretool: {tool}"
    if tool in ("AskUserQuestion", "ExitPlanMode"):
        return AWAITING_COMMAND       # Claude is asking YOU -> red
    if tool in ("Bash", "Task", "Agent"):
        return EXECUTING              # shell command / awaiting an agent -> blue
    return WORKING                    # other tool -> magenta (busy)


def d_posttool():
    """PostToolUse / PostToolUseFailure: after a tool finishes. An event with an
    agent_id is a SUBAGENT's / TEAMMATE's own tool finishing -> IGNORE it (the
    tab tracks the main session only). Otherwise it's the main agent between
    tools -> WORKING (magenta)."""
    global AUDIT_SID, REASON
    p = read_payload()
    AUDIT_SID = (p.get("session_id") or "").strip()
    if p.get("agent_id"):
        return None                     # subagent/teammate inner call -> don't touch the tab
    REASON = "posttool: main agent between tools"
    return WORKING


DISPATCHES = {
    "stop":            d_stop,
    "agent-start":     d_agent_start,
    "bg-watch":        d_bg_watch,
    "interrupt-watch": d_interrupt_watch,
    "bg-recheck":      d_bg_recheck,
    THINKING:        d_thinking,
    "notify":          d_notify,
    "pretool":         d_pretool,
    "posttool":        d_posttool,
}


def resolve(state):
    """Map a dispatch mode to the literal state to paint (see DISPATCHES)."""
    handler = DISPATCHES.get(state)
    if handler:
        return handler()
    # A literal state (SessionStart's `idle`, SessionEnd's `clear`, the manual
    # smoke cycle): attribute its transition row when a hook payload is present.
    # These rows used to land with session_id="" — which left the SessionEnd
    # clear invisible to per-session audit queries (the busy-colour anomaly
    # flagged sessions whose tab WAS cleared). TTY guard: the manual smoke loop
    # pipes no stdin, and reading a terminal would block it.
    global AUDIT_SID, MLOG
    try:
        if not sys.stdin.isatty():
            sid = (read_payload().get("session_id") or "").strip()
            if sid:
                AUDIT_SID = sid
                MLOG = log_for_sid(P.sanitize_sid(sid))
    except Exception:
        pass
    return state                            # already a literal state (or clear/reset)


# --- painting -----------------------------------------------------------------
# The paint itself goes through the Frontend (frontends/kitty.py today): active
# bg/fg + inactive (dimmed) bg for THIS window's tab — the inactive background
# is a darkened variant of the same hue so the focused tab still stands out
# (otherwise only the bold font-style tells them apart). See
# frontends.kitty.set_tab_color for the audit-the-real-rc rationale.

# COLORS (state -> active_bg/active_fg/inactive_bg hex) is imported from
# core/tabs.py — the paint contract shared by every frontend.


def main(state):
    _ensure_win()                            # daemon-origin env has no KITTY_WINDOW_ID
    state = resolve(state)
    if state is None:
        return

    # Must be inside a controllable terminal (kitty: window id + remote-control
    # socket), else no-op silently. (Audited so the audit trail shows hooks
    # fired even where the tab can't be set.)
    if not _win() or not _fe().available():
        audit_tx("", state, 0, "skipped: not inside kitty / no remote-control socket")
        return

    # Skip the work entirely when the tab is ALREADY showing this state.
    # Tool-heavy turns fire many hooks that all resolve to the same colour (a run
    # of Read/Edit/MCP calls all become WORKING), and re-applying an identical
    # colour is a wasted `kitten @` socket round-trip. The persisted state row
    # (written at the end of every applied change) is our record of what's
    # currently shown: if it matches, there's nothing to do — bail before locating
    # the kitten binary or touching the socket. (clear/reset deletes the row, so
    # an empty prev_state means "already cleared".)
    prev_state = tab_get(_win())
    if state in ("clear", "reset", ""):
        if not prev_state:
            return
    elif state == prev_state:
        audit_tx(prev_state, state, 0, "skipped: colour already shown")
        return

    if not _fe().usable():
        return

    if state in COLORS:
        rc = _fe().set_tab_color(_win(), *COLORS[state])
    elif state in ("clear", "reset", ""):
        rc = _fe().clear_tab_color(_win())
    else:
        return

    # Persist the resolved state (tab DB row) so bg-recheck / bg-watch can tell
    # whether a finishing background job should flip the stale bg-running blue back
    # to green — but ONLY when the paint actually landed (rc == 0). Persisting a
    # failed paint made the DB claim a colour the tab never showed, and the
    # "colour already shown" dedup above then suppressed every retry of that same
    # state: one transient socket error stranded the old colour until a DIFFERENT
    # state came along. Leaving the row unchanged keeps the next same-state event
    # eligible to retry the paint.
    if rc == 0:
        audit_tx(prev_state, state, 1, REASON)
        if state in COLORS:
            tab_set(_win(), state)
        else:
            tab_clear(_win())
    else:
        audit_tx(prev_state, state, 0,
                 (f"{REASON} — " if REASON else "")
                 + f"kitten @ failed rc={rc} — state row unchanged")


def dispatch(state, payload):
    """In-process entry for the single per-event dispatcher (dispatch.py): paint
    the tab for `state` (idle/thinking/pretool/posttool/notify/stop/clear) against
    the dispatcher-injected payload, instead of reading argv[1] + stdin. The
    detached watcher sub-dispatches (bg-watch / interrupt-watch / bg-recheck /
    agent-start) still re-invoke the shim by filename with argv, so they keep the
    entry() argv path."""
    global DISPATCH
    DISPATCH = state                       # DISPATCH labels the tab_transitions row
    prev = HK.injected()                   # under dispatch.py route() this is `payload`
    HK.set_payload(payload)                # already, but a direct caller needs the inject
    try:
        main(state)
    finally:
        HK.set_payload(prev)


def entry():
    # The standalone-shim argv contract: claude-tab-status.py <state> [...].
    # Parsed HERE (not at import) so importing this module reads no argv.
    global DISPATCH
    DISPATCH = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        main(DISPATCH)
    except Exception:
        try:
            A.error(AUDIT_SID or MLOG, "main")   # audit the swallow, then stay silent
        except Exception:
            pass
    sys.exit(0)
