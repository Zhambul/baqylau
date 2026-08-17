"""Start Claude Code's plugin-owned OTLP receiver when telemetry is enabled.

Runs INSIDE the daemon (a `SessionStarted` reactor), so this is a spawner and
not a client: the receiver itself is `client/claude_otel.py`, and everything it
needs is passed on its argv — the daemon's address, the port to bind and how
long to sit idle. Passing them is what keeps the pre-check below and the bind
over there reading the same numbers.
"""

from __future__ import annotations

import os
import socket
import subprocess

from core import clients
from harness.impl.claude_code.otel.config import grace_seconds, port

TELEMETRY_VARIABLE = "CLAUDE_CODE_ENABLE_TELEMETRY"
LISTEN_PROBE_TIMEOUT_SECONDS = 0.2
# The receiver program, named beside the only code that starts it.
RECEIVER_CLIENT = "claude_otel.py"


def _listening(receiver_port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(LISTEN_PROBE_TIMEOUT_SECONDS)
        return connection.connect_ex(("127.0.0.1", receiver_port)) == 0


def start() -> None:
    if os.environ.get(TELEMETRY_VARIABLE) != "1":
        return
    receiver_port = port()
    if _listening(receiver_port):
        return
    subprocess.Popen(
        clients.command(RECEIVER_CLIENT, receiver_port, grace_seconds()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=dict(os.environ),
    )
