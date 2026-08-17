"""Accept Claude Code's OTLP export and ship it to the daemon.

A thin client, exactly like a hook process: bind the port, read the body,
POST the exact bytes to the daemon's telemetry endpoint, exit when nothing has
arrived for a while. It owns its port, its gzip and its idle timer — those are
properties of being an OTLP endpoint — and nothing else.

It used to open the event store and append raw events itself, which made it the
only writer of canonical evidence outside the daemon. The trade is the same one
the hook channel already makes deliberately: telemetry needs a running daemon,
and a delivery it never accepted is lost — audited here, before the swallow.
OTLP counters are re-exported on the next interval, so this is the cheapest
evidence in the tree to miss.
"""

from __future__ import annotations

import gzip
import os
import socketserver
import sys
from pathlib import Path
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.daemon import client as daemon_client
from diagnostics import record as A
from harness.models import TELEMETRY_KIND_HEADER
from harness.impl.claude_code.otel.config import port

DELIVERY_PATH = "/api/harnesses/claude_code/telemetry"
DELIVERY_TIMEOUT_SECONDS = 5.0


def idle_seconds() -> float:
    return float(os.environ.get("CLAUDE_OTEL_GRACE_S") or "900")


def deliver(raw_body: bytes) -> bool:
    """Ship one export. True when the daemon accepted it."""
    if not raw_body:
        return False
    try:
        status, _reply = daemon_client.post_bytes(
            DELIVERY_PATH,
            raw_body,
            {"Content-Type": "application/json", TELEMETRY_KIND_HEADER: "otlp"},
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
    except OSError:
        # The daemon is not running. Audited rather than swallowed: "the export
        # had nowhere to go" is exactly the thing that is otherwise invisible.
        A.error("", "otel delivery (daemon unreachable)", {"bytes": len(raw_body)})
        return False
    if status != 200:
        A.error("", "otel delivery", {"status": status, "bytes": len(raw_body)})
        return False
    return True


class ReceiverHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *arguments):
        del format, arguments

    def _body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        return gzip.decompress(body) if self.headers.get("Content-Encoding") == "gzip" else body

    def do_POST(self):
        if deliver(self._body()):
            self.server.last_delivery_at = time.time()
        response = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


class ReceiverServer(HTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def __init__(self, address):
        super().__init__(address, ReceiverHandler)
        self.last_delivery_at = time.time()


def serve() -> None:
    try:
        server = ReceiverServer(("127.0.0.1", port()))
    except OSError:
        return
    maximum_idle = idle_seconds()
    server.timeout = min(30.0, maximum_idle)
    try:
        while time.time() - server.last_delivery_at < maximum_idle:
            server.handle_request()
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
