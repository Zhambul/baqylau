# Copyright (c) 2026 Zhambyl Yermagambet
"""Report new originals and diagnostics to the host."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from baqylau_extension_api.models.reporting import (
        DiagnosticRecord,
        DiagnosticRecorded,
        ObservationsSubmitted,
        ObservationSubmission,
    )


class ExtensionObservationSink(Protocol):
    """Append new originals of the calling package; interpretation reads them like source input."""

    def submit_observations(self, observation_submission: ObservationSubmission) -> ObservationsSubmitted:
        """Store the new originals and count the repeated ones."""
        ...


class ExtensionAuditService(Protocol):
    """Record typed diagnostics in the host's operational audit."""

    def record_diagnostic(self, diagnostic_record: DiagnosticRecord) -> DiagnosticRecorded:
        """Store one diagnostic with its context."""
        ...
