"""claude-dashboard.py [serve|start|stop|status|open]

The web dashboard's CLI lifecycle. The implementation behind the thin
bin/claude-dashboard.py shim (bin/ entries stay shims; that FILENAME is audit
vocabulary — the spawn below re-launches it by name). Lives in the package so
it is importable/testable in-process, like the rest of the dashboard tier.

  serve   — run the server in the foreground (what `start` spawns; also the
            debugging mode: crashes are visible instead of DEVNULL'd)
  start   — spawn the server detached (core/spawn.spawn_detached — audited,
            start_new_session) unless one is already running; prints the URL
  stop    — SIGTERM the lock-holder pid
  status  — holder pid + URL
  open    — start (if needed) and open the browser        [the default]

Import-pure: no argv/I/O/DB/frontend work at import — everything runs inside a
function (docs/architecture.md import-time purity rule).
"""
import os
import signal
import subprocess
import sys
import time

from core import locks
from core import paths as P
from core import spawn as SP
from core.state import pid_alive


def _server():
    from dashboard import server
    return server


def holder():
    """The running server's pid, or 0 (dead holders are not 'running' — the
    next start steals the stale lock)."""
    pid = locks.lock_holder(P.DASH_DB, "dashboard")
    return pid if pid and pid_alive(pid) else 0


def url():
    # config, not the server facade: the bind address is config.py's to own, and
    # a lazy import keeps this module import-pure like _server() does (config is
    # cheap, but `serve` must stay the only thing that pulls the server in).
    from dashboard import config
    return "http://%s:%d" % (config.HOST, config.PORT)


def start():
    if holder():
        print("dashboard already running · %s" % url())
        return 0
    entry = os.path.join(P.BIN, "claude-dashboard.py")   # the load-bearing bin name
    proc = SP.spawn_detached(entry, ["serve"], "", purpose="web dashboard")
    if proc is None:
        print("dashboard failed to spawn (see audit errors)", file=sys.stderr)
        return 1
    for _ in range(40):                     # ~2s for the lock/port to land
        if holder():
            break
        time.sleep(0.05)
    print("dashboard started · %s" % url())
    return 0


def stop():
    pid = holder()
    if not pid:
        print("dashboard not running")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        print("dashboard stopped (pid %d)" % pid)
        return 0
    except OSError as e:
        print("stop failed: %s" % e, file=sys.stderr)
        return 1


def status():
    pid = holder()
    if pid:
        print("running · pid %d · %s" % (pid, url()))
    else:
        print("not running")
    return 0


def open_browser():
    rc = start()
    if rc:
        return rc
    try:
        subprocess.run(["open", url()], check=False)   # macOS; harmless no-op elsewhere
    except OSError:
        pass
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "open"
    if cmd == "serve":
        return _server().serve()
    if cmd == "start":
        return start()
    if cmd == "stop":
        return stop()
    if cmd == "status":
        return status()
    if cmd == "open":
        return open_browser()
    print(__doc__ or "usage: claude-dashboard.py [serve|start|stop|status|open]",
          file=sys.stderr)
    return 2
