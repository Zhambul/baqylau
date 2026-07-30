# plugins/codex/tabstatus.py — the codex TAB PRODUCER.
#
# The SECOND producer over the shared, tool-agnostic core/tabpaint engine
# (docs/tab-colors.md — plugins/claude_code/tabstatus.py is the reference
# producer). A tab producer contributes only its own {event -> (state, reason)}
# DECISION plus a window resolver and calls tabpaint.paint(); the dedup +
# persist-only-on-rc==0 + tab_transitions audit come from the engine, so this
# module never re-implements the rc==0 rule.
#
# Codex has NO SessionEnd hook and NO cancel/interrupt hook (the same doctrine
# as "Claude fires nothing on cancel"): a turn interrupted at the terminal writes
# a `turn_aborted` RECORD to the rollout and fires NO Stop, so the tab would sit
# magenta/blue forever. run_interrupt_watch is the recovery — a detached tailer
# that flips the stale busy colour green when it sees that record (unless a new
# turn STEERS off it, the queue+Esc case), the codex twin of claude's
# interrupt-watch. It is re-invoked detached through bin/claude-codex-hook.py's
# own argv path (`claude-codex-hook.py interrupt-watch <rollout> <sid> <win>`),
# so the entry FILENAME stays the audit vocabulary.
#
# NESTED vs STANDALONE is decided ONCE at SessionStart and recorded in the tab
# DB (core/tabs.codex_host_*); the DISPATCHER reads that, not this module — a
# nested codex-inside-Claude run never reaches a paint here.
import glob
import os
import time
from datetime import datetime, timedelta

from core.paths import BIN  # bin/, where the sibling ENTRY scripts live
SELF = os.path.join(BIN, "claude-codex-hook.py")

import frontends  # noqa: E402  (the terminal adapter — plugins may import frontends)
from core import env as EV  # noqa: E402  (numeric env knobs, never raises)
from core import paths as P  # noqa: E402
from core import spawn as SP  # noqa: E402  (the ONE audited detached-spawn owner)
from core import state as St  # noqa: E402  (parked/pid_alive probes)
from core import tabpaint  # noqa: E402  (the tool-agnostic tab PAINT engine)
from core import tabs  # noqa: E402  (the tab vocabulary + tab DB helpers)
from core.noaudit import load_audit  # noqa: E402
from core.tabs import (  # noqa: E402
    IDLE, THINKING, WORKING, EXECUTING, AWAITING_COMMAND, AWAITING_RESPONSE)
from core.tail import stream_lifecycle  # noqa: E402
from plugins.codex import rollout  # noqa: E402  (record parsing — same plugin)

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)

# The tab-transitions `dispatch` label per codex event — a raw-arg label like
# claude's (pretool/stop/…) but codex-prefixed so the tab-left-on-busy /
# nested-guard anomalies and the audit-debug playbook can grep `dispatch LIKE
# 'codex%'`. An unlisted event falls back to a derived label.
EVENT_LABEL = {
    "UserPromptSubmit":   "codex-thinking",
    "PreToolUse":         "codex-pretool",
    "PostToolUse":        "codex-posttool",
    "PostToolUseFailure": "codex-posttool",
    "PermissionRequest":  "codex-permission",
    "PreCompact":         "codex-compact",
    "Stop":               "codex-stop",
    "SubagentStart":      "codex-subagent",
    "SubagentStop":       "codex-subagent",
}

# codex PreToolUse tool_names that run a SHELL/PATCH (tab -> blue "executing",
# same colour intent as claude's Bash). codex sends CLAUDE-COMPATIBLE hook
# payloads, so its shell tool arrives as `Bash` (verified live 0.144.1 — the
# PreToolUse `tool_name` is "Bash", not "exec_command") and a Task as `Task`/
# `Agent`; this mirrors claude's own Bash/Task/Agent -> executing mapping. The
# codex-native spellings are kept too (they drift across versions: shell ->
# exec_command; apply_patch on function_call then a custom tool — see
# rollout.py), so a version that sends the raw name still goes blue. An unlisted
# tool falls through to WORKING (magenta busy) — a cosmetic miss, never a stuck
# tab.
EXEC_TOOLS = frozenset({
    "Bash", "Task", "Agent",                        # claude-compatible names codex emits
    "exec_command", "shell", "local_shell", "unified_exec",
    "apply_patch", "container.exec",
})
# codex's question tool (plan-mode in practice) — codex is asking YOU -> red.
# `AskUserQuestion` is the claude-compatible name a codex payload may carry for
# the same intent (mirrors claude's own ask tools).
ASK_TOOLS = frozenset({"request_user_input", "AskUserQuestion"})


