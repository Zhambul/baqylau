"""The revision of browser application state.

The global event stream reads this small value on each poll. It reads the
application snapshot only when a producer advances the revision.
"""

from __future__ import annotations

import threading


class ApplicationUpdateState:
    """One process-wide revision for state in ``/api/application``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._revision = 0

    def publish(self) -> None:
        with self._lock:
            self._revision += 1

    def revision(self) -> int:
        with self._lock:
            return self._revision
