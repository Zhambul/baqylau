"""Receive Claude Code OTLP JSON as exact raw and canonical usage evidence."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import socketserver
import sys
from pathlib import Path
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.data import data_directory
from harness.models import RawEvent
from domain.ids import RawEventId, SessionId
from harness.impl.claude_code.otel.config import port
from runtime.recorder import RawEventRecorder
from runtime.sessions import SessionStore


def idle_seconds() -> float:
    return float(os.environ.get("CLAUDE_OTEL_GRACE_S") or "900")


def _session_ids(document: dict) -> tuple[SessionId, ...]:
    session_ids = set()
    for resource in document.get("resourceMetrics", []):
        for scope in resource.get("scopeMetrics", []):
            for metric in scope.get("metrics", []):
                for point in (metric.get("sum") or {}).get("dataPoints", []):
                    for attribute in point.get("attributes", []):
                        if attribute.get("key") != "session.id":
                            continue
                        value = (attribute.get("value") or {}).get("stringValue")
                        if value:
                            session_ids.add(SessionId(str(value)))
    return tuple(sorted(session_ids, key=str))


def deliver(recorder: RawEventRecorder, sessions: SessionStore, raw_body: bytes) -> int:
    document = json.loads(raw_body)
    delivered = 0
    for session_id in _session_ids(document):
        session = sessions.find_by_id(session_id)
        if session is None:
            continue
        digest = hashlib.sha256(str(session_id).encode() + b"\0" + raw_body).hexdigest()
        recorder.record((
            RawEvent(
                RawEventId(f"claude_code:otel:{digest}"),
                "claude_code",
                "otel",
                "otlp",
                digest,
                session_id,
                session.lead_actor_id,
                None,
                time.time(),
                "json",
                raw_body,
                f"claude_code:otel:{session_id}",
            ),
        ))
        delivered += 1
    return delivered


class ReceiverHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *arguments):
        del format, arguments

    def _body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        return gzip.decompress(body) if self.headers.get("Content-Encoding") == "gzip" else body

    def do_POST(self):
        delivered = deliver(self.server.recorder, self.server.sessions, self._body())
        if delivered:
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

    def __init__(self, address, recorder, sessions):
        super().__init__(address, ReceiverHandler)
        self.recorder = recorder
        self.sessions = sessions
        self.last_delivery_at = time.time()


def serve() -> None:
    database_path = os.path.join(data_directory(), "events.db")
    recorder = RawEventRecorder(database_path)
    sessions = SessionStore(database_path)
    try:
        server = ReceiverServer(("127.0.0.1", port()), recorder, sessions)
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
