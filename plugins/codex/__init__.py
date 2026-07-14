# plugins/codex/ — the codex adapter (a SECONDARY source).
#
# Codex has no hook system pointed at us: the plugin discovers every codex run
# from the two global directories all runs funnel through (companion job
# sidecars + native rollouts — see watch.py) and streams each into the HOSTING
# session's mirror. Modules: launch.py (detach-fast launcher), watch.py (the
# one-per-session discovery watcher), stream.py (one tailer per run).
import os
import subprocess
import sys

from core.paths import ROOT  # the repo root, where the sibling ENTRY scripts live


def on_session_start(log, cwd, sid):
    """Attach codex discovery to a starting host session: run the launcher
    entry, which Popens the watcher DETACHED (start_new_session) and exits in a
    few ms — so SessionStart can never hang on it (the hard-won lesson in
    plugins/codex/launch.py). Invoked via the plugins registry from the host's
    SessionStart (plugins/claude_code/split.py cmd_open)."""
    launcher = os.path.join(ROOT, "claude-codex-launch.py")
    if not os.path.isfile(launcher):
        return
    try:
        subprocess.run([sys.executable or "python3", launcher, log, cwd, sid],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
