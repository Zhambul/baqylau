# api/server.py — the daemon's lifecycle: the ONE process that builds the
# application graph.
#
# serve() runs the singleton uvicorn server (pid-lock + port bind as the two
# guards) with the interpreter, usage and notifier threads beside it, as one
# audited stream (kind 'dashboard') so uptime and the exit path are queryable.
# Everything else stays a recorder or a thin HTTP/SSE client of this process.
from __future__ import annotations

import os
import signal
import socket
import threading

import uvicorn

from api import config
from api.app import build_web_application
from repository.impl.sqlite.databases import lock_database
from repository.impl.sqlite.locks import SqliteProcessLockRepository
from app.bootstrap import build_default_application
from diagnostics import record as A
from core.daemon.contract import HOST_ADDRESS, PORT_NUMBER
from notify.notifier import Notifier

def build_server(web_application) -> uvicorn.Server:
    """One uvicorn server for an already-bound socket (passed to run()).
    Shared with the HTTP test fixture so the tests exercise the daemon's real
    engine configuration, not a lookalike."""
    return uvicorn.Server(
        uvicorn.Config(
            web_application,
            # The graceful path waits for open connections, and the SSE
            # streams never close on their own — force-close after the grace.
            timeout_graceful_shutdown=config.GRACEFUL_SHUTDOWN_SECONDS,
            lifespan="on",
            access_log=False,
            log_level="warning",
        )
    )


def serve():
    """Run the server in THIS process (the `serve` CLI verb — `start` spawns
    it detached). Singleton: the paths.DASH_DB pid-lock first, the port bind
    as the second guard."""
    locks = SqliteProcessLockRepository(lock_database())
    lock = locks.acquire(config.LOCK_KEY, os.getpid())
    if not lock.held:
        A.error("", "dashboard serve (lock denied)", {
            "decision": lock.decision,
            "holder": lock.holder_process_id,
        })
        return 1
    stream_id = A.stream_start("", "dashboard", src_path=f"http://{HOST_ADDRESS}:{PORT_NUMBER}")
    try:
        try:
            bound_socket = socket.create_server(
                (HOST_ADDRESS, PORT_NUMBER), backlog=config.REQUEST_QUEUE_SIZE
            )
        except OSError:
            A.error("", "dashboard serve (port busy)", {"port": PORT_NUMBER})
            A.stream_end(stream_id, "port-busy")
            return 1
        application = build_default_application()
        web_application = build_web_application(application)
        observation_stop = threading.Event()
        observation_thread = threading.Thread(
            target=application.interpreter.run,
            args=(observation_stop,),
            daemon=True,
            name="baqylau-interpreter",
        )
        observation_thread.start()
        usage_stop = threading.Event()
        usage_thread = threading.Thread(
            target=application.usage_state.run,
            args=(usage_stop,),
            daemon=True,
            name="baqylau-usage",
        )
        usage_thread.start()
        # Attachments are pruned from the ROW, not by walking the directory
        # and trusting mtimes: what we wrote is what we know about.
        application.uploads.prune()
        notifier = Notifier(application)
        threading.Thread(target=notifier.run, daemon=True).start()

        server = build_server(web_application)

        def absorb_signal(_signal_number, _frame):
            # uvicorn captures SIGTERM/SIGINT for its graceful shutdown, then
            # RE-RAISES the captured signal after run() to preserve kill
            # semantics — which would end the process before the cleanup
            # below (the audit stream_end, the lock release). Restoring to
            # this absorber instead of the default lets serve() finish and
            # exit 0, as it always has.
            pass

        signal.signal(signal.SIGTERM, absorb_signal)
        try:
            # uvicorn installs its own SIGTERM/SIGINT handlers (main thread)
            # and returns from run() after the graceful shutdown.
            server.run(sockets=[bound_socket])
        except KeyboardInterrupt:
            pass
        finally:
            observation_stop.set()
            usage_stop.set()
            observation_thread.join(timeout=2)
            usage_thread.join(timeout=2)
            try:
                bound_socket.close()
            except OSError:
                pass
        A.stream_end(stream_id, "stopped")
        return 0
    except Exception:
        A.error("", "dashboard serve", {"port": PORT_NUMBER})
        A.stream_end(stream_id, "crash")
        raise
    finally:
        locks.release(config.LOCK_KEY, os.getpid())
