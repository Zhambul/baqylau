# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain bounded failed-start evidence without putting feature log text in errors."""

from baqylau_extension_api.runtime.models import ExtensionTransportError

from extensions.models.workers import WorkerDiagnostics


class WorkerStartError(ExtensionTransportError):
    """A worker did not become ready and its resources have been released."""

    def __init__(self, diagnostics: WorkerDiagnostics) -> None:
        """Keep structured evidence separate from the public exception message."""
        self.diagnostics = diagnostics
        super().__init__("extension worker could not be prepared")
