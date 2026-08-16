"""The hook process body: ship one delivery to the daemon, print its reply.

A hook is a THIN CLIENT of the daemon's hook-delivery endpoint — it reads its
stdin, POSTs the exact bytes plus a few flat header values only it can observe
(its terminal window, the CLI pid in its own ancestry, the shell-selected
account, the launch-time selections in its inherited environment), and prints
whatever reply comes back. There is deliberately NO direct
store write and NO daemon boot: the daemon is started by you, and a delivery it
never accepted is lost — audited, client-side, before the swallow.

A hook must never block or fail its harness: every failure path exits cleanly
with empty output, and the delivery timeout is short — a wedged daemon may cost
each hook this pause, never more.
"""

from __future__ import annotations

import sys
import urllib.parse

from core.process import nearest_ancestor_named
from core.daemon import client as daemon_client
from harness.hooks.wire import (
    ACCOUNT_ID_HEADER,
    ACCOUNT_NAME_HEADER,
    HARNESS_PROCESS_HEADER,
    LAUNCH_EFFORT_HEADER,
    LAUNCH_MODEL_HEADER,
    TERMINAL_WINDOW_HEADER,
)

DELIVERY_TIMEOUT_SECONDS = 2.0


def _terminal_window_id() -> str:
    """The window this hook is running in.

    The ORIGIN of every window fact in the system: a hook runs INSIDE the
    session's own window, so it is the only thing that can observe which one
    that is. Everything downstream receives the answer as evidence — which is
    why this is one of the two places allowed to resolve a terminal directly
    instead of taking one by injection.

    Deferred: a hook that never gets this far must not pay for the import.
    """
    from terminal.impl import resolve

    terminal = resolve()
    return (terminal.metadata.current_window_id() if terminal is not None else None) or ""


def run(
    harness: str,
    cli_process_name: str,
    account_id: str = "",
    account_display_name: str = "",
    launch_model: str = "",
    launch_effort: str = "",
) -> None:
    payload = sys.stdin.buffer.read()
    try:
        headers = {
            "Content-Type": "application/json",
            TERMINAL_WINDOW_HEADER: _terminal_window_id(),
            HARNESS_PROCESS_HEADER: str(nearest_ancestor_named(cli_process_name) or ""),
            ACCOUNT_ID_HEADER: account_id,
            ACCOUNT_NAME_HEADER: account_display_name,
            LAUNCH_MODEL_HEADER: launch_model,
            LAUNCH_EFFORT_HEADER: launch_effort,
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
            from diagnostics import record

            record.error("", f"{harness} hook (deliver)", {"payload_bytes": len(payload)})
        except Exception:
            pass
        output = b""
    if output:
        sys.stdout.buffer.write(output)
