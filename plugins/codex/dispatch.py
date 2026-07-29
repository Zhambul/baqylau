# plugins/codex/dispatch.py — the single per-event codex hook DISPATCHER.
# Entry point: claude-codex-hook.py (a thin shim — the entry FILENAME is
# load-bearing; ~/.codex/hooks.json wires codex's NON-SessionStart events to it).
#
# Codex's SessionStart stays on claude-codex-session.py (it stands up the mirror
# pane + records the standalone-host bit); this dispatcher handles the OTHER nine
# codex hook events (UserPromptSubmit / Pre/PostToolUse / PermissionRequest /
# Pre/PostCompact / SubagentStart/Stop / Stop) and drives the codex TAB PRODUCER
# (plugins/codex/tabstatus.py) over the shared core/tabpaint engine.
#
# NESTED GUARD (load-bearing). Those nine events fire for BOTH a standalone codex
# host AND a codex-inside-Claude subagent run (`codex exec` in a Claude session,
# e.g. the codex:rescue skill IN THIS REPO). Only the standalone host may paint
# the kitty tab — a nested run's tab belongs to the Claude host, whose own watcher
# already streams the codex run. plugins/codex/session.py decides standalone vs
# nested ONCE at SessionStart (hostpane.tab_host_sid, a kitten subprocess) and
# records the standalone host + its window in the tab DB (core/tabs.codex_host_*).
# This dispatcher reads that as a cheap sqlite lookup per event and BAILS
# (audited `nested-skip`) when the sid is not a known standalone host — never
# re-running tab_host_sid on every PreToolUse.
#
# The same entry ALSO re-invokes this process DETACHED for the interrupt-recovery
# watcher (`claude-codex-hook.py interrupt-watch <rollout> <sid> <win>`), so the
# frozen basename stays the audit vocabulary for both the hook and the watcher.
import json
import sys

from core.noaudit import load_audit

A = load_audit()   # audit trail (real module, or an inert stub if it can't import)

HANDLER = "claude-codex-hook.py"   # the audit handler vocabulary (entry filename)


def route(payload):
    """Fan a codex hook event out to the tab producer, gated on the standalone
    bit. Every path audits a hook_event with a decision (incl. the nested bail)."""
    from core import tabs
    import frontends
    from plugins.codex import tabstatus as TS
    event = str(payload.get("hook_event_name") or "")
    sid = str(payload.get("session_id") or "")
    # NESTED GATE: only a recorded STANDALONE codex host paints the tab.
    win = tabs.codex_host_win(sid) if sid else None
    if win is None:
        A.hook_event(payload, handler=HANDLER,
                     decision="nested-skip (%s: not a standalone codex host)"
                     % (event or "?"))
        return
    if not win:
        # a known host whose window couldn't be resolved (scrubbed env) — can't
        # paint, but it is NOT nested, so record the distinct outcome.
        A.hook_event(payload, handler=HANDLER,
                     decision="standalone host, no tab window (%s)" % (event or "?"))
        return
    fe = frontends.get(resolve=True)
    if not fe.usable():
        A.hook_event(payload, handler=HANDLER,
                     decision="no usable frontend (%s)" % (event or "?"))
        return
    decision = TS.handle(fe, event, payload, sid, win)
    A.hook_event(payload, handler=HANDLER, decision=decision)


def _run_watcher(argv):
    """The detached re-invocation path: `interrupt-watch <rollout> <sid> <win>`.
    The watcher owns its own audited streams lifecycle (tabstatus)."""
    from plugins.codex import tabstatus as TS
    mode = argv[0]
    if mode == "interrupt-watch":
        rollout_path = argv[1] if len(argv) > 1 else ""
        sid = argv[2] if len(argv) > 2 else ""
        win = argv[3] if len(argv) > 3 else ""
        try:
            TS.run_interrupt_watch(rollout_path, sid, win)
        except Exception:
            try:
                A.error(sid, "codex interrupt-watch")
            except Exception:
                pass


def entry():
    argv = sys.argv[1:]
    if argv:
        _run_watcher(argv)                     # detached watcher, not a hook
        sys.exit(0)
    try:
        payload = json.loads(sys.stdin.read() or "{}") or {}
    except Exception:
        payload = {}
    try:
        route(payload)
    except Exception:
        try:
            A.error(str(payload.get("session_id") or ""), "codex dispatch")
        except Exception:
            pass
    sys.exit(0)
