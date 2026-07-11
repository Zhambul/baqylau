# plugins/claude_code/dispatch.py — the single per-event hook dispatcher.
# Entry point: claude-hook.py (a thin shim — the entry FILENAME is load-bearing;
# ~/.claude/settings.json wires EVERY Claude Code hook event to it).
#
# Before this module, each event listed several separate command entries in
# settings.json — the tab-colour dispatch, a matcher-gated formatter, and the
# always-on async audit subscriber — so Claude Code spawned one python process per
# concern per event. This dispatcher collapses that to ONE entry per event: it
# reads the payload once and fans out IN-PROCESS to whichever subsystems the event
# needs, then records the universal audit-subscriber row itself.
#
# Behaviour is preserved exactly (README § Wiring):
#   - matcher routing (Bash / Read|Edit|… / Monitor / Task|Agent) moves from
#     settings.json matchers into _plan() below — same gating, same tools.
#   - each subsystem still writes its OWN audit rows under its ENTRY filename:
#     the dispatcher stamps A.set_handler("claude-<x>.py") around each call so
#     hook_events.handler / errors.script never collapse to "claude-hook.py"
#     (the argv[0] the whole dispatcher actually runs under).
#   - claude-cmd-pre.py's PreToolUse stdout (the `updatedInput` Bash rewrite) is
#     emitted by its own print(), which lands on THIS process's stdout — the one
#     Claude Code reads. No other subsystem writes stdout, so there is no clash.
#   - the async universal audit subscriber (was `claude_audit.py hook subscriber`)
#     becomes an in-process A.hook_event(handler="subscriber") at the end — audit
#     writes never block and spool on a locked DB (same property tabstatus relies
#     on for its in-process transitions), so this stays off the failure path.
#
# Each subsystem crashing is isolated the same way separate processes isolated it:
# every step runs through hookkit.run() (audit-then-swallow), so one failing step
# never blocks the others or the turn.
import json
import re
import sys

from core import audit as A
from plugins.claude_code import adopt
from plugins.claude_code import hookkit as H
from plugins.claude_code import tabstatus
from plugins.claude_code import split
from plugins.claude_code import subagent_fmt
from plugins.claude_code import cmd_pre
from plugins.claude_code import cmd_fmt
from plugins.claude_code import file_fmt
from plugins.claude_code import monitor_fmt
from plugins.claude_code import stop_fmt
from plugins.claude_code import task_fmt


def _match(tool, pattern):
    return re.fullmatch(pattern, tool or "") is not None


def _plan(ev, tool, d):
    """The routing table: (entry-filename, thunk) steps for this event, in order.
    Mirrors the old settings.json wiring one-for-one — see README § Wiring."""
    steps = []

    def tab(state):
        steps.append(("claude-tab-status.py", lambda: tabstatus.dispatch(state, d)))

    def fmt(name, mod):
        steps.append((name, mod.main))

    if ev == "SessionStart":
        tab("idle")
        steps.append(("claude-split.py", lambda: split.handle("open", d)))
    elif ev == "UserPromptSubmit":
        tab("thinking")
    elif ev == "PreToolUse":
        tab("pretool")                       # matcher .* in the old wiring
        if _match(tool, "Task|Agent"):
            steps.append(("claude-subagent-fmt.py",
                          lambda: subagent_fmt.run_phase("push")))
        if tool == "Bash":
            fmt("claude-cmd-pre.py", cmd_pre)
    elif ev in ("PostToolUse", "PostToolUseFailure"):
        tab("posttool")                      # matcher .* in the old wiring
        if tool == "Bash":
            fmt("claude-cmd-fmt.py", cmd_fmt)
        elif _match(tool, "Read|Edit|Write|MultiEdit|NotebookEdit"):
            fmt("claude-file-fmt.py", file_fmt)
        elif tool == "Monitor":
            fmt("claude-monitor-fmt.py", monitor_fmt)
    elif ev == "Notification":
        tab("notify")
    elif ev in ("Stop", "StopFailure"):
        tab("stop")
        fmt("claude-stop-fmt.py", stop_fmt)
    elif ev == "SessionEnd":
        tab("clear")
        # Fold any final-turn tail the last Stop MISSED: Stop fires at each turn
        # boundary, but the closing assistant line can be flushed to the transcript
        # a beat AFTER the Stop hook reads it (observed: last Stop folded to txpos
        # short of EOF, leaving the final reply's cache-read cost unbooked). By
        # SessionEnd the transcript is fully flushed. Idempotent via the txpos cursor
        # (a no-op when Stop already reached EOF), and it runs as an ORDERED step
        # BEFORE the close/park below — the two are no longer separate racing hook
        # processes, so the old "SessionEnd fold races split.py's park" objection
        # (see stop_fmt.py header) is moot.
        fmt("claude-stop-fmt.py", stop_fmt)
        steps.append(("claude-split.py", lambda: split.handle("close", d)))
    elif ev == "SubagentStart":
        steps.append(("claude-subagent-fmt.py", lambda: subagent_fmt.run_phase("start")))
    elif ev == "SubagentStop":
        steps.append(("claude-subagent-fmt.py", lambda: subagent_fmt.run_phase("stop")))
    elif ev in ("TaskCreated", "TaskCompleted"):
        fmt("claude-task-fmt.py", task_fmt)
    elif ev == "PreCompact":
        # Compaction is Claude busy with no tool/reply signal of its own — paint the
        # busy magenta so the tab doesn't sit stale (grey/green) through it. Use
        # WORKING, not THINKING: no interrupt-watch to start (this isn't a turn
        # boundary), just the colour. The next turn's hooks repaint from there.
        tab("working")
    # Every other event (Setup, PermissionRequest, …) has no functional
    # handler — it only ever fed the universal audit subscriber, which route() still
    # records below.
    return steps


def _step(name, fn):
    """Run one subsystem in-process under its entry-filename identity, swallowing
    (and auditing) anything it leaks — exactly hookkit.run()'s contract, which is
    what the separate-process wiring gave each handler."""
    A.set_handler(name)
    try:
        H.run(fn)
    finally:
        A.set_handler(None)


def route(d):
    ev = d.get("hook_event_name") or ""
    tool = d.get("tool_name") or ""
    H.set_payload(d)                         # every formatter reads this, not stdin
    try:
        # Resume-fork adoption runs FIRST: if this event belongs to a sid that
        # forked off a resumed session (SessionStart fired under the OLD sid —
        # see adopt.py), the predecessor's state DB / pane tags must be adopted
        # BEFORE any subsystem below keys off the new sid, or the formatters
        # write into a fresh DB nothing renders. Runs under the dispatcher's
        # own identity (claude-hook.py) — it is dispatch plumbing, not a
        # subsystem of its own.
        _step("claude-hook.py", lambda: adopt.on_event(d))
        for name, fn in _plan(ev, tool, d):
            _step(name, fn)
    finally:
        H.clear_payload()
    # The universal subscriber row: records EVERY event's full payload (handler=
    # "subscriber"), alongside each functional handler's own decision row — the
    # two-row model claude_audit's queries already expect (handler != 'subscriber').
    try:
        A.hook_event(d, handler="subscriber")
    except Exception:
        pass


def entry():
    try:
        d = json.loads(sys.stdin.read() or "{}") or {}
    except Exception:
        d = {}
    try:
        route(d)
    except Exception:
        try:
            A.error("", "dispatch")
        except Exception:
            pass
    sys.exit(0)
