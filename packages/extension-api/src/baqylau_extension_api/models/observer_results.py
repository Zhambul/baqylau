# Copyright (c) 2026 Zhambyl Yermagambet
"""Return original observations and explicit proof of the job outcome."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.documents import ContentReference, Diagnostic, EncodedDocument
from baqylau_extension_api.models.observations import MAX_OBSERVATIONS, ObservationCandidate
from baqylau_extension_api.models.observer_jobs import ObservationJobBinding
from baqylau_extension_api.models.queries import MAX_RESULT_CONTENT


class ObservationOutcome(WireModel):
    """Propose recorded evidence without assigning host cursors or job state."""

    binding: ObservationJobBinding
    observations: Annotated[tuple[ObservationCandidate, ...], Field(max_length=MAX_OBSERVATIONS)] = ()
    content: Annotated[tuple[ContentReference, ...], Field(max_length=MAX_RESULT_CONTENT)] = ()


class ObservationSucceeded(ObservationOutcome):
    """Report completed work, which can have no new observations."""

    status: Literal["succeeded"] = "succeeded"


class ObservationFailed(ObservationOutcome):
    """Report a known failure, not a write whose result is unknown."""

    status: Literal["failed"] = "failed"
    diagnostic: Diagnostic


class ObservationCanceled(ObservationOutcome):
    """Report stopped work after checking possible external effects."""

    status: Literal["canceled"] = "canceled"
    diagnostic: Diagnostic | None = None


class ObservationOutcomeUnknown(ObservationOutcome):
    """Keep a lost external outcome explicit until evidence can resolve it."""

    status: Literal["outcome_unknown"] = "outcome_unknown"
    diagnostic: Diagnostic
    receipt: EncodedDocument | None = None


type ObservationJobResult = Annotated[
    ObservationSucceeded | ObservationFailed | ObservationCanceled | ObservationOutcomeUnknown,
    Field(discriminator="status"),
]
