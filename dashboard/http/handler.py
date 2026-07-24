# dashboard/http/handler.py — the concrete HTTP handler + server lifecycle.
#
# Handler composes the GET / POST / SSE mixins over the plumbing base; serve()
# runs the singleton ThreadingHTTPServer (pid-lock + port bind) as one audited
# stream, and _prune_uploads GCs stale composer attachments at boot.
import os
import signal
import threading
import time
from http.server import ThreadingHTTPServer

from core import locks
from core import paths as P
from core.noaudit import load_audit
from core.tail import stream_lifecycle
from dashboard.config import HOST, LOCK_KEY, PORT
from dashboard.http.base import _Base
from dashboard.http.get import _GetMixin
from dashboard.http.post import _PostMixin
from dashboard.http.sse import _SseMixin
from dashboard.notify.notifier import NOTIFIER

A = load_audit()

UPLOAD_TTL_S = 7 * 24 * 3600      # composer attachments older than this are pruned


class Handler(_GetMixin, _PostMixin, _SseMixin, _Base):
    protocol_version = "HTTP/1.1"
    server_version = "claude-dash"


def _prune_uploads():
    """Best-effort sweep of stale composer attachments (paths.UPLOADS_DIR) at
    server start — the bytes are only needed until Claude Code has read them, so
    a week is generous. Never raises (it's off the request path, and a failed
    prune must not stop the server from booting); empty per-session subdirs are
    removed too."""
    root = P.UPLOADS_DIR
    now = time.time()
    try:
        subs = os.listdir(root)
    except OSError:
        return
    for sub in subs:
        d = os.path.join(root, sub)
        try:
            for name in os.listdir(d):
                f = os.path.join(d, name)
                try:
                    if now - os.path.getmtime(f) > UPLOAD_TTL_S:
                        os.remove(f)
                except OSError:
                    pass
            if not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass


def serve():
    """Run the server in THIS process (the `serve` CLI verb — `start` spawns
    it detached). Singleton: the paths.DASH_DB pid-lock first, the port bind
    as the second guard. The whole run is one audited stream (kind
    'dashboard') so uptime and the exit path are queryable."""
    res = locks.lock_acquire(P.DASH_DB, LOCK_KEY)
    if res.startswith("claim-denied"):
        A.error("", "dashboard serve (lock denied)", {"res": res})
        return 1
    with stream_lifecycle("", "dashboard", src_path="http://%s:%d" % (HOST, PORT),
                          ctx={"port": PORT},
                          on_exit=lambda: locks.lock_release(P.DASH_DB, LOCK_KEY)) as run:
        try:
            httpd = ThreadingHTTPServer((HOST, PORT), Handler)
        except OSError:
            run.end("port-busy")
            A.error("", "dashboard serve (port busy)", {"port": PORT})
            return 1
        httpd.daemon_threads = True
        _prune_uploads()
        threading.Thread(target=NOTIFIER.run, daemon=True).start()

        def _term(signum, frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _term)
        run.end("stopped")                  # the expected exit (SIGTERM/^C);
        try:                                # a crash overwrites it via __exit__
            httpd.serve_forever(poll_interval=0.5)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            try:
                httpd.server_close()
            except Exception:
                pass
    return 0
