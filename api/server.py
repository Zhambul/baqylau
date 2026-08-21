# api/server.py — the daemon's lifecycle, and now barely more than the bind.
#
# serve() runs the singleton uvicorn server as one audited stream (kind
# 'dashboard') so uptime and the exit path are queryable. The port bind IS the
# singleton guard: a second daemon cannot listen, and a pid claim in a database
# was a second answer to a question the kernel already answers. Everything the
# daemon runs beside the request loop belongs to the application's lifespan
# (api/lifecycle.py), not to this function.
from __future__ import annotations

import signal
import socket
from types import FrameType

import uvicorn
from fastapi import FastAPI

from api import dependencies
from api.app import build_web_application
from app import providers
from app.injection import registry, resolve
from core.daemon.contract import HOST_ADDRESS, PORT_NUMBER

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


def serve() -> int:
    """Run the server in THIS process (the `serve` CLI verb — `start` spawns
    it detached). Singleton: the port bind, and nothing else."""
    # The one registry this process has: the policy below, the routes' services
    # and the lifespan's workers all resolve from it.
    instances = registry()
    policy = resolve(instances, dependencies.policy)
    audit = resolve(instances, providers.recorder)
    try:
        bound_socket = socket.create_server(
            (HOST_ADDRESS, PORT_NUMBER), backlog=policy.request_queue_size
        )
    except OSError:
        audit.error("", "dashboard serve (port busy)", {"port": PORT_NUMBER})
        return 1
    stream_id = audit.stream_start("", "dashboard", src_path=f"http://{HOST_ADDRESS}:{PORT_NUMBER}")
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
            # default lets serve() finish and exit 0, as it always has.
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
        audit.error("", "dashboard serve", {"port": PORT_NUMBER})
        audit.stream_end(stream_id, "crash")
        raise

