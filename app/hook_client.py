"""The hook process body: ship one delivery to the daemon, print its reply.

A hook is a THIN CLIENT of the daemon's hook-delivery endpoint — it reads its
stdin, ensures the daemon is up, POSTs the exact bytes plus the env subset only
it can see, and prints whatever reply comes back. There is deliberately NO
direct store write: the daemon's `HookGatewayService` is the one recorder of
pushed hook evidence, so a delivery the daemon never accepted is lost — and
audited, client-side, before the swallow.

A hook must never block or fail its harness: every failure path exits cleanly
with empty output, and the delivery timeout is short — a wedged daemon may cost
each hook this pause, never more.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse

from app import daemon_client
from app.host import ApplicationHost
from dashboard.config import ENVIRONMENT_HEADER

DELIVERY_TIMEOUT_SECONDS = 2.0


def deliver(harness: str, environment_keys: tuple[str, ...], payload: bytes) -> bytes:
    """POST one hook delivery; the response body is the hook's stdout reply."""
    ApplicationHost().ensure_running()
    environment = {
        key: os.environ[key] for key in environment_keys if os.environ.get(key)
    }
    status, body = daemon_client.post_bytes(
        f"/api/harnesses/{urllib.parse.quote(harness)}/hooks",
        payload,
        {
            "Content-Type": "application/json",
            ENVIRONMENT_HEADER: json.dumps(environment),
        },
        timeout=DELIVERY_TIMEOUT_SECONDS,
    )
    if status != 200:
        raise RuntimeError(
            f"hook delivery refused: {status} {body[:200].decode('utf-8', 'replace')}"
        )
    return body


def run(harness: str, environment_keys: tuple[str, ...]) -> None:
    payload = sys.stdin.buffer.read()
    try:
        output = deliver(harness, environment_keys, payload)
    except Exception:
        # A hook must never block or fail its harness; the audit row is the trace.
        try:
            from core import audit

            audit.error("", f"{harness} hook (deliver)", {"payload_bytes": len(payload)})
        except Exception:
            pass
        output = b""
    if output:
        sys.stdout.buffer.write(output)
