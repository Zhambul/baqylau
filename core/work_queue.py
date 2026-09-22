# Copyright (c) 2026 Zhambyl Yermagambet
"""Combine work notices and wait for data or a known deadline."""

from enum import Enum, auto
from math import isfinite
from threading import Condition
from time import monotonic


class WorkKind(Enum):
    """Name the stages that can have pending work."""

    EXTENSIONS = auto()
    SOURCES = auto()
    EXTENSION_SOURCES = auto()
    RAW = auto()
    CANONICAL = auto()


class WorkQueue:
    """Keep notices until the worker takes them, including notices during work."""

    def __init__(self) -> None:
        """Create an empty queue with no timed wake."""
        self._condition = Condition()
        self._pending: set[WorkKind] = set()
        self._deadlines: dict[tuple[WorkKind, str], float] = {}
        self._closed = False

    def put(self, work_kind: WorkKind) -> None:
        """Make one stage ready."""
        with self._condition:
            self._pending.add(work_kind)
            self._condition.notify()

    def schedule(self, work_kind: WorkKind, delay: float, key: str = "") -> None:
        """Wake a stage at its earliest known deadline."""
        with self._condition:
            deadline = monotonic() + max(0, delay)
            identity = work_kind, key
            self._deadlines[identity] = min(self._deadlines.get(identity, deadline), deadline)
            self._condition.notify()

    def take(self) -> set[WorkKind]:
        """Wait for work.

        Returns:
            Ready stages, or an empty set after close.

        """
        with self._condition:
            while not self._closed:
                now = monotonic()
                self._ready(now)
                if self._pending:
                    pending = self._pending
                    self._pending = set()
                    return pending
                timeout = min(self._deadlines.values()) - now if self._deadlines else None
                self._condition.wait(timeout)
            return set()

    def set_deadline(self, work_kind: WorkKind, delay: float | None, key: str) -> None:
        """Replace or clear one owned deadline without changing other producers.

        Raises:
            ValueError: If a supplied delay is not finite.

        """
        if delay is not None and not isfinite(delay):
            message = "work deadline delay must be finite"
            raise ValueError(message)
        with self._condition:
            identity = work_kind, key
            if delay is None:
                self._deadlines.pop(identity, None)
            else:
                self._deadlines[identity] = monotonic() + max(0, delay)
            self._condition.notify()

    def close(self) -> None:
        """Release a waiting worker at shutdown."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _ready(self, now: float) -> None:
        for identity, deadline in tuple(self._deadlines.items()):
            if deadline <= now:
                self._pending.add(identity[0])
                self._deadlines.pop(identity)
