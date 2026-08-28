"""One terminal operation at a time for each session."""

from __future__ import annotations

import threading
from _thread import LockType
from collections.abc import Iterator
from contextlib import contextmanager

from domain.ids import SessionId


class SessionTerminalGate:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[SessionId, LockType] = {}

    @contextmanager
    def enter(self, session_id: SessionId) -> Iterator[None]:
        with self._guard:
            lock = self._locks.setdefault(session_id, threading.Lock())
        with lock:
            yield
