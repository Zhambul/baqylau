# plugins/otel/launch.py
# Entry point: claude-otlp-launch.py (a thin shim — kept as its own entry so the
# audited spawn chain and the detach-fast contract stay byte-identical to codex's).
#
# Detach-fast launcher for the GLOBAL OTLP receiver, called from every host
# SessionStart (via plugins.on_session_start). Its only job: start the receiver
# FULLY DETACHED (start_new_session=True, the hard-won lesson in
# plugins/codex/launch.py — a child left in the hook's process group hangs
# SessionStart) and return in a few ms.
#
# Cheap pre-check: if a receiver is already listening on the port we skip spawning,
# so steady-state sessions don't fork a doomed process each time. The receiver
# itself is the AUTHORITATIVE singleton guard (global lock + port bind), so a race
# between two first-sessions is still safe — the loser exits 0 with a clean
# duplicate `streams` row.
import os
import socket
import subprocess
import sys

from core.paths import ROOT  # the repo root, where the sibling ENTRY scripts live
RECEIVER = os.path.join(ROOT, "claude-otlp-receiver.py")

from core.noaudit import load_audit
from plugins.otel.config import port as _port   # the ONE port resolver (must match the receiver's bind)

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import


def _already_listening(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def main():
    if not os.path.exists(RECEIVER):
        return
    if _already_listening(_port()):
        return
    proc = subprocess.Popen(
        [sys.executable, RECEIVER],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
        env=dict(os.environ))
    # A synthetic per-machine key (the receiver's own audit vocabulary), matching
    # receiver.SELF_LOG so the spawn and the receiver's streams row join up.
    A.spawn("otlp-receiver", proc.pid, [RECEIVER], purpose="stream:otlp")


def entry():
    try:
        main()
    except Exception:
        A.error("", "otlp launch", {"receiver": RECEIVER, "port": _port()})
