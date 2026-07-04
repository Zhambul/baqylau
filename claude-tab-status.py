#!/usr/bin/env python3
# claude-tab-status.py — color the kitty tab to reflect Claude Code's status.
#
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
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.join(HERE, os.path.basename(__file__))
sys.path.insert(0, HERE)
import claude_audit as A  # noqa: E402  (in-process; every write swallows + spools)
import claude_paths as P  # noqa: E402  (the one owner of the mirror-log path format)
import claude_state as St  # noqa: E402  (pid_alive only — DB reads stay mode=ro via sq())

WIN = os.environ.get("KITTY_WINDOW_ID", "")

# The state decisions below record to the audit DB (see claude_audit.py) as
# tab_transitions rows — applied, skipped, and early bails alike. claude_audit's
# writers never raise and spool on a locked/unreachable DB, so calling them
# in-process can't block or break a hook (the bash predecessor had to spawn a
# detached python for this; in-process is both faster and still safe).

STATE = sys.argv[1] if len(sys.argv) > 1 else ""
DISPATCH = STATE          # the raw arg, before the dispatch blocks rewrite STATE
AUDIT_SID = ""            # set by dispatches that learn the session_id
REASON = ""               # why the final state was chosen (set by dispatch blocks)
MLOG = ""                 # this session's mirror-log KEY (state DB derives from it)


def audit_tx(prev, new, applied, reason):
    try:
        A.transition(AUDIT_SID, WIN, DISPATCH, prev, new, applied, reason)
    except Exception:
        pass


# --- read-only sqlite (never creates a DB whose absence is a liveness signal) ----

