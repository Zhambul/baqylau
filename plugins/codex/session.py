# plugins/codex/session.py — the codex HOST session handler.
# Entry point: claude-codex-session.py (a thin shim — the entry FILENAME is the
# audit vocabulary and what codex's SessionStart hook invokes).
#
# argv: none — the codex SessionStart payload arrives as JSON on stdin, exactly
# like a Claude Code hook. Codex hooks (CLI v0.142+, `[features] hooks = true`)
# are Claude-compatible: {session_id, cwd, source, transcript_path, ...}.
#
# This makes codex a FIRST-CLASS HOST. When you run `codex` on its own in a kitty
# tab (no Claude Code session hosting it), its native SessionStart hook fires this
# handler, which stands up the SAME mirror pane + scoreboard + state DB that
# Claude Code's SessionStart does — via the shared core.hostpane lifecycle — and
# spawns this session's codex watcher to stream the run into it.
#
# NESTED vs STANDALONE. Codex also runs as a Claude SUBAGENT (Claude launches
# `codex exec`); that inner codex inherits Claude's kitty pane, so ITS SessionStart
# hook fires too. But Claude's own codex watcher already streams that run into
# Claude's mirror. So when this handler detects it is nested inside a live host
# (the tab already carries a claude_mirror), it does NOTHING — no second mirror,
# no double stream. Only a truly standalone codex opens its own.
#
# TEARDOWN. Codex has NO SessionEnd hook — only Stop (per-turn) and the 10
# start/tool/compact events. So, exactly like every other cancellation path in
# this repo (CLAUDE.md: "every cancellation path needs its own liveness signal"),
# teardown rides a liveness signal: this handler resolves the codex process pid
# (walking the ppid chain from the hook) and hands it to the per-session watcher,
# which parks the state DB + closes the panes when codex exits — even on a hard
# Ctrl-C, which fires no hook at all.
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAUNCH = os.path.join(REPO, "claude-codex-launch.py")

import frontends                                   # noqa: E402
from core import audit as A                        # noqa: E402
from core import hostpane as HP                    # noqa: E402
from core import paths as P                        # noqa: E402


def read_payload():
    try:
        return json.loads(sys.stdin.read() or "{}") or {}
    except Exception:
        return {}


def codex_pid():
    """The pid of the codex process hosting this hook — teardown's liveness
    signal. The hook runs as a descendant of codex; walk the ppid chain until a
    process whose command is `codex` (the chain from a short-lived hook is
    shallow). Fall back to the immediate parent when the walk can't identify it
    (a parent that dies is still a reasonable end-of-session signal)."""
    parent = os.getppid()
    pid = parent
    for _ in range(12):
        if pid <= 1:
            break
        try:
            out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=2).stdout.strip()
        except Exception:
            break
        parts = out.split(None, 1)
        if not parts:
            break
        comm = parts[1].strip() if len(parts) > 1 else ""
        if os.path.basename(comm) == "codex":
            return pid
        try:
            pid = int(parts[0])
        except ValueError:
            break
    return parent


def bias():
    """Mirror width % — the same CLAUDE_MIRROR_BIAS setting Claude's split reads,
    honoured from the env when codex inherited it, else the 25% default."""
    v = os.environ.get("CLAUDE_MIRROR_BIAS")
    try:
        return int(v)
    except (TypeError, ValueError):
        return 25


def spawn_watcher(log, cwd, sid, host_pid):
    """Detach this session's codex watcher in STANDALONE mode (host pid passed as
    argv[4]) via the launcher, whose only job is a fast Popen(start_new_session)
    so this hook returns immediately (the hard-won lesson in launch.py)."""
    if not os.path.isfile(LAUNCH):
        return
    try:
        subprocess.run([sys.executable or "python3", LAUNCH, log, cwd, sid,
                        str(host_pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        A.error(log, "spawn codex standalone watcher", {"sid": sid})


def main():
    payload = read_payload()
    sid = str(payload.get("session_id") or "")
    cwd = payload.get("cwd") or os.getcwd()
    source = payload.get("source") or ""
    if not sid:
        A.hook_event(payload, handler="codex-session", decision="no session_id")
        return

    # The terminal adapter (resolve=True: a detached/inherited codex hook may not
    # carry KITTY_LISTEN_ON — walk the ppid chain to the controlling instance).
    # export_env() stamps the resolved socket back so the watcher inherits it.
    fe = frontends.get(resolve=True)
    if not fe.usable():
        A.hook_event(payload, handler="codex-session", decision="no usable frontend")
        return
    fe.export_env()

    # Nested inside a live Claude (or other) host? That host's codex watcher
    # already streams this run — do not open a second mirror.
    host = HP.tab_host_sid(fe, exclude_sid=sid)
    if host:
        A.hook_event(payload, handler="codex-session",
                     decision="nested-skip (host mirror %s present)" % host)
        return

    log = P.mirror_log(sid)
    try:
        A.session_start(payload)
    except Exception:
        pass
    fate = HP.decide_log_fate(sid, log)
    try:
        A.state_file(log, log + ".state.db.keep", fate, "source=" + source)
    except Exception:
        pass
    HP.ensure_db(log)

    win = fe.current_window()                 # tag the codex pane for keybindings
    if win:
        fe.set_user_vars(win, {"claude_session": sid})
    HP.close_stale_mirrors(fe, sid)           # a prior-sid pane (resume/clear)
    b = bias()
    HP.open_mirror(fe, REPO, sid, log, b, 25)

    ok = HP.mirror_exists(fe, sid)
    try:
        A.pane(sid, "open", 1 if ok else 0,
               "standalone codex bias=%d%% %s" % (b, fate)
               if ok else "mirror window absent after launch")
    except Exception:
        pass

    host_pid = codex_pid()
    spawn_watcher(log, cwd, sid, host_pid)
    A.hook_event(payload, handler="codex-session",
                 decision="standalone-open (%s, host_pid=%d)" % (fate, host_pid))


def entry():
    try:
        main()
    except Exception:
        try:
            A.error("", "codex session main")
        except Exception:
            pass
    sys.exit(0)
