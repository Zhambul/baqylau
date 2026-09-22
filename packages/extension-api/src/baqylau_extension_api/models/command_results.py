# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep proven command outcomes separate from uncertain external effects."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.commands import CommandBinding
from baqylau_extension_api.models.documents import ContentReference, Diagnostic, EncodedDocument
from baqylau_extension_api.models.observations import MAX_OBSERVATIONS, ObservationCandidate
from baqylau_extension_api.models.queries import MAX_RESULT_CONTENT


class CommandOutcome(WireModel):
    """Carry recorded evidence for an attempt, without allocating host cursors."""

    binding: CommandBinding
    observations: Annotated[tuple[ObservationCandidate, ...], Field(max_length=MAX_OBSERVATIONS)] = ()
    content: Annotated[tuple[ContentReference, ...], Field(max_length=MAX_RESULT_CONTENT)] = ()


class CommandSucceeded(CommandOutcome):
    """Report a proven result whose document matches the command declaration."""

    status: Literal["succeeded"] = "succeeded"
    document: EncodedDocument


class CommandFailed(CommandOutcome):
    """Report a known failed operation, not an uncertain write outcome."""

    status: Literal["failed"] = "failed"
    diagnostic: Diagnostic


class CommandCanceled(CommandOutcome):
    """Confirm stopped work after checking any possible external effect."""

    status: Literal["canceled"] = "canceled"
    diagnostic: Diagnostic | None = None


class CommandOutcomeUnknown(CommandOutcome):
    """Keep an uncertain external result explicit until it can be reconciled."""

    status: Literal["outcome_unknown"] = "outcome_unknown"
    diagnostic: Diagnostic
    receipt: EncodedDocument | None = None


CommandResult = Annotated[
    CommandSucceeded | CommandFailed | CommandCanceled | CommandOutcomeUnknown,
    Field(discriminator="status"),
]
