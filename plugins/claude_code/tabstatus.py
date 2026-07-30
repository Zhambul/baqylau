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
import sys
import time

from core.paths import BIN  # bin/, where the sibling ENTRY scripts live
# SELF must be the ENTRY script: the detached watchers re-invoke it by
# filename, and argv[0] is what the audit records.
SELF = os.path.join(BIN, "claude-tab-status.py")
import frontends  # noqa: E402  (the terminal adapter — kitty today)
from core import env as EV  # noqa: E402  (numeric env knobs, never raises)
from core.noaudit import load_audit  # noqa: E402  (in-process; every write swallows + spools)

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)
from core import paths as P  # noqa: E402  (the one owner of the mirror-log path format)
from core import spawn as SP  # noqa: E402  (the ONE audited detached-spawn owner)
from core import state as St  # noqa: E402  (pid_alive only — DB reads stay mode=ro via sq())
from plugins.claude_code import hookkit as HK  # noqa: E402  (the injected-payload accessor)
from core import tabpaint  # noqa: E402  (the tool-agnostic tab PAINT engine — core/tabpaint.py)
from core.tabs import (  # noqa: E402  (the core tab vocabulary + tab DB — see core/tabs.py)
    IDLE, THINKING, WORKING, EXECUTING, AWAITING_BG, AWAITING_COMMAND,
    AWAITING_RESPONSE, sq, tab_get,
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
# grace 4s, escape-recheck 0.25s). Unset (the shipped default) leaves each sleep
# its literal value —
# written as `time.sleep(WATCH_POLL_S or <literal>)` so the defaults stay
# greppable at their use sites.
WATCH_POLL_S = EV.env_float("CLAUDE_WATCH_POLL_S", 0)

# Watcher WALL-CLOCK ceilings, in seconds. The loop counts are derived at run
# time from ceiling / actual poll interval, so tuning either the ceiling or the
# cadence keeps the give-up wall time (and the audited reason strings, rendered
# via _dur_label) honest instead of silently scaling.
BGWATCH_MAX_S = 3600        # bg-watch gives up after ~1h of live markers
INTERRUPT_MAX_S = 1800      # interrupt-watch gives up after ~30m without a cancel
ESCAPE_GRACE_S = 2.0        # escape-recheck: how long a web Esc into magenta may
#                             stay signal-less before the turn is declared dead
#                             (the transcript line, when one is coming, lands
#                             well inside this — interrupt-watch flips within
#                             one 0.5s tick and the state change bails us out)
# bg-watch green-flip grace: the team must stay quiet across this many
# consecutive checks (~BG_MISS_GRACE_N * poll seconds) before declaring green,
# so a teammate's inter-task marker gap doesn't flip the tab early.
BG_MISS_GRACE_N = 4


def _dur_label(sec):
    """Whole-unit duration for audit reason strings ("1h", "30m", "45s") —
    deliberately NOT ops.fmt_dur, whose "1h00m" would change the historical
    reason vocabulary byte-for-byte."""
    if sec % 3600 == 0:
        return f"{int(sec // 3600)}h"
    if sec % 60 == 0:
        return f"{int(sec // 60)}m"
    return f"{sec:g}s"

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
MLOG = ""                 # this session's mirror-log KEY (state DB derives from it)
#
# There is deliberately NO `REASON` global. Why the resolved state was chosen is
# a handler's RESULT — it belongs to the one transition row main() writes — so a
# handler returns `(state, reason)` and main() takes it as a value. As a global
# it was an out-of-band return channel that four handlers wrote, one function
# read, and dispatch() (the in-process entry, called on hook events) did NOT
# save/restore alongside the payload it does — so a second dispatch in one
# process could attribute a stale reason to a fresh transition. AUDIT_SID/MLOG
# stay module state on purpose: they are this invocation's session IDENTITY,
# which audit_tx() and the watcher loops read long after the handler returned.


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
        log = P.mirror_log()                # cwd-slug fallback, same as hookkit.log_path
    if not log:
        return False
    # sq() (fresh open per poll), NOT tabs.sqc(): this is the per-session STATE
    # DB, whose file-absence is the session-alive signal (state.parked) — a
    # cached conn would keep answering from a parked/deleted DB. Only the
    # fixed-path tab DB reads (tab_get/watcher_pid) use the cached reader.
    for pid in sq(P.state_db(log), "SELECT pid FROM live WHERE pid IS NOT NULL "
                                   "AND kind IN ('bg','monitor','fg','sub.pid')"):
        if _alive(pid):
            return True                     # a live tailer -> a job/agent is running
    return False


def log_for_sid(sid):
    """Mirror-log KEY for a given session key (an ALREADY-sanitised session_id or
    cwd slug — appended verbatim), matching hookkit.log_path so it points at
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
    fallback-resolved (_ensure_win) rather than inherited from the env.
    Spawn + failure are AUDITED (core.spawn — A.spawn/A.error rows): the
    watcher IS the recovery lattice, so a spawn that silently failed was
    indistinguishable from one never requested — the exact non-firing-
    invisible class the recovery watchers exist to close."""
    try:
        fe, win = _fe(), _win()
        fe.export_env()   # stamp terminal-reach env (kitty: KITTY_LISTEN_ON);
        env = dict(os.environ)  # no-op on the inert stub — no frontend attrs read
        if win:
            env["KITTY_WINDOW_ID"] = str(win)
        p = SP.spawn_detached(SELF, args, MLOG, env=env,
                              purpose="watcher:" + str(args[0]))
        if p:
            watcher_set(kind, win, p.pid)
    except Exception:
        # spawn_detached never raises — this covers frontend/env assembly.
        A.error(MLOG, "_spawn_watcher " + kind, {"args": [str(a) for a in args]})


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
    RECORD to the session transcript the instant that happens (confirmed
    empirically, same as the subagent-cancel case) — this watcher tails the
    transcript for it (see is_interrupt_line: a record match, never a byte scan
    — growth that merely QUOTES the marker is not a cancel), for the whole turn,
    and flips green within ~0.5s.

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


def _ensure_win(args=()):
    """Resolve WIN when KITTY_WINDOW_ID is absent (a daemon-origin session's
    hook processes carry a scrubbed env — same sessions whose sid can fork on
    resume, see adopt.py): the pane split.py tagged claude_session=<sid> at
    SessionStart (or adopt retagged) IS this session's window. Must run before
    resolve() — the dispatch handlers themselves consult WIN (d_notify's
    mid-turn check, d_stop's bg path, the watchers' tab_get loops).

    `args` is the dispatch's argv words, handed down from the entry like every
    other consumer of them — there is no sys.argv read below entry()."""
    global WIN
    if _win() or not _fe().usable():
        return
    sid = ""
    if DISPATCH in ("bg-recheck", "bg-watch", "agent-start", "escape-recheck"):
        sid = sid_from_key(args[0] if args else "")
    elif DISPATCH == "interrupt-watch":
        t = args[0] if args else ""
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
    global MLOG, AUDIT_SID
    MLOG = mlog
    AUDIT_SID = sid_from_key(MLOG)
    if not _win():
        return None
    watch_id = A.stream_start(AUDIT_SID, "bg-watch", src_path=MLOG)
    reason = "killed-or-crashed"
    try:
        misses = 0
        poll = WATCH_POLL_S or 2
        for _ in range(max(1, int(BGWATCH_MAX_S / poll))):
            time.sleep(poll)
            if tab_get(_win()) != AWAITING_BG:
                reason = "state-moved-on"
                audit_tx("", "", 0, "bg-watch: state moved on, watcher exiting")
                return None
            if bg_command_running():
                misses = 0                  # something running -> reset
            else:
                # GRACE: a teammate working in bursts drops its marker between
                # tasks. Require the team to stay quiet across BG_MISS_GRACE_N
                # checks before declaring green, so an inter-task gap doesn't flip
                # the tab green while the team is still going.
                misses += 1
                if misses >= BG_MISS_GRACE_N:
                    break
        else:
            reason = f"gave-up-after-{_dur_label(BGWATCH_MAX_S)} (markers still live)"
            return None
        reason = "cleared-to-green"
        return AWAITING_RESPONSE, (
            f"bg-watch: no live markers across "
            f"~{_dur_label(BG_MISS_GRACE_N * poll)} of checks")
    finally:
        watcher_del("bgwatch", _win())
        try:
            A.stream_end(watch_id, reason)
        except Exception:
            pass


INTERRUPT_MARK = b"[Request interrupted by user"      # bytes: the raw-line prefilter
INTERRUPT_MARK_S = INTERRUPT_MARK.decode()           # str: the parsed content check


def is_interrupt_line(line):
    """True when ONE transcript line IS Claude Code's synthetic cancel record.

    The marker must be the CONTENT of a `type:"user"` record — either the bare
    string or a text block, `[Request interrupted by user]` and the tool-call
    variant `[… for tool use]` alike (both are real cancels; only the first was
    ever detected, because the old scan required the closing bracket).

    A bare substring scan of the transcript bytes CANNOT be used: any growth
    that merely QUOTES the marker matched, and the tab flipped green mid-turn.
    Live case (session 2e9b57e4, three times in one session): a `nested_memory`
    attachment injecting a worktree's CLAUDE.md — this repo's own CLAUDE.md
    documents the marker — so every mid-turn memory load read as a cancel. Same
    class: a Read of THIS file, a grep hit, an audit-CLI paste landing as a
    `tool_result`. Those are quotes, not cancels, and must not flip the tab."""
    if INTERRUPT_MARK not in line:
        return False
    try:
        rec = json.loads(line)
    except Exception:
        return False        # torn/unparsable line — never a decidable record
    if not isinstance(rec, dict) or rec.get("type") != "user":
        return False        # `attachment`, `assistant`, metadata records
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content.strip().startswith(INTERRUPT_MARK_S)
    if isinstance(content, list):
        # A `tool_result` block never counts — only the model's own text block.
        return any(isinstance(b, dict) and b.get("type") == "text"
                   and str(b.get("text") or "").strip().startswith(INTERRUPT_MARK_S)
                   for b in content)
    return False


def _interrupt_mark(chunk, pos):
    """Scan one growth chunk for the cancel record: returns (mark, next_pos) —
    `mark` the ABSOLUTE offset of the interrupt LINE's start (or -1), `next_pos`
    how far the chunk was consumed. Only COMPLETE lines are decidable, so a torn
    tail is deliberately left unconsumed and re-read whole on the next tick (the
    old byte scan needed no line framing; a JSON parse needs the whole record)."""
    lines = chunk.split(b"\n")
    off = pos
    for ln in lines[:-1]:
        if is_interrupt_line(ln):
            return off, off
        off += len(ln) + 1
    return -1, pos + len(chunk) - len(lines[-1])


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
    global AUDIT_SID
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
        poll = WATCH_POLL_S or 0.5
        # Green/idle/cleared end the watch ONLY once a mid-turn state has been
        # seen THIS run. The watcher is spawned by d_thinking BEFORE main()
        # paints THINKING, and the tab row is written only on an APPLIED paint
        # (rc==0) — so if that paint fails (transient socket error) or lags
        # past the first 0.5s tick, the row still holds the PREVIOUS turn's
        # green and an ungated check exited "turn-over" at once. A later paint
        # this turn then succeeded (magenta/blue), and a cancel after it had no
        # recovery at all: stuck magenta until the next interaction. (Writing
        # the row before/regardless of the paint isn't an option — persisting
        # failed paints stranded colours, the dedup bug main() documents.)
        # Cost of the gate: a turn whose paints ALL fail keeps the watcher
        # alive until the interrupt-max ceiling — harmless, and the next
        # prompt's ensure_interruptwatch reuses it via the live pid lock.
        seen_midturn = stale_audited = False
        for _ in range(max(1, int(INTERRUPT_MAX_S / poll))):
            time.sleep(poll)
            # Keep watching through the WHOLE turn (magenta/blue/red are all
            # mid-turn). Exiting the moment the state left thinking/working
            # meant the first Bash/Task pretool (-> executing) killed the
            # watcher, and a cancel later in the same turn — e.g. Esc while the
            # model writes its long post-command reply — had no recovery at all
            # (the fg tailer only covers a cancel while its command runs):
            # stuck magenta until the next interaction. Only green/idle/cleared
            # mean the turn is over and there is nothing left to recover.
            cur = tab_get(_win())
            if cur in (AWAITING_RESPONSE, IDLE, ""):
                if seen_midturn:
                    reason = "turn-over"    # green/idle/cleared -> nothing to do
                    return None
                # Pre-turn stale row (the THINKING paint failed or hasn't
                # landed yet) — keep watching; audited once so a lagging paint
                # leaves evidence.
                if cur and not stale_audited:
                    stale_audited = True
                    audit_tx(cur, "", 0,
                             "interrupt-watch: stale pre-turn row — paint "
                             "failed/lagged, keep watching")
            else:
                seen_midturn = True
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
                mark, pos = _interrupt_mark(chunk, pos)
                if mark >= 0:
                    # A QUEUED message changes what this interrupt MEANS:
                    # Claude Code interrupts the turn and immediately
                    # delivers the queued prompt — a NEW turn starts thinking
                    # right away, repaints magenta within our tick, and a
                    # green flip paints "done" over it (stuck green through
                    # the whole think; only the first tool event corrected it
                    # — reported live from the web stop button). The tell is
                    # in the transcript: a plain cancel leaves the interrupt
                    # line LAST, a queued delivery appends the user-prompt
                    # record right after it. One settle tick before deciding,
                    # so a near-simultaneous delivery isn't misread as a
                    # plain cancel — and on the queued case KEEP WATCHING:
                    # the delivered turn is mid-flight and deserves the same
                    # cancel recovery as any other.
                    time.sleep(poll)
                    try:
                        with open(transcript, "rb") as f:
                            f.seek(mark)
                            after = f.read()
                    except OSError:
                        after = b""
                    if any(b'"type":"user"' in ln
                           for ln in after.split(b"\n")[1:]):
                        audit_tx(tab_get(_win()), "", 0,
                                 "interrupt-watch: queued prompt delivered on "
                                 "the interrupt — the new turn owns the tab")
                        pos = mark + len(after)
                        continue
                    break
                # else: _interrupt_mark already advanced pos past the complete
                # lines it decided (a torn tail stays for the next tick).
        else:
            reason = f"no-interrupt-within-{_dur_label(INTERRUPT_MAX_S)}"
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
        return AWAITING_RESPONSE, (
            "interrupt-watch: [Request interrupted by user] in transcript")
    finally:
        watcher_del("interruptwatch", _win())
        try:
            A.stream_end(watch_id, reason)
        except Exception:
            pass


# --- dispatch -> resolved state ---------------------------------------------------
# One handler per dispatch mode, wired in the DISPATCHES table at the bottom (was a
# single 215-line if-ladder). Each takes the dispatch's extra argv words as `args`
# (parsed ONCE by the caller — a handler never reads sys.argv) and returns the
# literal state to paint, `(state, reason)` when it has something to say about WHY,
# or None for 'no change / exit silently' (all bail paths audit themselves first).
# MLOG / AUDIT_SID stay module state — this invocation's session IDENTITY, which
# audit_tx() and the watcher loops read after the handler has returned.

def d_stop(args=()):
    """Stop: it's your turn (green) — unless a background command/monitor Claude
    launched is still running, in which case Claude is awaiting that job, not you,
    so show blue (awaiting-bg). Red is reserved for Claude asking you a question
    (the notify dispatch), never for the turn merely ending."""
    global MLOG, AUDIT_SID
    p = read_payload()
    # A Stop with an agent_id is an AGENT's stop, never the lead's -> ignore,
    # same as pretool/posttool. agent_type is NOT such a signal: a main session
    # whose whole thread runs a custom agent (settings `agent` / --agent, e.g.
    # a "task-manager" orchestrator tab) carries agent_type on its own genuine
    # turn-end Stops — filtering on it left that tab permanently stuck on
    # magenta (confirmed live).
    if tabpaint.agent_inner_event(p):   # main-tab doctrine, one owner (core/tabpaint)
        audit_tx("", "", 0, "ignored: agent stop, not the lead's")
        return None
    AUDIT_SID = (p.get("session_id") or "").strip()
    if AUDIT_SID:
        MLOG = log_for_sid(AUDIT_SID)
    if bg_command_running():
        # A background command / monitor is still running — Claude is awaiting
        # it (not waiting on you), shown BLUE (same as a running foreground
        # command), via a distinct state name so the recheck/watch can target it.
        reason = f"stop: live tailer row(s) in {MLOG}.state.db"
        # There's no "background finished" hook, and the per-job bg-recheck only
        # fires from that job's claude-stream.py tailer — so an UNTRACKED job
        # (tailer died, or a job with none) finishing would leave the tab stuck
        # blue. The detached watcher polls until no bg job remains, then flips
        # this stale blue green.
        ensure_bgwatch()
        return AWAITING_BG, reason
    if re.search(r'"status"\s*:\s*"running"', json.dumps(p)):
        # No live tailer marker, but the Stop payload's own background_tasks list
        # says a teammate/background task is still RUNNING. Markers are burst-
        # scoped — a teammate idling between tasks has released its streamer —
        # so the payload is the more truthful signal here: Claude is awaiting
        # the team, not you. Stay blue.
        ensure_bgwatch()
        return AWAITING_BG, "stop: payload background_tasks reports status=running"
    return AWAITING_RESPONSE, "stop: nothing running"


def d_agent_start(args=()):
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
    global MLOG, AUDIT_SID
    MLOG = args[0] if args else ""
    AUDIT_SID = sid_from_key(MLOG)
    cur = tab_get(_win()) if _win() else ""
    if cur == AWAITING_COMMAND:
        audit_tx(cur, "", 0,
                 "agent-start: red (awaiting-command) wins — user's answer still needed")
        return None
    ensure_bgwatch()
    return AWAITING_BG, "agent-start: main session now awaiting a subagent/teammate"


def d_bg_watch(args=()):
    return run_bgwatch(args[0] if args else "")


def d_interrupt_watch(args=()):
    return run_interruptwatch(args[0] if args else "")


def d_bg_recheck(args=()):
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
    global MLOG, AUDIT_SID
    MLOG = args[0] if args else ""                    # this session's log key
    kind = args[1] if len(args) > 1 else ""           # fg / bg / monitor / sub
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
    reason = f"bg-recheck({kind}): no live markers remain"
    # A finishing SUBAGENT/TEAMMATE (kind=sub) does NOT mean it's your turn:
    # Claude Code re-invokes the main session to process the teammate's result
    # the instant it completes, so the main is about to TAKE OVER, not hand back
    # to you. Painting green here produced a visible green flash before the
    # main's own hooks (or its next Stop) repainted magenta. Go straight to
    # WORKING (magenta) so the tab reflects the main resuming; its subsequent
    # Stop sets green once that follow-up turn genuinely ends. Untracked shell
    # jobs (fg/bg/monitor) don't re-invoke the main, so those still go green.
    return (WORKING if kind == "sub" else AWAITING_RESPONSE), reason


def d_escape_recheck(args=()):
    """escape-recheck (spawned by the web dashboard after a successful
    /interrupt Escape into a MAGENTA tab): recovery for the mid-thinking
    cancel gap interrupt-watch documents as deliberately unhandled — an Esc
    before the model has produced anything leaves no hook, no transcript
    line, nothing, so magenta sticks until the next interaction. For a
    TERMINAL Esc that stays unhandled (no signal exists at all, and the
    banned idle-timeout backstop false-positived on every long think because
    thinking and cancel look identical from outside). A WEB interrupt is
    different in kind: the press itself is an EVENT we generated — we KNOW an
    Escape reached a busy tab, where the TUI's meaning of Esc is
    turn-interrupt — so a short recheck keyed to that press honours the
    "events, never idle timeouts" rule. Wait ESCAPE_GRACE_S; if the tab is
    still on the SAME magenta state, the turn is dead — flip green. ANY state
    movement in the gap means a real signal handled it (the transcript line →
    interrupt-watch's green, a hook repaint, Stop) → bail. Magenta only:
    blue has writer-liveness/bg-recheck, red's dialog outcomes fire their own
    events, and any cancel that DID write the transcript line is
    interrupt-watch's.

    The state poll alone is NOT enough: a NEW prompt submitted within the
    grace window repaints the same magenta invisibly (main()'s dedup skips
    the identical colour and tab rows carry no ts), so a user who interrupts
    and immediately re-prompts would get green painted over a live think.
    That's why the recheck ALSO watches the TRANSCRIPT (`args[1]`) from the
    PRESS-TIME baseline (`args[2]`, stat'd by the dashboard right before the
    send_key, so not even the spawn-latency sub-second is blind) — but only
    for `"type":"user"` RECORDS, not raw growth: a new prompt lands as a
    user record the moment it's submitted, and an interrupt that had
    partial output lands the "[Request interrupted by user]" line (also a
    user record) — while the double-Esc CANCEL-EDIT gesture appends pure
    METADATA (`ai-title`, `last-prompt`) right after killing the turn, and
    a raw-growth bail false-positived on exactly that (observed live: the
    first recheck bailed on the edit-restore's own records and the tab sat
    magenta until a second gesture's recheck flipped it). User record ⇒ a
    real signal owns the tab ⇒ bail; metadata-only growth or total silence
    ⇒ the turn is dead ⇒ flip."""
    global MLOG, AUDIT_SID
    MLOG = args[0] if args else ""                    # this session's log key
    transcript = args[1] if len(args) > 1 else ""
    AUDIT_SID = sid_from_key(MLOG)
    start = tab_get(_win()) if _win() else ""
    if start not in (THINKING, WORKING):
        audit_tx(start, "", 0,
                 "escape-recheck: tab not on magenta — other recoveries own it")
        return None

    def tsize():
        try:
            return os.path.getsize(transcript) if transcript else 0
        except OSError:
            return 0

    try:
        pos = int(args[2])                  # press-time baseline from the dashboard
    except (IndexError, ValueError):
        pos = tsize()                       # fallback: our own start is close enough
    poll = WATCH_POLL_S or 0.25
    for _ in range(max(1, int(ESCAPE_GRACE_S / poll))):
        time.sleep(poll)
        cur = tab_get(_win()) if _win() else ""
        if cur != start:
            audit_tx(cur, "", 0,
                     "escape-recheck: state moved on — a real signal handled it")
            return None
        size = tsize()
        if size > pos:
            try:
                with open(transcript, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
            except OSError:
                chunk = b""
            end = chunk.rfind(b"\n")        # complete lines only — never scan
            if end >= 0:                    # a torn record (read-exactly rule)
                if b'"type":"user"' in chunk[:end]:
                    audit_tx(cur, "", 0, "escape-recheck: user record appeared "
                             "— a new prompt or the interrupt line landed, "
                             "real signals own it")
                    return None
                pos += end + 1              # metadata-only growth: keep waiting
    return AWAITING_RESPONSE, (
        f"escape-recheck: web Esc into {start} left no turn-over signal "
        f"for {_dur_label(ESCAPE_GRACE_S)} — mid-thinking cancel gap")


def d_thinking(args=()):
    """UserPromptSubmit: besides the literal colour (handled by the paint table
    at the bottom, as before), starts this turn's interrupt-watch — see its
    dispatch above — so a cancel with no Bash/subagent tool involved still
    clears the tab promptly."""
    global AUDIT_SID
    p = read_payload()
    AUDIT_SID = (p.get("session_id") or "").strip()
    ensure_interruptwatch(p.get("transcript_path") or "")
    return THINKING, "prompt submitted"


def d_notify(args=()):
    """Notification: Claude wants your attention. If it's asking you for a
    DECISION (a permission / tool-approval prompt), that's awaiting-command
    (red). Otherwise it's just "waiting for your input" — your turn — which is
    awaiting-response (green)... UNLESS a background job / teammate is still
    running, in which case Claude is awaiting THEM, not you, so it must stay
    blue (awaiting-bg). In an agent team, teammate messages / idle pings fire
    notifications constantly, and treating those as "your turn" was what turned
    the tab green while teammates were clearly still working."""
    global MLOG, AUDIT_SID
    p = read_payload()
    msg = str(p.get("message") or "")
    AUDIT_SID = (p.get("session_id") or "").strip()
    if AUDIT_SID:
        MLOG = log_for_sid(AUDIT_SID)
    if re.search(r"[Pp]ermission|[Aa]pprov|confirmation", msg):
        # -> red (wins over bg)
        return AWAITING_COMMAND, f"notify: permission/approval prompt: {msg}"
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
        ensure_bgwatch()                # teammates/bg still running -> blue, not green
        return AWAITING_BG, f"notify: bg/teammates still running: {msg}"
    if cur == AWAITING_BG:
        # The tab was blue (awaiting the team) and a bg job just finished,
        # firing this notification. In an agent team the main session is
        # re-invoked to process the finished teammate's result -> it's TAKING
        # OVER, not your turn. Go magenta (working); the main's next Stop sets
        # green once it truly hands back to you.
        return WORKING, f"notify: bg finished, main taking over: {msg}"
    # genuinely your turn -> green
    return AWAITING_RESPONSE, f"notify: your turn: {msg}"


def d_pretool(args=()):
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
    global AUDIT_SID
    p = read_payload()
    AUDIT_SID = (p.get("session_id") or "").strip()
    if tabpaint.agent_inner_event(p):   # main-tab doctrine, one owner (core/tabpaint)
        return None                     # subagent/teammate inner call -> don't touch the tab
    tool = p.get("tool_name") or ""
    reason = f"pretool: {tool}"
    if tool in ("AskUserQuestion", "ExitPlanMode"):
        return AWAITING_COMMAND, reason   # Claude is asking YOU -> red
    if tool in ("Bash", "Task", "Agent"):
        return EXECUTING, reason          # shell command / awaiting an agent -> blue
    return WORKING, reason                # other tool -> magenta (busy)


def d_posttool(args=()):
    """PostToolUse / PostToolUseFailure: after a tool finishes. An event with an
    agent_id is a SUBAGENT's / TEAMMATE's own tool finishing -> IGNORE it (the
    tab tracks the main session only). Otherwise it's the main agent between
    tools -> WORKING (magenta)."""
    global AUDIT_SID
    p = read_payload()
    AUDIT_SID = (p.get("session_id") or "").strip()
    if tabpaint.agent_inner_event(p):   # main-tab doctrine, one owner (core/tabpaint)
        return None                     # subagent/teammate inner call -> don't touch the tab
    return WORKING, "posttool: main agent between tools"


DISPATCHES = {
    "stop":            d_stop,
    "agent-start":     d_agent_start,
    "bg-watch":        d_bg_watch,
    "interrupt-watch": d_interrupt_watch,
    "bg-recheck":      d_bg_recheck,
    "escape-recheck":  d_escape_recheck,
    THINKING:        d_thinking,
    "notify":          d_notify,
    "pretool":         d_pretool,
    "posttool":        d_posttool,
}


def resolve(state, args=()):
    """Map a dispatch mode to `(literal state to paint, reason)` — see
    DISPATCHES. `args` is the dispatch's extra argv words, parsed ONCE by the
    caller (entry()/dispatch()) and passed in; a handler never reads sys.argv,
    which is what made half of this table untestable without patching argv and
    made its uniform zero-arg signature a lie.

    A handler may return None (no change), a bare state, or `(state, reason)`;
    the tuple is normalised here so a handler with nothing to say stays a
    one-liner."""
    handler = DISPATCHES.get(state)
    if handler:
        got = handler(args)
        return got if isinstance(got, tuple) else (got, "")
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
    return state, ""                        # already a literal state (or clear/reset)


# --- painting -----------------------------------------------------------------
# The PAINT ENGINE — the dedup against the persisted tab row, the frontend
# set_tab_color/clear_tab_color call, the persist-only-when-rc==0 rule, and the
# tab_transitions audit row on every applied/skipped/failed path — is tool-
# AGNOSTIC and lives in core/tabpaint.py, so a second producer (standalone codex,
# a future hookless polled producer) reuses it instead of reimplementing the
# rc==0 rule. THIS module is the Claude-Code producer: it resolves the dispatch
# (resolve()/DISPATCHES/the d_* handlers) and the window (_ensure_win), then hands
# the resolved (state, reason) to the engine.
#
# The frontend paints active bg/fg + inactive (dimmed) bg for THIS window's tab —
# the inactive background is a darkened variant of the same hue so the focused tab
# still stands out; see frontends.kitty.set_tab_color for the audit-the-real-rc
# rationale, and core/tabs.COLORS (state -> hex) for the paint contract.


def main(state, args=()):
    """Resolve `state` (a dispatch mode or a literal) and paint the tab.

    `args` is the dispatch's extra argv words — the log key, the transcript
    path, the recheck kind. They are parsed ONCE at the entry (entry() from
    sys.argv, dispatch() from its caller) and handed down, so no handler reads
    argv and every one of them is callable from a test with a plain tuple.

    The Claude-specific work is resolve() + _ensure_win(); the tool-agnostic
    dedup/persist/audit paint is core/tabpaint.paint(), handed our resolved
    window + audit identity (AUDIT_SID/DISPATCH)."""
    _ensure_win(args)                        # daemon-origin env has no KITTY_WINDOW_ID
    state, reason = resolve(state, args)
    if state is None:
        return
    tabpaint.paint(_fe(), _win(), state, reason, sid=AUDIT_SID, dispatch=DISPATCH)


def dispatch(state, payload, args=()):
    """In-process entry for the single per-event dispatcher (dispatch.py): paint
    the tab for `state` (idle/thinking/pretool/posttool/notify/stop/clear) against
    the dispatcher-injected payload, instead of reading argv[1] + stdin. The
    detached watcher sub-dispatches (bg-watch / interrupt-watch / bg-recheck /
    agent-start) still re-invoke the shim by filename with argv, so they keep the
    entry() argv path — which is why `args` exists here too: the dispatch words
    reach main() as a value on BOTH paths."""
    global DISPATCH
    DISPATCH = state                       # DISPATCH labels the tab_transitions row
    prev = HK.injected()                   # under dispatch.py route() this is `payload`
    HK.set_payload(payload)                # already, but a direct caller needs the inject
    try:
        main(state, args)
    finally:
        HK.set_payload(prev)


def entry():
    # The standalone-shim argv contract: claude-tab-status.py <state> [words…].
    # Parsed HERE (not at import) so importing this module reads no argv — and
    # parsed ONCE: the dispatch words used to be re-read out of sys.argv[2:]
    # inside five handlers, which made the DISPATCHES table's uniform signature
    # a lie and every one of those handlers untestable without patching argv.
    global DISPATCH
    DISPATCH = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        main(DISPATCH, tuple(sys.argv[2:]))
    except Exception:
        try:
            A.error(AUDIT_SID or MLOG, "main")   # audit the swallow, then stay silent
        except Exception:
            pass
    sys.exit(0)
