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

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RECEIVER = os.path.join(REPO, "claude-otlp-receiver.py")

try:
    from core import audit as A
except Exception:
    class _NoAudit:
        def __getattr__(self, _):
            return lambda *a, **k: None
    A = _NoAudit()


def _port():
    try:
        return int(os.environ.get("CLAUDE_OTEL_PORT") or "4319")
    except (TypeError, ValueError):
        return 4319


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
        pass
