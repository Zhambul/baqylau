# The HTTP server for an application that already owns a bound socket and a
# provider graph. api.runtime owns configuration and the port bind. The
# application's lifespan owns all background work.
from __future__ import annotations

import signal
import socket
from types import FrameType

import uvicorn
from fastapi import FastAPI

from api import dependencies
from api.app import build_web_application
from app import providers
from app.injection import Instances, resolve

def build_server(web_application: FastAPI, graceful_shutdown_seconds: int = 3) -> uvicorn.Server:
    """One uvicorn server for an already-bound socket (passed to run()).
    Shared with the HTTP test fixture so the tests exercise the daemon's real
    engine configuration, not a lookalike."""
    return uvicorn.Server(
        uvicorn.Config(
            web_application,
            # The graceful path waits for open connections, and the SSE
            # streams never close on their own — force-close after the grace.
            timeout_graceful_shutdown=graceful_shutdown_seconds,
            lifespan="on",
            access_log=False,
            log_level="warning",
        )
    )


def run_server(bound_socket: socket.socket, instances: Instances) -> int:
    """Run one configured application on an already-bound socket."""
    policy = resolve(instances, dependencies.policy)
    audit = resolve(instances, providers.recorder)
    host, port = bound_socket.getsockname()[:2]
    stream_id = audit.stream_start("", "dashboard", src_path=f"http://{host}:{port}")
    try:
        server = build_server(
            build_web_application(instances, run_background_workers=True),
            policy.graceful_shutdown_seconds,
        )

        def absorb_signal(_signal_number: int, _frame: FrameType | None) -> None:
            # uvicorn captures SIGTERM/SIGINT for its graceful shutdown, then
            # RE-RAISES the captured signal after run() to preserve kill
            # semantics — which would end the process before the cleanup below
            # (the audit stream_end). Restoring to this absorber instead of the
            # default lets the application runtime finish and exit 0.
            pass

        signal.signal(signal.SIGTERM, absorb_signal)
        try:
            # uvicorn installs its own SIGTERM/SIGINT handlers (main thread)
            # and returns from run() after the graceful shutdown; the lifespan
            # stops the background threads on the way out.
            server.run(sockets=[bound_socket])
        except KeyboardInterrupt:
            pass
        finally:
            try:
                bound_socket.close()
            except OSError:
                pass
        audit.stream_end(stream_id, "stopped")
        return 0
    except Exception:
        audit.error("", "dashboard serve", {"port": port})
        audit.stream_end(stream_id, "crash")
        raise
