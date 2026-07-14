# plugins/codex/launch.py — argv: MIRROR_LOG CWD [SESSION_ID]
# Entry point: claude-codex-launch.py (a thin shim — kept as its own entry so
# the audited spawn chain and the detach-fast contract stay byte-identical).
#
# Tiny launcher for the per-session codex watcher, called from `claude-split.py open`
# (the SessionStart hook). Its ONLY job: start claude-codex-watch.py **fully detached**
# and exit IMMEDIATELY, so SessionStart can never hang.
#
# Why this exists (a hard-won lesson): launching the long-lived watcher from the hook
# with a bash `python3 watch.py &` left it in the HOOK's process group. Claude Code
# waits for a hook's process group to drain, so the never-exiting watcher blocked
# SessionStart — new sessions "got no answer", and the watcher orphaned. The fix is
# the same pattern the mirror's other streamers use: subprocess.Popen(..., start_new_
# session=True), which puts the child in its OWN session/group at fork time. This
# launcher returns in a few ms, so the hook completes instantly.
import os
import subprocess
import sys

from core.paths import BIN  # bin/, where the sibling ENTRY scripts live
WATCH = os.path.join(BIN, "claude-codex-watch.py")

from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import


def main():
    if not os.path.exists(WATCH) or len(sys.argv) < 2:
        return
    proc = subprocess.Popen(
        [sys.executable, WATCH] + sys.argv[1:],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    A.spawn(sys.argv[1], proc.pid, [WATCH] + sys.argv[1:], purpose="codex watcher")


def entry():
    try:
        main()
    except Exception:
        A.error(sys.argv[1] if len(sys.argv) > 1 else "", "codex launch",
                {"argv": sys.argv[1:]})
