# dashboard/http/handler.py — the concrete HTTP handler + server lifecycle.
#
# Handler composes the GET / POST / SSE mixins over the plumbing base; serve()
# runs the singleton ThreadingHTTPServer (pid-lock + port bind) as one audited
# stream, and _prune_uploads GCs stale composer attachments at boot.
import os
import signal
import sys
import threading
import time
from http.server import ThreadingHTTPServer

from app.bootstrap import build_default_application
from core import locks
from core import audit as A
from dashboard import paths
from dashboard.config import REQUEST_QUEUE_SIZE, HOST_ADDRESS, LOCK_KEY, PORT_NUMBER
from dashboard.http.base import _Base
from dashboard.http.canonical import _CanonicalMixin
from dashboard.http.get import _GetMixin
from dashboard.http.post import _PostMixin


UPLOAD_LIFETIME_SECONDS = 7 * 24 * 3600


class Handler(_CanonicalMixin, _GetMixin, _PostMixin, _Base):
    protocol_version = "HTTP/1.1"
    server_version = "baqylau-dashboard"


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # the stdlib default backlog of 5 RSTs the tunnel's refresh bursts — the
    # "502 / half-loaded page" failure (config.REQUEST_QUEUE_SIZE). Class attribute, not a
    # post-construction assignment: listen() runs inside __init__.
    request_queue_size = REQUEST_QUEUE_SIZE

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        if isinstance(error, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def _prune_uploads():
    """Remove composer attachments older than their delivery lifetime."""
    root = paths.UPLOADS_DIRECTORY
    now = time.time()
    try:
        session_directories = os.listdir(root)
    except OSError:
        return
    for directory_name in session_directories:
        directory_path = os.path.join(root, directory_name)
        try:
            for file_name in os.listdir(directory_path):
                file_path = os.path.join(directory_path, file_name)
                try:
                    if now - os.path.getmtime(file_path) > UPLOAD_LIFETIME_SECONDS:
                        os.remove(file_path)
                except OSError:
                    pass
            if not os.listdir(directory_path):
                os.rmdir(directory_path)
        except OSError:
            pass


def serve():
    """Run the server in THIS process (the `serve` CLI verb — `start` spawns
    it detached). Singleton: the paths.DASH_DB pid-lock first, the port bind
    as the second guard. The whole run is one audited stream (kind
    'dashboard') so uptime and the exit path are queryable."""
    lock_result = locks.lock_acquire(paths.DASHBOARD_LOCK_DATABASE, LOCK_KEY)
    if lock_result.startswith("claim-denied"):
        A.error("", "dashboard serve (lock denied)", {"result": lock_result})
        return 1
    stream_id = A.stream_start("", "dashboard", src_path=f"http://{HOST_ADDRESS}:{PORT_NUMBER}")
    try:
        try:
            httpd = Server((HOST_ADDRESS, PORT_NUMBER), Handler)
        except OSError:
            A.error("", "dashboard serve (port busy)", {"port": PORT_NUMBER})
            A.stream_end(stream_id, "port-busy")
            return 1
        application = build_default_application()
        httpd.canonical_application = application
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
        _prune_uploads()
        from dashboard.notify.notifier import Notifier

        notifier = Notifier(application)
        threading.Thread(target=notifier.run, daemon=True).start()

        def stop_server(_signal_number, _frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop_server)
        try:
            httpd.serve_forever(poll_interval=0.5)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            observation_stop.set()
            usage_stop.set()
            observation_thread.join(timeout=2)
            usage_thread.join(timeout=2)
            try:
                httpd.server_close()
            except Exception:
                pass
        A.stream_end(stream_id, "stopped")
        return 0
    except Exception:
        A.error("", "dashboard serve", {"port": PORT_NUMBER})
        A.stream_end(stream_id, "crash")
        raise
    finally:
        locks.lock_release(paths.DASHBOARD_LOCK_DATABASE, LOCK_KEY)
