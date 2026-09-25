# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish checked extension selections without holding locks across worker calls."""

from collections.abc import Iterator
from contextlib import contextmanager
from math import isfinite
from threading import Condition, Lock

from extensions.models.registry import RegistryPublication
from extensions.registry_contract import ExtensionRegistry, RegistryClosedError, RegistryCommit, RegistryRead
from extensions.registry_snapshot import RuntimeSnapshot, prepare_snapshot


class ActiveExtensionRegistry(ExtensionRegistry):
    """Own the active read boundary, not worker processes or durable lifecycle state."""

    def __init__(self, runtime_revision: str) -> None:
        """Start with an empty checked selection and no active reader."""
        self._lock = Lock()
        self._readers = 0
        self._current = RegistryRead(0, prepare_snapshot(0, runtime_revision, ()))
        self._published = {runtime_revision}
        self._closing = False
        self._drained = Condition(self._lock)

    @contextmanager
    def read_snapshot(self) -> Iterator[RegistryRead]:
        """Hold the current set through nested callbacks and release it on failure.

        Yields:
            A borrowed selection valid only until this context exits.

        """
        with self._lock:
            self._require_open()
            selected = self._current
            self._readers += 1
        try:
            yield selected
        finally:
            with self._lock:
                self._readers -= 1
                self._drained.notify_all()

    def publish_snapshot(
        self, expected_revision: int, snapshot: RuntimeSnapshot, commit: RegistryCommit,
    ) -> RegistryPublication:
        """Reject stale or busy publication without waiting for a worker call.

        Returns:
            The current revision and the exact publication outcome.

        """
        checked = prepare_snapshot(
            snapshot.directory.catalog_revision, snapshot.directory.runtime_revision, snapshot.packages,
            snapshot.relations,
        )
        with self._lock:
            self._require_open()
            if expected_revision != self._current.revision:
                return RegistryPublication(status="stale", revision=self._current.revision)
            if self._readers:
                return RegistryPublication(status="busy", revision=self._current.revision)
            _require_unused(checked, self._published)
            _require_new_boundary(self._current.snapshot, checked)
            return self._commit(checked, commit)

    def close_registry(self, timeout_seconds: float) -> bool:
        """Stop admission without changing the stored selection needed by restart.

        Returns:
            True when no reader still borrows the last published capabilities.

        Raises:
            ValueError: If the wait bound is negative or not finite.

        """
        if not isfinite(timeout_seconds) or timeout_seconds < 0:
            message = "registry close needs a finite non-negative timeout"
            raise ValueError(message)
        with self._drained:
            self._closing = True
            return self._drained.wait_for(lambda: self._readers == 0, timeout_seconds)

    def _require_open(self) -> None:
        if self._closing:
            message = "extension registry is closed to new operations"
            raise RegistryClosedError(message)

    def _commit(self, snapshot: RuntimeSnapshot, commit: RegistryCommit) -> RegistryPublication:
        selected = RegistryRead(self._current.revision + 1, snapshot)
        published = self._published | {snapshot.directory.runtime_revision}
        accepted = RegistryPublication(status="accepted", revision=selected.revision)
        if not commit.commit_registry(snapshot.runtime_selection()):
            return RegistryPublication(status="stale", revision=self._current.revision)
        self._current = selected
        self._published = published
        return accepted


def _require_new_boundary(current: RuntimeSnapshot, proposed: RuntimeSnapshot) -> None:
    if proposed.directory.catalog_revision < current.directory.catalog_revision:
        message = "registry publication cannot move the catalog revision backwards"
        raise ValueError(message)


def _require_unused(proposed: RuntimeSnapshot, published: set[str]) -> None:
    if proposed.directory.runtime_revision in published:
        message = "registry publication requires an unused runtime revision"
        raise ValueError(message)
