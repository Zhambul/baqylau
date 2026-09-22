# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep original extension observations separate from derived translation input."""

from dataclasses import dataclass
from typing import Annotated, Self

from baqylau_extension_api.models import base, documents, observations, scopes, source_results
from baqylau_extension_api.sources.batches import MAX_SOURCE_BATCH_BYTES
from pydantic import Field, model_validator

from domain.ids import RawEventId
from harness.models.raw_events import RawEvent


class ObservationAppend(base.WireModel):
    """Append one owner's original input under a selected committed runtime.

    This is an observation write, not a source checkpoint or a command result.
    A source coordinator must commit its progress in the same transaction.
    """

    extension_id: base.ExtensionId
    manager_id: base.Identifier
    runtime_revision: base.Identifier
    scope: scopes.ExtensionScope
    observed_at: float
    observations: Annotated[
        tuple[source_results.PositionedObservation, ...], Field(max_length=observations.MAX_OBSERVATIONS),
    ]

    @model_validator(mode="after")
    def validate_size(self) -> Self:
        """Bound the complete append before any database write.

        Returns:
            The checked append request.

        Raises:
            ValueError: If the request exceeds the source batch byte limit.

        """
        if len(self.model_dump_json().encode("utf-8")) > MAX_SOURCE_BATCH_BYTES:
            message = "observation append exceeds its encoded size limit"
            raise ValueError(message)
        return self


class ExtensionObservationMetadata(base.WireModel):
    """Store typed metadata once, with original content kept in the raw byte column."""

    observation_key: base.Identifier
    scope: scopes.ExtensionScope
    schema_ref: documents.SchemaRef
    occurred_at: float | None
    causes: tuple[base.OpaqueId, ...]
    runtime_revision: base.Identifier


class ExtensionObservation(base.WireModel):
    """Read one original extension observation without a fake harness or session."""

    raw_event_id: Annotated[RawEventId, Field(min_length=1, max_length=base.MAX_TEXT_LENGTH)]
    observed_at: float
    runtime_revision: base.Identifier
    source_position: base.NonemptyText
    candidate: observations.ObservationCandidate


@dataclass(frozen=True)
class StoredObservation:
    """Keep one host-owned arrival cursor beside either strict input branch."""

    cursor: int
    observation: RawEvent | ExtensionObservation


@dataclass(frozen=True)
class ObservationAppendOutcome:
    """Return the original stored rows for new and repeated observations."""

    accepted: tuple[StoredObservation, ...]
    repeated: tuple[StoredObservation, ...]