def resolve(event, payload):
    """Map a codex hook event -> (state, reason). state is None for a deliberate
    NO-OP (PostCompact — let the next event repaint; any unrecognised event).

    Duplicate/out-of-order hooks are fine: every mapping is a pure function of
    THIS event, and tabpaint dedups an identical colour, so re-deriving the state
    defensively per event can only re-assert what the tab already shows."""
    tool = payload.get("tool_name") or ""
    if event == "UserPromptSubmit":
        return THINKING, "codex: prompt submitted"
    if event == "PreToolUse":
        if tool in ASK_TOOLS:
            return AWAITING_COMMAND, "codex pretool: %s — asking you" % tool
        if tool in EXEC_TOOLS:
            return EXECUTING, "codex pretool: %s (shell/patch)" % tool
        return WORKING, "codex pretool: %s" % (tool or "tool")
    if event in ("PostToolUse", "PostToolUseFailure"):
        return WORKING, "codex posttool: between tools"
    if event == "PermissionRequest":
        return AWAITING_COMMAND, "codex: permission request — asking you"
    if event == "PreCompact":
        return WORKING, "codex: compacting"
    if event == "Stop":
        # Codex Stop fires PER TURN (even headless) — it's your turn (green).
        return AWAITING_RESPONSE, "codex stop: turn ended"
    if event in ("SubagentStart", "SubagentStop"):
        # Codex blocks on its subagents (no bg-watch analog needed in v1).
        return WORKING, "codex: %s (awaiting a codex subagent)" % event
    # PostCompact + anything unrecognised: no tab change.
    return None, "codex: no tab change for %s" % (event or "?")


def handle(fe, event, payload, sid, win):
    """Resolve `event` to a tab state, paint it through the engine, and — on a
    TURN START (UserPromptSubmit) — arm this turn's interrupt-recovery watcher.
    Returns the audit `decision` string for the dispatcher's hook_event row."""
    if event == "UserPromptSubmit":
        ensure_interrupt_watch(fe, sid, win, _rollout_path(payload, sid))
    state, reason = resolve(event, payload)
    if state is None:
        return reason
    label = EVENT_LABEL.get(event, "codex-" + (event or "event").lower())
    tabpaint.paint(fe, win, state, reason, sid=sid, dispatch=label)
    return "%s -> %s" % (label, state)


# --- interrupt recovery: a turn_aborted with no Stop -----------------------------

INTERRUPT_MAX_S = 1800      # give up after ~30m without a turn_aborted
# Test-suite cadence override, exactly like tabstatus.WATCH_POLL_S.
WATCH_POLL_S = EV.env_float("CLAUDE_WATCH_POLL_S", 0)
ABORT_MARK = b"turn_aborted"    # cheap byte prefilter before the per-line parse


def _rollout_path(payload, sid):
    """This session's rollout path: the hook's transcript_path when it IS a codex
    rollout, else a bounded glob by uuid==sid. "" when not resolvable yet (the
    watcher then no-ops; a plain turn end still flips green via Stop)."""
    tp = payload.get("transcript_path") or ""
    if tp and rollout.owns(tp):
        return tp
    return find_rollout(sid)


def find_rollout(sid):
    """Locate `rollout-*-<sid>.jsonl` under ~/.codex/sessions for today/yesterday
    — the same bounded scan the standalone watcher uses (uuid == session id).
    "" when not found."""
    if not sid:
        return ""
    base = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
    for dd in (datetime.now(), datetime.now() - timedelta(days=1)):
        d = os.path.join(base, "%04d" % dd.year, "%02d" % dd.month, "%02d" % dd.day)
        cand = glob.glob(os.path.join(d, "rollout-*-%s.jsonl" % sid))
        if cand:
            return cand[0]
    return ""


def ensure_interrupt_watch(fe, sid, win, rollout_path):
    """Spawn ONE detached interrupt-watch per window (if not already running),
    re-invoking bin/claude-codex-hook.py's own argv path. No-op without a window
    or a rollout to tail. Spawn + failure are AUDITED (core.spawn)."""
    if not (win and rollout_path):
        return
    wpid = tabs.watcher_pid("codex-interruptwatch", win)
    if wpid and St.pid_alive(wpid):
        return
    try:
        fe.export_env()                       # stamp KITTY_LISTEN_ON for the detached child
        env = dict(os.environ)
        env["KITTY_WINDOW_ID"] = str(win)
        p = SP.spawn_detached(SELF, ["interrupt-watch", rollout_path, sid, str(win)],
                              P.mirror_log(sid), env=env,
                              purpose="watcher:codex-interrupt-watch")
        if p:
            tabs.watcher_set("codex-interruptwatch", win, p.pid)
    except Exception:
        A.error(P.mirror_log(sid), "codex ensure_interrupt_watch",
                {"sid": sid, "win": str(win)})


