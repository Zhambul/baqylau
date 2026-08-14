"""Application-owned current usage state refreshed outside request handling."""

from __future__ import annotations

import threading
from typing import Protocol

from contracts.harness import UsageRow

USAGE_REFRESH_SECONDS = 60.0


class UsageSource(Protocol):
    def read(self) -> tuple[UsageRow, ...]: ...


class ApplicationUsageState:
    def __init__(self, source: UsageSource) -> None:
        self.source = source
        self._lock = threading.Lock()
        self._rows: tuple[UsageRow, ...] = ()

    def usage_rows(self) -> tuple[UsageRow, ...]:
        with self._lock:
            return self._rows

    def refresh(self) -> None:
        rows = self.source.read()
        with self._lock:
            self._rows = rows

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self.refresh()
            stop_event.wait(USAGE_REFRESH_SECONDS)
