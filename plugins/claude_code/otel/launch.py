"""Start Claude Code's plugin-owned OTLP receiver when telemetry is enabled."""

from __future__ import annotations

import os
import socket
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from plugins.claude_code.otel.config import port

RECEIVER_PATH = os.path.join(os.path.dirname(__file__), "receiver.py")


def _listening(receiver_port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.2)
        return connection.connect_ex(("127.0.0.1", receiver_port)) == 0


def start() -> None:
    if os.environ.get("CLAUDE_CODE_ENABLE_TELEMETRY") != "1":
        return
    receiver_port = port()
    if _listening(receiver_port):
        return
    subprocess.Popen(
        [sys.executable, RECEIVER_PATH],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=dict(os.environ),
    )


if __name__ == "__main__":
    start()