def _is_abort(line):
    """True when ONE complete rollout line IS a `turn_aborted` RECORD — matched
    through rollout.parse, NEVER as a raw byte scan (the invariant: growth that
    merely QUOTES the marker, e.g. a rollout that echoes this file, is not a
    cancel)."""
    try:
        rec = rollout.parse_line(line.decode("utf-8", "replace"))
    except Exception:
        return False
    return bool(rec) and rec.get("kind") == "turn_aborted"


def _abort_mark(chunk, pos):
    """Scan one growth chunk for the abort record: (abs offset of the abort
    LINE's start | -1, next_pos). Only COMPLETE lines are decidable — a torn
    tail is left unconsumed and re-read whole next tick (a JSON parse needs the
    whole record)."""
    lines = chunk.split(b"\n")
    off = pos
    for ln in lines[:-1]:
        if _is_abort(ln):
            return off, off
        off += len(ln) + 1
    return -1, pos + len(chunk) - len(lines[-1])


def _new_turn_after(after):
    """Does a NEW turn start right after the abort line? codex's queue+Esc STEER
    appends a `task_started` + the queued `user_message` (a `prompt` record) the
    instant the abort lands — the new turn owns the tab, so the watcher must NOT
    flip green. Skips the abort line itself (`[1:]`)."""
    for ln in after.split(b"\n")[1:]:
        if not ln.strip():
            continue
        try:
            rec = rollout.parse_line(ln.decode("utf-8", "replace"))
        except Exception:
            continue
        if rec and rec.get("kind") in ("task_started", "prompt"):
            return True
    return False


def run_interrupt_watch(rollout_path, sid, win):
    """Tail the rollout for a `turn_aborted` record (a terminal interrupt fires
    NO Stop) and flip the stale busy colour green — UNLESS a new turn STEERS off
    it. Registers an audited streams row (a dead watcher = a codex tab stuck
    magenta with no evidence). Runs detached, spawned by ensure_interrupt_watch."""
    fe = frontends.get(resolve=True)
    log = P.mirror_log(sid)
    if not (win and rollout_path):
        return
    poll = WATCH_POLL_S or 0.5
    with stream_lifecycle(
            log, "codex-interrupt-watch", src_path=rollout_path,
            on_exit=lambda: tabs.watcher_del("codex-interruptwatch", win)) as run:
        run.end("killed-or-crashed")
        try:
            pos = os.path.getsize(rollout_path)   # only records appended AFTER the turn started
        except OSError:
            pos = 0
        seen_midturn = False
        deadline = time.time() + INTERRUPT_MAX_S
        while time.time() < deadline:
            time.sleep(poll)
            if St.parked(log):
                run.end("session-parked")
                return
            cur = tabs.tab_get(win)
            if cur in (AWAITING_RESPONSE, IDLE, ""):
                if seen_midturn:
                    run.end("turn-over")       # the turn already resolved (Stop)
                    return
            else:
                seen_midturn = True
            try:
                size = os.path.getsize(rollout_path)
            except OSError:
                run.end("rollout-gone")
                return
            if size <= pos:
                continue
            try:
                with open(rollout_path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read(size - pos)
            except OSError:
                continue
            if ABORT_MARK not in chunk:         # cheap prefilter — no per-line parse
                nl = chunk.rfind(b"\n")
                if nl >= 0:
                    pos += nl + 1               # advance over complete lines
                continue
            mark, pos = _abort_mark(chunk, pos)
            if mark < 0:
                continue                        # marker byte was inside another field
            # A settle tick, so a near-simultaneous STEER (queue+Esc) has landed
            # before we decide the abort was a plain interrupt.
            time.sleep(poll)
            try:
                with open(rollout_path, "rb") as f:
                    f.seek(mark)
                    after = f.read()
            except OSError:
                after = b""
            if _new_turn_after(after):
                A.transition(sid, win, "codex-interrupt", tabs.tab_get(win), "", 0,
                             "codex-interrupt: turn_aborted STEERED — a new turn owns the tab")
                pos = mark + len(after)
                continue                        # the delivered turn deserves the same recovery
            cur = tabs.tab_get(win)
            if cur in (THINKING, WORKING, EXECUTING):
                tabpaint.paint(fe, win, AWAITING_RESPONSE,
                               "codex-interrupt: turn_aborted with no follow-up turn",
                               sid=sid, dispatch="codex-interrupt")
                run.end("interrupt-detected-flipped-green")
                return
            # green/idle/cleared -> already resolved; red -> a dialog owns it.
            run.end("interrupt-seen-but-tab-not-busy")
            return
        run.end("no-interrupt-within-30m")
