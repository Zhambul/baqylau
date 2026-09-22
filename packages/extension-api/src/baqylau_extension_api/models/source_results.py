# Copyright (c) 2026 Zhambyl Yermagambet
"""Return source plans, exact read results, and release acknowledgments."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, NonemptyText, WireModel
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.observations import MAX_OBSERVATIONS, ObservationCandidate
from baqylau_extension_api.models.sources import MAX_SOURCES, SourceBinding, SourceDeadline, SourceDescriptor


class SourcePlan(WireModel):
    """Replace one scope's complete source plan after host validation."""

    binding: SourceBinding
    sources: Annotated[tuple[SourceDescriptor, ...], Field(max_length=MAX_SOURCES)] = ()


class SourceReadBinding(WireModel):
    """Repeat the exact source and committed position selected for a read."""

    call: SourceBinding
    source_identity: Identifier
    source_type: Identifier
    after_position: NonemptyText | None


class PositionedObservation(WireModel):
    """Link one proposed original observation to its next resume position."""

    position: NonemptyText
    observation: ObservationCandidate


class SourceBatch(WireModel):
    """Propose observations and progress for one atomic host write.

    Empty batches can advance an explicit checkpoint. A batch with more work
    must advance. Positions are opaque; the host does not sort their text.
    """

    status: Literal["ready"] = "ready"
    binding: SourceReadBinding
    observations: Annotated[tuple[PositionedObservation, ...], Field(max_length=MAX_OBSERVATIONS)] = ()
    next_position: NonemptyText | None
    has_more: bool = False
    next_due_at: SourceDeadline | None = None


class SourceReadFailed(WireModel):
    """Report a failed read without committing a new source position."""

    status: Literal["failed"] = "failed"
    binding: SourceReadBinding
    diagnostic: Diagnostic


SourceReadResult = Annotated[SourceBatch | SourceReadFailed, Field(discriminator="status")]


class SourceReleaseResult(WireModel):
    """Distinguish a complete source release from pending cleanup."""

    binding: SourceBinding
    source_identity: Identifier | None
    status: Literal["released", "pending"]
    diagnostic: Diagnostic | None = None
