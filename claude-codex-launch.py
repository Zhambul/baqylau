#!/usr/bin/env python3
# claude-codex-launch.py MIRROR_LOG CWD [SESSION_ID]
#
# Tiny launcher for the per-session codex watcher, called from `claude-split.sh open`
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

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "claude-codex-watch.py")


def main():
    if not os.path.exists(WATCH) or len(sys.argv) < 2:
        return
    subprocess.Popen(
        [sys.executable, WATCH] + sys.argv[1:],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
