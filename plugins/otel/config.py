# plugins/otel/config.py — shared launcher/receiver configuration (stdlib-only).
#
# The OTLP port MUST resolve identically in launch.py's already-listening
# pre-check and receiver.py's bind: if the two drift, the launcher either probes
# a port nobody binds (a doomed duplicate spawn every SessionStart) or sees a
# stranger's listener and never spawns the receiver at all. Single-sited here —
# a leaf module with no heavy deps, so launch.py's detach-fast path stays a
# few-ms import.
import os

DEFAULT_PORT = 4319


def port():
    """The receiver's listen port: CLAUDE_OTEL_PORT, else 4319."""
    try:
        return int(os.environ.get("CLAUDE_OTEL_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT
