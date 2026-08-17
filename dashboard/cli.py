"""baqylau-dashboard.py [serve|start|stop|status|open]

The web dashboard's CLI lifecycle. The implementation behind the thin
bin/baqylau-dashboard.py entry (that filename is operational audit
vocabulary — the spawn below re-launches it by name). Lives in the package so
it is importable/testable in-process, like the rest of the dashboard tier.

  serve   — run the server in the foreground (what `start` spawns; also the
            debugging mode: crashes are visible instead of DEVNULL'd)
  start   — spawn the server detached (core/spawn.spawn_detached — audited,
            start_new_session) unless one is already running; prints the URL
  stop    — SIGTERM whoever answers on the port
  status  — the answering pid + URL
  open    — start (if needed) and open the browser        [the default]

Import-pure: no argv/I/O/DB/frontend work at import — everything runs inside a
function (docs/architecture.md import-time purity rule).
"""
import http.client
import json
import os
import signal
import subprocess
import sys
import time

from audit import record
from core.process import process_is_alive
from dashboard import paths

HEALTH_PATH = "/api/health"
HEALTH_TIMEOUT_SECONDS = 1.0


def _server():
    from api import server  # noqa: PLC0415 — import purity: serve() is the only thing that pulls the server in

    return server


def holder():
    """The running server's pid, or 0.

    Asked over the port the daemon binds, because that bind IS the singleton
    guard — a pid claim in a database was a second answer to the same question,
    and it could disagree.

    Anything other than a pid falls through to `_listening_pid`, which asks the
    kernel who holds the port. That covers the two cases the probe cannot: a
    daemon wedged past answering, and — measured, the first time this shipped —
    a daemon still running the code from BEFORE /api/health existed, which
    answers the probe with a 404. Both are exactly the daemon you need `stop`
    for, and reporting "not running" at one is how you end up with two.
    """
    from core.daemon import contract as daemon_contract  # noqa: PLC0415 — same import purity as _server()

    pid = _answered_pid(daemon_contract) or _listening_pid(daemon_contract.PORT_NUMBER)
    return pid if pid and process_is_alive(pid) else 0


def _answered_pid(daemon_contract):
    """The pid the daemon reports for itself, or 0 if it does not report one."""
    connection = http.client.HTTPConnection(
        daemon_contract.HOST_ADDRESS, daemon_contract.PORT_NUMBER,
        timeout=HEALTH_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", HEALTH_PATH)
        response = connection.getresponse()
        if response.status != 200:
            return 0
        return int(json.loads(response.read())["process_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    finally:
        connection.close()


def _listening_pid(port):
    """Whoever holds the port, when it no longer answers. Best effort: if lsof
    is not installed there is nothing to signal and nothing to report."""
    try:
        found = subprocess.run(
            ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, check=False, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    first = found.stdout.split()
    return int(first[0]) if first and first[0].isdigit() else 0


def url():
    # the daemon contract, not the server facade: the bind address is
    # core/daemon/contract.py's to own, and a lazy import keeps this module import-pure
    # like _server() does (`serve` must stay the only thing that pulls the
    # server in).
    from core.daemon import contract as daemon_contract  # noqa: PLC0415 — same import purity as _server()
    return "http://%s:%d" % (daemon_contract.HOST_ADDRESS, daemon_contract.PORT_NUMBER)


def start():
    if holder():
        print("dashboard already running · %s" % url())
        return 0
    entry = os.path.join(paths.BIN_DIRECTORY, "baqylau-dashboard.py")
    try:
        process = subprocess.Popen(
            [sys.executable, entry, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        record.error("", "spawn web dashboard", {"path": entry})
        print("dashboard failed to spawn (see audit errors)", file=sys.stderr)
        return 1
    record.spawn("", process.pid, [entry, "serve"], purpose="web dashboard")
    for _ in range(40):                     # ~2s for the port to answer
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


def main(argv: list[str]) -> int:
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
    print(__doc__ or "usage: baqylau-dashboard.py [serve|start|stop|status|open]",
          file=sys.stderr)
    return 2
