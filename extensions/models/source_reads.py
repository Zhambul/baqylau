# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind original source output and its checkpoint to one atomic host proposal."""

from dataclasses import dataclass
from typing import Self

from baqylau_extension_api.models import base, scopes, source_results, sources
from baqylau_extension_api.sources.batches import validate_source_batch
from pydantic import model_validator

from extensions.models.observations import ObservationAppendOutcome

MAX_SOURCE_PROPOSAL_BYTES = 8_388_608


class SourceKey(base.WireModel):
    """Name one source without tying its resume position to a runtime generation."""

    extension_id: base.ExtensionId
    scope: scopes.ExtensionScope
    source_identity: base.Identifier


class SourceCheckpoint(base.WireModel):
    """Use a host revision to reject stale reads even if a position repeats."""

    key: SourceKey
    revision: base.Revision = 0
    source_type: base.Identifier | None = None
    position: base.NonemptyText | None = None

    @model_validator(mode="after")
    def require_complete_progress(self) -> Self:
        """Keep initial absence distinct from a committed source position.

        Returns:
            An explicit initial or complete committed checkpoint.

        Raises:
            ValueError: If type, position, and revision disagree.

        """
        progress = (self.source_type, self.position)
        if self.revision == 0 and progress != (None, None):
            message = "initial source checkpoint must not claim committed progress"
            raise ValueError(message)
        if self.revision > 0 and None in progress:
            message = "committed source checkpoint requires its type and position"
            raise ValueError(message)
        return self


class SourceReadProposal(base.WireModel):
    """Retain the complete request and successful reply before any progress is accepted."""

    manager_id: base.Identifier
    checkpoint: SourceCheckpoint
    request: sources.SourceReadRequest
    response: source_results.SourceBatch

    @model_validator(mode="after")
    def require_selected_source(self) -> Self:
        """Check source identity, exact progress, complete output, and encoded size.

        Returns:
            The complete proposal; schema and runtime authority still need storage checks.

        Raises:
            ValueError: If source selection, type, position, or bounds differ.

        """
        expected_key = source_key(self.request)
        if expected_key != self.checkpoint.key or self.request.after_position != self.checkpoint.position:
            message = "source request differs from its captured checkpoint"
            raise ValueError(message)
        permitted_types = {None, self.request.source.source_type}
        if self.checkpoint.source_type not in permitted_types:
            message = "source identity cannot change its committed source type"
            raise ValueError(message)
        validate_source_batch(self.request, self.response)
        encoded = self.model_dump_json().encode("utf-8")
        if len(encoded) > MAX_SOURCE_PROPOSAL_BYTES:
            message = "source proposal exceeds its encoded size limit"
            raise ValueError(message)
        return self


class SourceReadCommit(base.WireModel):
    """Keep host observation time separate from the stable read identity."""

    proposal: SourceReadProposal
    observed_at: float


@dataclass(frozen=True)
class SourceReadOutcome:
    """Return original rows and the checkpoint accepted for this exact call."""

    checkpoint: SourceCheckpoint
    observations: ObservationAppendOutcome
    repeated: bool = False


def source_key(request: sources.SourceReadRequest) -> SourceKey:
    """Derive the source key from the host-selected request.

    Returns:
        The owner, complete scope, and stable source identity.

    """
    binding = request.context.binding
    return SourceKey(
        extension_id=binding.extension_id, scope=binding.scope, source_identity=request.source.source_identity,
    )


def next_checkpoint(proposal: SourceReadProposal) -> SourceCheckpoint:
    """Advance only when the reply changes the opaque resume position.

    Returns:
        The exact post-commit checkpoint, including unchanged empty reads.

    """
    if proposal.response.next_position == proposal.checkpoint.position:
        return proposal.checkpoint
    return SourceCheckpoint(
        key=proposal.checkpoint.key, revision=proposal.checkpoint.revision + 1,
        source_type=proposal.request.source.source_type, position=proposal.response.next_position,
    )
