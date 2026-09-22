# Copyright (c) 2026 Zhambyl Yermagambet
"""Release a worker before its loop and private environment are removed."""

from contextlib import ExitStack
from threading import Lock

from baqylau_extension_api.contracts.plugin import ExtensionPlugin

from extensions.impl.process.runtime import RunningWorker
from extensions.models.workers import WorkerDiagnostics
from extensions.worker_contract import ExtensionWorker


class ManagedExtensionWorker(ExtensionWorker):
    """Own the cleanup stack while exposing only the public plugin and bounded evidence."""

    def __init__(self, running: RunningWorker, cleanup: ExitStack) -> None:
        """Take ownership only after the worker has returned a valid ready reply."""
        self._running = running
        self._cleanup = cleanup
        self._lock = Lock()

    @property
    def plugin(self) -> ExtensionPlugin:
        """The verified process-backed plugin."""
        return self._running.plugin

    def diagnostics(self) -> WorkerDiagnostics:
        """Read bounded process output before or after close.

        Returns:
            The process evidence retained by this owner.

        """
        return self._running.diagnostics()

    def close(self) -> None:
        """Serialize repeated close requests and release resources in ownership order."""
        with self._lock:
            self._cleanup.close()
