# Copyright (c) 2026 Zhambyl Yermagambet
"""Own checked worker processes without exposing their transport to engine consumers."""

from typing import Protocol

from baqylau_extension_api.contracts.plugin import ExtensionPlugin
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest

from extensions.models.workers import WorkerDiagnostics


class ExtensionWorker(Protocol):
    """Own one process, typed plugin, and private dependency environment."""

    @property
    def plugin(self) -> ExtensionPlugin:
        """The verified process-backed plugin, never a local feature object."""
        ...

    def diagnostics(self) -> WorkerDiagnostics:
        """Read bounded process evidence without invoking feature code."""
        ...

    def close(self) -> None:
        """Reject calls, stop the process group, and release owned environment resources."""
        ...


class ExtensionWorkers(Protocol):
    """Prepare one worker without activating it in the daemon."""

    def prepare_worker(self, request: WorkerLoadRequest, services: ExtensionHostServices) -> ExtensionWorker:
        """Check the exact package selection and prepare a typed, owned worker."""
        ...
