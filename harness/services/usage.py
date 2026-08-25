"""Plan limits: every harness's rows, and the application's current copy of them.

Two tiers, because reading is expensive and displaying is constant: the service
asks each harness for its rows on demand, and the state above it holds the last
answer so a request never waits on a harness's own reader.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from harness.models import UsageRow
from harness.registry import HarnessRegistry
from repository.contract.usage import AccountUsageRepository

USAGE_REFRESH_SECONDS = 5.0
USAGE_INITIAL_DELAY_VARIABLE = "BAQYLAU_USAGE_INITIAL_DELAY_SECONDS"
USAGE_REFRESH_VARIABLE = "BAQYLAU_USAGE_REFRESH_SECONDS"
USAGE_SHARED_CACHE_VARIABLE = "BAQYLAU_USAGE_SHARED_CACHE"
USAGE_SHARED_CACHE_SECONDS_VARIABLE = "BAQYLAU_USAGE_SHARED_CACHE_SECONDS"
USAGE_SHARED_CACHE_SECONDS = 60.0
USAGE_FAILED_CACHE_SECONDS = 5.0


def _configured_seconds(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, str(default))))
    except ValueError:
        return default


class UsageSource(Protocol):
    def read(self) -> tuple[UsageRow, ...]: ...


@dataclass(frozen=True)
class UsageCacheDocument:
    captured_at: float
    rows: tuple[UsageRow, ...]


USAGE_CACHE_DOCUMENT = TypeAdapter(UsageCacheDocument)


class SharedUsageCache:
    """One run-scoped usage probe shared by concurrent application processes."""

    def __init__(self, path: Path, max_age_seconds: float = USAGE_SHARED_CACHE_SECONDS) -> None:
        self.path = path
        self.max_age_seconds = max_age_seconds

    def read(self, usage_source: UsageSource) -> tuple[UsageRow, ...]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            cached = self._fresh()
            if cached is not None:
                return cached.rows
            rows = usage_source.read()
            self._write(UsageCacheDocument(time.time(), rows))
            return rows

    def _fresh(self) -> UsageCacheDocument | None:
        try:
            document = USAGE_CACHE_DOCUMENT.validate_json(self.path.read_bytes())
        except (OSError, ValidationError):
            return None
        age = time.time() - document.captured_at
        max_age = (
            min(self.max_age_seconds, USAGE_FAILED_CACHE_SECONDS)
            if any(row.collection_error for row in document.rows)
            else self.max_age_seconds
        )
        return document if 0.0 <= age < max_age else None

    def _write(self, usage_cache_document: UsageCacheDocument) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                delete=False,
            ) as target:
                temporary_path = Path(target.name)
                target.write(USAGE_CACHE_DOCUMENT.dump_json(usage_cache_document))
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


class HarnessUsageService(UsageSource):
    def __init__(self, harness_registry: HarnessRegistry, account_usage_repository: AccountUsageRepository) -> None:
        self.registry = harness_registry
        self.usage = account_usage_repository
        shared_path = os.environ.get(USAGE_SHARED_CACHE_VARIABLE)
        self.shared_cache = (
            SharedUsageCache(
                Path(shared_path),
                _configured_seconds(
                    USAGE_SHARED_CACHE_SECONDS_VARIABLE,
                    USAGE_SHARED_CACHE_SECONDS,
                ),
            )
            if shared_path
            else None
        )

    def read(self) -> tuple[UsageRow, ...]:
        if self.shared_cache is not None:
            return self.shared_cache.read(_HarnessUsageReader(self.registry, self.usage))
        return _HarnessUsageReader(self.registry, self.usage).read()


class _HarnessUsageReader(UsageSource):
    def __init__(
        self,
        harness_registry: HarnessRegistry,
        account_usage_repository: AccountUsageRepository,
    ) -> None:
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

    def __init__(
        self,
        usage_source: UsageSource,
        initial_delay_seconds: float = 0.0,
        refresh_seconds: float = USAGE_REFRESH_SECONDS,
    ) -> None:
        self.source = usage_source
        self.initial_delay_seconds = initial_delay_seconds
        self.refresh_seconds = refresh_seconds
        self._lock = threading.Lock()
        self._rows: tuple[UsageRow, ...] = ()

    @classmethod
    def configured(cls, usage_source: UsageSource) -> ApplicationUsageState:
        return cls(
            usage_source,
            _configured_seconds(USAGE_INITIAL_DELAY_VARIABLE, 0.0),
            _configured_seconds(USAGE_REFRESH_VARIABLE, USAGE_REFRESH_SECONDS),
        )

    def usage_rows(self) -> tuple[UsageRow, ...]:
        with self._lock:
            return self._rows

    def refresh(self) -> tuple[UsageRow, ...]:
        rows = self.source.read()
        with self._lock:
            self._rows = rows
        return rows

    def run(self, stop_event: threading.Event) -> None:
        if self.initial_delay_seconds and stop_event.wait(self.initial_delay_seconds):
            return
        while not stop_event.is_set():
            try:
                rows = self.refresh()
            except Exception:
                # Usage is auxiliary state and native probes are external
                # processes. A transient probe/cache/repository failure must
                # not kill the only refresh thread and leave the application
                # permanently empty until restart.
                delay = min(self.refresh_seconds, USAGE_FAILED_CACHE_SECONDS)
            else:
                delay = (
                    min(self.refresh_seconds, USAGE_FAILED_CACHE_SECONDS)
                    if any(row.collection_error for row in rows)
                    else self.refresh_seconds
                )
            stop_event.wait(delay)
