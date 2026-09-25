# Copyright (c) 2026 Zhambyl Yermagambet
"""Submit new original observations and record typed diagnostics through the host."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.observations import MAX_OBSERVATIONS
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.models.source_results import PositionedObservation


class ObservationSubmission(WireModel):
    """Propose new originals in one scope; each names its causes and a position text."""

    scope: ExtensionScope
    observations: Annotated[tuple[PositionedObservation, ...], Field(min_length=1, max_length=MAX_OBSERVATIONS)]


class ObservationsSubmitted(WireModel):
    """Count the new and the already stored originals of one submission."""

    accepted: int
    repeated: int


class DiagnosticRecord(WireModel):
    """Keep one typed diagnostic with its optional scope, job, and event context."""

    diagnostic: Diagnostic
    scope: ExtensionScope | None = None
    job_id: Identifier | None = None
    event_id: Identifier | None = None


class DiagnosticRecorded(WireModel):
    """Confirm that the host stored the diagnostic."""
