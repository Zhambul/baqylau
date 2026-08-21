# Claude Code OTEL receiver configuration — the numbers the daemon hands the
# client (stdlib-only, import-pure).
#
# The OTLP port MUST resolve identically in launch.py's already-listening
# pre-check and the receiver's bind: if the two drift, the launcher either probes
# a port nobody binds (a doomed duplicate spawn every SessionStart) or sees a
# stranger's listener and never spawns the receiver at all. Single-sited here,
# and now unambiguous — the receiver no longer resolves anything itself, it is
# TOLD (client/claude_otel.py takes both numbers on its argv).
from core import env as EV

DEFAULT_PORT = 4319
# How long a receiver with nothing arriving stays up. Claude Code exports every
# couple of seconds while a session lives, so this is really "how long after the
# last session ends".
DEFAULT_GRACE_SECONDS = 900


def port() -> int:
    """The receiver's listen port: CLAUDE_OTEL_PORT, else 4319."""
    return EV.env_int("CLAUDE_OTEL_PORT", DEFAULT_PORT)


def grace_seconds() -> int:
    """The receiver's idle timeout: CLAUDE_OTEL_GRACE_S, else 900 s."""
    return EV.env_int("CLAUDE_OTEL_GRACE_S", DEFAULT_GRACE_SECONDS)
