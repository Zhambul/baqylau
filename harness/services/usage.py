"""Plan limits: every harness's rows, and the application's current copy of them.

Two tiers, because reading is expensive and displaying is constant: the service
asks each harness for its rows on demand, and the state above it holds the last
answer so a request never waits on a harness's own reader.
"""

from __future__ import annotations

import threading
from typing import Protocol

from harness.models import UsageRow
from harness.registry import HarnessRegistry
from repository.contract.usage import AccountUsageRepository

USAGE_REFRESH_SECONDS = 60.0


class UsageSource(Protocol):
    def read(self) -> tuple[UsageRow, ...]: ...


class HarnessUsageService(UsageSource):
    def __init__(self, harness_registry: HarnessRegistry, account_usage_repository: AccountUsageRepository) -> None:
        self.registry = harness_registry
        self.usage = account_usage_repository

    def read(self) -> tuple[UsageRow, ...]:
        rows: list[UsageRow] = []
        for plugin in self.registry.plugins():
            if plugin.usage is None:
                continue
            plugin_rows = plugin.usage.read(self.usage)
            if any(row.harness != plugin.info.name for row in plugin_rows):
                raise ValueError("usage row harness does not match its plugin")
            rows.extend(plugin_rows)
        return tuple(rows)


class ApplicationUsageState:
    """The application's current usage rows, refreshed outside request handling."""

    def __init__(self, usage_source: UsageSource) -> None:
        self.source = usage_source
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
