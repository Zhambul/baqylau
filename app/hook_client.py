"""The hook process body: ship one delivery to the daemon, print its reply.

A hook is a THIN CLIENT of the daemon's hook-delivery endpoint — it reads its
stdin, POSTs the exact bytes plus four flat header values only it can observe
(its terminal window, the CLI pid in its own ancestry, the shell-selected
account), and prints whatever reply comes back. There is deliberately NO direct
store write and NO daemon boot: the daemon is started by you, and a delivery it
never accepted is lost — audited, client-side, before the swallow.

A hook must never block or fail its harness: every failure path exits cleanly
with empty output, and the delivery timeout is short — a wedged daemon may cost
each hook this pause, never more.
"""

from __future__ import annotations

import sys
import urllib.parse

import frontends
from core.process import nearest_ancestor_named
from app import daemon_client
from dashboard.config import (
    ACCOUNT_ID_HEADER,
    ACCOUNT_NAME_HEADER,
    HARNESS_PROCESS_HEADER,
    TERMINAL_WINDOW_HEADER,
)

DELIVERY_TIMEOUT_SECONDS = 2.0


def run(
    harness: str,
    cli_process_name: str,
    account_id: str = "",
    account_display_name: str = "",
) -> None:
    payload = sys.stdin.buffer.read()
    try:
        headers = {
            "Content-Type": "application/json",
            TERMINAL_WINDOW_HEADER: frontends.current_window_id() or "",
            HARNESS_PROCESS_HEADER: str(nearest_ancestor_named(cli_process_name) or ""),
            ACCOUNT_ID_HEADER: account_id,
            ACCOUNT_NAME_HEADER: account_display_name,
        }
        status, output = daemon_client.post_bytes(
            f"/api/harnesses/{urllib.parse.quote(harness)}/hooks",
            payload,
            headers,
            timeout=DELIVERY_TIMEOUT_SECONDS,
        )
        if status != 200:
            raise RuntimeError(f"hook delivery refused: {status}")
    except Exception:
        # Daemon down or refused: the delivery is lost; the audit row is the trace.
        try:
            from core import audit

            audit.error("", f"{harness} hook (deliver)", {"payload_bytes": len(payload)})
        except Exception:
            pass
        output = b""
    if output:
        sys.stdout.buffer.write(output)
