#!/usr/bin/env python3
"""Accept Claude Code's OTLP export and forward it to the daemon.

    claude_otel.py HOST PORT LISTEN_PORT GRACE_SECONDS

Claude Code is configured (the `env` block of ~/.claude/settings.json) to POST
metrics to a local port every couple of seconds; being that port is this
process's only reason to exist. It owns its port, its gzip and its idle timer —
properties of being an OTLP endpoint — and nothing else: what an export MEANS is
decided daemon-side (`harness/impl/claude_code/otel/gateway.py`).

The daemon spawns it and passes every number it needs, so the launcher's
already-listening pre-check and this bind can no longer disagree. An export the
daemon does not accept is dropped in silence; OTLP counters are re-exported on
the next interval, so this is the cheapest raw event in the tree to miss.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import gzip
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _http                                                     # noqa: E402

HARNESS = "claude_code"
DELIVERY_TIMEOUT_SECONDS = 5.0
# Answer Claude Code the same way whatever happens downstream: its exporter is
# not our error channel.
ACKNOWLEDGEMENT = b"{}"
TELEMETRY_HEADERS = {_http.TELEMETRY_KIND_HEADER: "otlp"}

# The daemon's address and the idle clock, module state rather than attributes
# hung on the server: this process serves one thing and lives for one purpose.
DAEMON_HOST = _http.HOST
DAEMON_PORT = _http.PORT
LAST_DELIVERY_AT = time.time()


def deliver(body: bytes) -> bool:
    """Ship one export. True when the daemon accepted it."""
    if not body:
        return False
    return _daemon.post(
        _http.TELEMETRY_PATH % HARNESS,
        body,
        TELEMETRY_HEADERS,
        host=DAEMON_HOST,
        port=DAEMON_PORT,
        timeout=DELIVERY_TIMEOUT_SECONDS,
    ) is not None


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, format: str, *arguments: str | int | float) -> None:
        del format, arguments                   # never write to a stream nobody reads

    def do_POST(self) -> None:                  # noqa: N802 — http.server's name
        global LAST_DELIVERY_AT
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        if deliver(body):
            LAST_DELIVERY_AT = time.time()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(ACKNOWLEDGEMENT)))
        self.end_headers()
        self.wfile.write(ACKNOWLEDGEMENT)


def serve(listen_port: int, grace_seconds: float) -> None:
    global LAST_DELIVERY_AT
    try:
        server = HTTPServer((_http.HOST, listen_port), Receiver)
    except OSError:
        return                                  # someone else got there first
    LAST_DELIVERY_AT = time.time()
    server.timeout = min(30.0, grace_seconds)
    try:
        while time.time() - LAST_DELIVERY_AT < grace_seconds:
            server.handle_request()
    finally:
        server.server_close()


def main(arguments: list[str]) -> None:
    global DAEMON_HOST, DAEMON_PORT
    if len(arguments) != 4:
        raise SystemExit("usage: claude_otel.py HOST PORT LISTEN_PORT GRACE_SECONDS")
    DAEMON_HOST, DAEMON_PORT = arguments[0], int(arguments[1])
    serve(int(arguments[2]), float(arguments[3]))


if __name__ == "__main__":
    main(sys.argv[1:])
