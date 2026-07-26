# dashboard/notify/broker.py — the /events fan-out.
#
# A tiny publish/subscribe over Queues: an SSE connection registers one, anybody
# with something to say to every open page pushes an (event, payload) pair, and
# a stalled client just misses toasts rather than blocking the publisher.
#
# It lives apart from notifier.py because it is not part of the tab-diff watcher
# at all. Notifier does three things — this fan-out, the asking/done state
# machine, and composing/delivering the deferred off-device alert — and the two
# non-watcher callers only ever wanted the first: sse_global registers a queue,
# and control/launch.launch_wake reaches for the whole singleton to make ONE
# push. Depending on a 480-line watcher to hand a dict to some queues is the
# coupling; a 25-line broker is the dependency those callers actually have.
#
# The singleton BROKER below is the process-wide bus. Notifier takes one (the
# singleton, for NOTIFIER; its own, for a test-constructed instance), so a test
# that builds a Notifier gets an isolated bus instead of sharing the server's.
import queue
import threading

QUEUE_MAX = 100        # per-client backlog: a page that stopped draining (a
#                        backgrounded tab, a wedged tunnel) drops the overflow
#                        rather than growing unbounded or stalling the watcher
#                        thread that publishes to it


class Broker:
    """Fan one (event, payload) out to every registered client queue."""

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    def register(self):
        """A fresh queue for one SSE connection. The caller MUST unregister it
        in a finally — a leaked queue is fed forever by every push."""
        q = queue.Queue(maxsize=QUEUE_MAX)
        with self.lock:
            self.clients.add(q)
        return q

    def unregister(self, q):
        with self.lock:
            self.clients.discard(q)

    def push(self, event, payload):
        """Publish to every client. Snapshot the set under the lock and send
        OUTSIDE it, so a slow put can't hold up a register/unregister."""
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait((event, payload))
            except queue.Full:
                pass                       # a stalled client just misses toasts


BROKER = Broker()      # the process-wide bus (the one NOTIFIER publishes on)