def sq(db, sql):
    """Query a DB read-only; first column of every row. Silent on any failure
    (missing db, lock). mode=ro so a probe can never create the state DB — its
    file-existence is the session-alive signal watchers poll."""
    if not db or not os.path.isfile(db):
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)
        try:
            return [r[0] for r in conn.execute(sql).fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


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


# --- global tab DB -------------------------------------------------------------
# The per-window shown-state + the per-window watcher pid locks live in ONE global
# runtime DB. Window-keyed — a kitty window id is unique per kitty instance and
# outlives any one session — so this is deliberately NOT the per-session state DB.
# In /tmp so it self-clears on reboot.
TABDB = "/tmp/claude-kitty-tab.db"
TABDB_SCHEMA = """
CREATE TABLE IF NOT EXISTS tab(win TEXT PRIMARY KEY, state TEXT);
CREATE TABLE IF NOT EXISTS watchers(kind TEXT, win TEXT, pid INTEGER, PRIMARY KEY(kind, win));
"""


def tw(sql, params=()):
    """Write against the tab DB (creates it + schema on first use); silent."""
    try:
        conn = sqlite3.connect(TABDB, timeout=0.2)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(TABDB_SCHEMA)
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def tab_get(win):
    rows = sq(TABDB, f"SELECT state FROM tab WHERE win='{win}'")
    return rows[0] if rows else ""


def tab_set(win, state):
    tw("INSERT INTO tab(win, state) VALUES(?, ?) "
       "ON CONFLICT(win) DO UPDATE SET state=excluded.state", (win, state))


def tab_clear(win):
    tw("DELETE FROM tab WHERE win=?", (win,))


def watcher_pid(kind, win):
    rows = sq(TABDB, f"SELECT pid FROM watchers WHERE kind='{kind}' AND win='{win}'")
    return rows[0] if rows else None


def watcher_set(kind, win, pid):
    tw("INSERT INTO watchers(kind, win, pid) VALUES(?, ?, ?) "
       "ON CONFLICT(kind, win) DO UPDATE SET pid=excluded.pid", (kind, win, pid))


def watcher_del(kind, win):
    tw("DELETE FROM watchers WHERE kind=? AND win=?", (kind, win))


_alive = St.pid_alive               # canonical probe: EPERM (foreign-owned) = alive


def _spawn_watcher(kind, args):
    """Detached self re-invocation (start_new_session so the long-lived watcher
    never sits in the hook's process group, which Claude Code waits to drain)."""
    try:
        p = subprocess.Popen([sys.executable or "python3", SELF] + args,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        watcher_set(kind, WIN, p.pid)
    except Exception:
        pass


def ensure_bgwatch():
    """Spawn ONE detached bg-watch for this window (if not already running) that
    polls MLOG's state DB until no background job/agent remains, then flips the
    stale awaiting-bg blue back to green. Shared by stop and agent-start."""
    if not WIN:
        return
    wpid = watcher_pid("bgwatch", WIN)
    if not (wpid and _alive(wpid)):
        _spawn_watcher("bgwatch", ["bg-watch", MLOG])   # pass this session's log key


def ensure_interruptwatch(transcript):
    """Spawn ONE detached interrupt-watch per window (if not already running): the
    recovery for a cancelled turn that never ran a Bash/subagent tool (so has no
    marker/pid of its own to liveness-check) — a plain text reply or an Edit/Read/MCP
    tool call killed mid-flight leaves the tab stuck on magenta (thinking/working
    merged) otherwise. Claude Code appends a synthetic "[Request interrupted by user]"
    line to the session transcript the instant that happens (confirmed empirically,
    same as the subagent-cancel case) — this watcher tails the transcript for that
    line and flips green within ~0.5s.

    KNOWN GAP (deliberate): cancelling BEFORE the model has produced anything at all
    (mid-thinking) leaves no trace anywhere — no hook, no transcript line, nothing
    (confirmed empirically) — so the tab stays magenta until the next interaction
    resets it. A timeout backstop for that case (idle-watch, "fully quiet for N secs
    -> green") was removed: long thinking fires zero hooks and writes nothing, which
    is EXACTLY the same signature as the cancel, so any timeout short enough to be
    useful false-positived on every long thinking stretch (tab lied "done" mid-turn).
    The stale magenta after a mid-thinking cancel is rarer and self-corrects at the
    next prompt, which the cancelling user is typically about to type anyway."""
    if not (WIN and transcript):
        return
    wpid = watcher_pid("interruptwatch", WIN)
    if not (wpid and _alive(wpid)):
        _spawn_watcher("interruptwatch", ["interrupt-watch", transcript])


def read_payload():
    """The hook's stdin JSON; {} on anything unparsable (a hook must never fail)."""
    try:
        return json.loads(sys.stdin.read() or "{}") or {}
    except Exception:
        return {}


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
    if not WIN:
        return None
    watch_id = A.stream_start(AUDIT_SID, "bg-watch", src_path=MLOG)
    reason = "killed-or-crashed"
    try:
        misses = 0
        for _ in range(1800):
            time.sleep(2)
            if tab_get(WIN) != "awaiting-bg":
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
        return "awaiting-response"
    finally:
        watcher_del("bgwatch", WIN)
        try:
            A.stream_end(watch_id, reason)
        except Exception:
            pass


def run_interruptwatch(transcript):
    """interrupt-watch: recovery for a turn cancelled before any Bash/subagent tool
    ran — those have their own fast self-heal (writer-liveness / meta.json polling)
    via a marker/pid this watcher doesn't need, but a plain text reply or an
    Edit/Read/MCP call killed mid-flight has neither, so it would otherwise sit on
    magenta until the next interaction. Tails the transcript for the synthetic
    "[Request interrupted by user]" line Claude Code appends the instant a cancel
    happens, and flips green within one ~0.5s tick."""
    global AUDIT_SID, REASON
    if not (WIN and transcript):
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
            time.sleep(0.5)
            if tab_get(WIN) not in ("thinking", "working"):
                reason = "state-moved-on"   # moved to blue/red/green -> nothing to do
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
        if tab_get(WIN) not in ("thinking", "working"):
            # re-check: only flip if still stuck busy right now
            reason = "interrupt-seen-but-state-moved-on"
            audit_tx("", "", 0, "interrupt-watch: interrupt seen but state moved on")
            return None
        reason = "interrupt-detected-flipped-green"
        REASON = "interrupt-watch: [Request interrupted by user] in transcript"
        return "awaiting-response"
    finally:
        watcher_del("interruptwatch", WIN)
        try:
            A.stream_end(watch_id, reason)
        except Exception:
            pass


# --- dispatch -> resolved state ---------------------------------------------------

def resolve(state):
    """Map a dispatch mode to the literal state to paint. Returns None for
    'no change / exit silently' (all bail paths audit themselves first)."""
    global MLOG, AUDIT_SID, REASON

    # Stop dispatch: it's your turn (green) — unless a background command/monitor
    # Claude launched is still running, in which case Claude is awaiting that job,
    # not you, so show blue (awaiting-bg). Red is reserved for Claude asking you a
    # question (the notify dispatch), never for the turn merely ending.
    if state == "stop":
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
            return "awaiting-bg"
        if re.search(r'"status"\s*:\s*"running"', json.dumps(p)):
            # No live tailer marker, but the Stop payload's own background_tasks list
            # says a teammate/background task is still RUNNING. Markers are burst-
            # scoped — a teammate idling between tasks has released its streamer —
            # so the payload is the more truthful signal here: Claude is awaiting
            # the team, not you. Stay blue.
            REASON = "stop: payload background_tasks reports status=running"
            ensure_bgwatch()
            return "awaiting-bg"
        REASON = "stop: nothing running"
        return "awaiting-response"

    # agent-start dispatch (called by claude-subagent-fmt.py when a background
    # TEAMMATE begins a task): the main session is now awaiting that teammate, so
    # the tab goes BLUE — even if the lead's turn had already ended (green).
    # Without this, a teammate starting a new task between the lead's turns would
    # leave the tab stuck green while the teammate works (SubagentStart otherwise
    # never touches the tab). We also ensure the watcher is running so the blue
    # clears once the team goes quiet.
    if state == "agent-start":
        MLOG = sys.argv[2] if len(sys.argv) > 2 else ""
        AUDIT_SID = sid_from_key(MLOG)
        REASON = "agent-start: main session now awaiting a subagent/teammate"
        ensure_bgwatch()
        return "awaiting-bg"

    if state == "bg-watch":
        return run_bgwatch(sys.argv[2] if len(sys.argv) > 2 else "")

    if state == "interrupt-watch":
        return run_interruptwatch(sys.argv[2] if len(sys.argv) > 2 else "")

    # bg-recheck dispatch (called by claude-stream.py when a background job/monitor/
    # live foreground stream finishes): there's no "background finished" hook, so
    # the bg-running blue would linger until the next exchange. Here we flip that
    # *stale* colour to green — but ONLY if the tab is currently awaiting-bg OR
    # executing (so we never override working/idle/awaiting-command) and nothing
    # else is still running.
    #
    # executing matters for a MANUALLY CANCELLED foreground command: cancelling one
    # fires NO hook at all (the same no-hook-on-interrupt gap noted above), so
    # "executing" would otherwise stick until the next interaction. But the fg
    # tailer (claude-cmd-pre.py) DOES notice its process died (has_writer goes
    # false) and calls bg-recheck right then — a fast, reliable signal for exactly
    # this case, so we honour it here too.
    if state == "bg-recheck":
        MLOG = sys.argv[2] if len(sys.argv) > 2 else ""   # this session's log key
        kind = sys.argv[3] if len(sys.argv) > 3 else ""   # fg / bg / monitor / sub
        AUDIT_SID = sid_from_key(MLOG)
        cur = tab_get(WIN) if WIN else ""
        # Clearing "executing" exists SOLELY for the cancelled-foreground-command
        # case, where the caller is that command's own fg tailer noticing its writer
        # died. Any OTHER tailer (a finishing teammate/subagent/bg job) calling in
        # while the tab shows executing means the MAIN session is running its own
        # command — flipping that green painted "done" over a still-working lead.
        # Only fg may clear it.
        if cur == "executing" and kind != "fg":
            audit_tx(cur, "", 0, f"bg-recheck({kind}): only fg may clear executing")
            return None
        if cur not in ("awaiting-bg", "executing"):
            audit_tx(cur, "", 0, f"bg-recheck({kind}): tab not on a bg-running colour")
            return None
        if bg_command_running():
            audit_tx(cur, "", 0, f"bg-recheck({kind}): another job still running")
            return None
        # GRACE: a teammate finishing one task usually starts the next within a
        # second or two. Wait briefly and re-check so we don't flip green in that
        # gap; if a new marker appeared (next task started), stay blue. Also bail
        # if the state changed.
        time.sleep(4)
        if bg_command_running():
            audit_tx(cur, "", 0, f"bg-recheck({kind}): a new job started in the grace gap")
            return None
        cur2 = tab_get(WIN) if WIN else ""
        if cur2 not in ("awaiting-bg", "executing") or \
           (cur2 == "executing" and kind != "fg"):
            audit_tx(cur2, "", 0, f"bg-recheck({kind}): state moved on in the gap")
            return None
        REASON = f"bg-recheck({kind}): no live markers remain"
        # A finishing SUBAGENT/TEAMMATE (kind=sub) does NOT mean it's your turn:
        # Claude Code re-invokes the main session to process the teammate's result
        # the instant it completes, so the main is about to TAKE OVER, not hand back
        # to you. Painting green here produced a visible green flash before the
        # main's own hooks (or its next Stop) repainted magenta. Go straight to
        # "working" (magenta) so the tab reflects the main resuming; its subsequent
        # Stop sets green once that follow-up turn genuinely ends. Untracked shell
        # jobs (fg/bg/monitor) don't re-invoke the main, so those still go green.
        return "working" if kind == "sub" else "awaiting-response"

    # UserPromptSubmit dispatch ("thinking"): besides the literal colour (handled
    # by the paint table at the bottom, as before), starts this turn's interrupt-
    # watch — see its dispatch above — so a cancel with no Bash/subagent tool
    # involved still clears the tab promptly.
    if state == "thinking":
        p = read_payload()
        AUDIT_SID = (p.get("session_id") or "").strip()
        REASON = "prompt submitted"
        ensure_interruptwatch(p.get("transcript_path") or "")
        return "thinking"

    # Notification dispatch: the hook pipes its JSON on stdin. A notification means
    # Claude wants your attention. If it's asking you for a DECISION (a permission /
    # tool-approval prompt), that's awaiting-command (red). Otherwise it's just
    # "waiting for your input" — your turn — which is awaiting-response (green)...
    # UNLESS a background job / teammate is still running, in which case Claude is
    # awaiting THEM, not you, so it must stay blue (awaiting-bg). In an agent team,
    # teammate messages / idle pings fire notifications constantly, and treating
    # those as "your turn" was what turned the tab green while teammates were
    # clearly still working.
    if state == "notify":
        p = read_payload()
        msg = str(p.get("message") or "")
        AUDIT_SID = (p.get("session_id") or "").strip()
        if AUDIT_SID:
            MLOG = log_for_sid(AUDIT_SID)
        if re.search(r"[Pp]ermission|[Aa]pprov|confirmation", msg):
            REASON = f"notify: permission/approval prompt: {msg}"
            return "awaiting-command"       # -> red (wins over bg)
        # If the MAIN session is mid-turn (busy/executing), this notification is a
        # teammate ping ("finished", "idle", mail) — NOT your turn. The last
        # teammate finishing used to slip through the bg check below and paint
        # green over a still-working lead; when the lead is truly waiting, Stop has
        # already set the state, so skipping here loses nothing.
        cur = tab_get(WIN) if WIN else ""
        if cur in ("thinking", "working", "executing"):
            audit_tx(cur, "", 0, f"notify: main mid-turn, teammate ping ignored: {msg}")
            return None
        if bg_command_running():
            REASON = f"notify: bg/teammates still running: {msg}"
            ensure_bgwatch()                # teammates/bg still running -> blue, not green
            return "awaiting-bg"
        if cur == "awaiting-bg":
            # The tab was blue (awaiting the team) and a bg job just finished,
            # firing this notification. In an agent team the main session is
            # re-invoked to process the finished teammate's result -> it's TAKING
            # OVER, not your turn. Go magenta (working); the main's next Stop sets
            # green once it truly hands back to you.
            REASON = f"notify: bg finished, main taking over: {msg}"
            return "working"
        REASON = f"notify: your turn: {msg}"
        return "awaiting-response"          # genuinely your turn -> green

    # PreToolUse dispatch: the hook pipes its JSON on stdin. The tab tracks the
    # MAIN session ONLY, so an event carrying an agent_id (a SUBAGENT's /
    # TEAMMATE's own inner tool call) is IGNORED — it must not flip the tab while
    # the main session is doing something else (thinking, or handed back to you).
    # The main session still goes blue while it *awaits* an agent: a FOREGROUND
    # agent keeps the main turn blocked after its Task/Agent pretool below sets
    # blue (so blue persists), and a BACKGROUND agent is picked up by the
    # Stop -> awaiting-bg path (a live sub.pid row). For the main session's own tools:
    #   - the Bash tool                   -> a shell command is running -> blue.
    #   - the Task/Agent tool             -> launching/awaiting an agent -> blue.
    #   - AskUserQuestion / ExitPlanMode  -> Claude is asking YOU -> red.
    #   - every other tool (Edit/Read/Write/MCP/...) -> "working" (magenta).
    if state == "pretool":
        p = read_payload()
        AUDIT_SID = (p.get("session_id") or "").strip()
        if p.get("agent_id"):
            return None                     # subagent/teammate inner call -> don't touch the tab
        tool = p.get("tool_name") or ""
        REASON = f"pretool: {tool}"
        if tool in ("AskUserQuestion", "ExitPlanMode"):
            return "awaiting-command"       # Claude is asking YOU -> red
        if tool in ("Bash", "Task", "Agent"):
            return "executing"              # shell command / awaiting an agent -> blue
        return "working"                    # other tool -> magenta (busy)

    # PostToolUse / PostToolUseFailure dispatch: after a tool finishes. An event
    # with an agent_id is a SUBAGENT's / TEAMMATE's own tool finishing -> IGNORE it
    # (the tab tracks the main session only). Otherwise it's the main agent between
    # tools -> "working" (magenta).
    if state == "posttool":
        p = read_payload()
        AUDIT_SID = (p.get("session_id") or "").strip()
        if p.get("agent_id"):
            return None                     # subagent/teammate inner call -> don't touch the tab
        REASON = "posttool: main agent between tools"
        return "working"

    return state                            # already a literal state (or clear/reset)


# --- painting -----------------------------------------------------------------

def find_kitten():
    """Locate the kitten binary (PATH first, then the macOS app bundle)."""
    k = os.environ.get("KITTY_KITTEN_BIN")
    if k:
        return k
    k = shutil.which("kitten")
    if k:
        return k
    bundle = "/Applications/kitty.app/Contents/MacOS/kitten"
    return bundle if os.access(bundle, os.X_OK) else None


def set_color(kitten, active_bg, active_fg, inactive_bg):
    """active bg/fg + inactive (dimmed) bg. The status hue is shown on BOTH active
    and inactive tabs (so it's visible even when the tab isn't focused), but the
    INACTIVE background is a darkened variant of the same hue so the focused tab
    clearly stands out — otherwise active and inactive tabs share one background
    and only the bold font-style tells them apart. The inactive foreground is a
    fixed light grey that reads on every dim background. The return code records
    whether the `kitten @` call actually succeeded — the output is still discarded
    (a hook must stay silent), but the audit gets the truth: a row claiming a
    colour was applied while the socket call failed is exactly the kind of
    trusted-but-wrong evidence that hides a stuck-colour bug."""
    try:
        return subprocess.run(
            [kitten, "@", "--to", os.environ["KITTY_LISTEN_ON"], "set-tab-color",
             "--match", f"window_id:{WIN}",
             f"active_bg={active_bg}", f"active_fg={active_fg}",
             f"inactive_bg={inactive_bg}", "inactive_fg=#c0c4cc"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    except Exception:
        return 1


COLORS = {
    "idle":              ("#5c6370", "#e6e9ef", "#33373f"),  # grey  — ready, nothing running
    # thinking + working are merged: there's no signal to tell reasoning apart
    # from non-shell tool use / reply-writing, so both are one "busy" colour.
    "thinking":          ("#c678dd", "#1a0620", "#4a2b52"),  # magenta — Claude busy
    "working":           ("#c678dd", "#1a0620", "#4a2b52"),
    # blue — a command is running: a foreground shell command (executing), or a
    # background command / monitor Claude is awaiting (awaiting-bg). Same colour.
    "executing":         ("#61afef", "#06121f", "#2c4a63"),
    "awaiting-bg":       ("#61afef", "#06121f", "#2c4a63"),
    "awaiting-command":  ("#e06c75", "#2a0608", "#5e2d31"),  # red — Claude is asking you
    "awaiting-response": ("#98c379", "#07180a", "#445733"),  # green — done, your turn
}


def main():
    state = resolve(STATE)
    if state is None:
        return

    # Must be inside kitty with socket remote control available, else no-op
    # silently. (Audited so the audit trail shows hooks fired even where the tab
    # can't be set.)
    if not WIN or not os.environ.get("KITTY_LISTEN_ON"):
        audit_tx("", state, 0, "skipped: not inside kitty / no remote-control socket")
        return

    # Skip the work entirely when the tab is ALREADY showing this state.
    # Tool-heavy turns fire many hooks that all resolve to the same colour (a run
    # of Read/Edit/MCP calls all become "working"), and re-applying an identical
    # colour is a wasted `kitten @` socket round-trip. The persisted state row
    # (written at the end of every applied change) is our record of what's
    # currently shown: if it matches, there's nothing to do — bail before locating
    # the kitten binary or touching the socket. (clear/reset deletes the row, so
    # an empty prev_state means "already cleared".)
    prev_state = tab_get(WIN)
    if state in ("clear", "reset", ""):
        if not prev_state:
            return
    elif state == prev_state:
        audit_tx(prev_state, state, 0, "skipped: colour already shown")
        return

    kitten = find_kitten()
    if not kitten:
        return

    if state in COLORS:
        rc = set_color(kitten, *COLORS[state])
    elif state in ("clear", "reset", ""):
        try:
            rc = subprocess.run(
                [kitten, "@", "--to", os.environ["KITTY_LISTEN_ON"], "set-tab-color",
                 "--match", f"window_id:{WIN}",
                 "active_bg=NONE", "active_fg=NONE",
                 "inactive_bg=NONE", "inactive_fg=NONE"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        except Exception:
            rc = 1
    else:
        return

    # Persist the resolved state (tab DB row) so bg-recheck / bg-watch can tell
    # whether a finishing background job should flip the stale bg-running blue back
    # to green. The audit row reflects what kitten actually did: applied=0 + a
    # "kitten @ failed" reason when the socket call errored (dead socket, closed
    # tab, …) — the tab did NOT change colour.
    if rc == 0:
        audit_tx(prev_state, state, 1, REASON)
    else:
        audit_tx(prev_state, state, 0,
                 (f"{REASON} — " if REASON else "") + f"kitten @ failed rc={rc}")
    if state in COLORS:
        tab_set(WIN, state)
    else:
        tab_clear(WIN)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            A.error(AUDIT_SID or MLOG, "main")   # audit the swallow, then stay silent
        except Exception:
            pass
    sys.exit(0)
