# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop a test host when the test process that started it ends without its cleanup.

A killed test run cannot stop its hosts, and a host that keeps running keeps
its extension workers. The host watches its parent and stops itself with the
same signal that a normal stop sends.
"""

import os
import signal
import threading
import time

POLL_SECONDS = 1.0


def stop_when_orphaned() -> None:
    """Start a thread that stops this process when its parent process ends."""
    watcher = threading.Thread(target=_watch, args=(os.getppid(),), daemon=True)
    watcher.start()


def _watch(parent: int) -> None:
    while os.getppid() == parent:
        time.sleep(POLL_SECONDS)
    os.kill(os.getpid(), signal.SIGTERM)
