# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep candidate worker resources separate from borrowed registry references."""

from contextlib import ExitStack
from threading import Lock

from extensions.models.runtime_resolution import RuntimeResolution
from extensions.registry_snapshot import RuntimeSnapshot
from extensions.runtime_preparation_contract import PreparedExtensionRuntime, RuntimePreparationError


class OwnedPreparedRuntime(PreparedExtensionRuntime):
    """Release workers in reverse preparation order only after the manager permits it."""

    def __init__(
        self, snapshot: RuntimeSnapshot, cleanup: ExitStack, resolution: RuntimeResolution | None = None,
    ) -> None:
        """Take a checked snapshot and all resources from successful preparation."""
        self._snapshot = snapshot
        self._cleanup = cleanup
        self._lock = Lock()
        self._closed = False
        self._resolution = resolution

    @property
    def resolution(self) -> RuntimeResolution | None:
        """The complete migration result retained with these prepared resources."""
        return self._resolution

    @property
    def snapshot(self) -> RuntimeSnapshot:
        """The immutable candidate while its resource owner remains open.

        Raises:
            RuntimePreparationError: If this candidate's workers have been closed.

        """
        with self._lock:
            if self._closed:
                message = "prepared extension runtime is closed"
                raise RuntimePreparationError(message)
            return self._snapshot

    def close(self) -> None:
        """Close this set once; the manager must first remove and drain borrowed calls."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._cleanup.close()
