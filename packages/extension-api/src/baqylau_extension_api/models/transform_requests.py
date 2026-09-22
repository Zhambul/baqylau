# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate the immutable input boundary for each transform stage."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.canonical import CanonicalFact, CoreStateSnapshot
from baqylau_extension_api.models.content import ContentBundle
from baqylau_extension_api.models.events import ProcessingContext, RawInput

MAX_TRANSFORM_INPUTS = 1000


class TransformRequest[Document](WireModel):
    """Provide a fixed processing context and its ordered inputs."""

    context: ProcessingContext
    inputs: Annotated[tuple[Document, ...], Field(max_length=MAX_TRANSFORM_INPUTS)]


class RawTransformRequest(TransformRequest[RawInput]):
    """Require a single scope and unique input identities in one raw batch."""

    content_snapshot: ContentBundle = ContentBundle()

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        """Validate raw identities before a worker receives the batch.

        Returns:
            The checked request.

        Raises:
            ValueError: If input identities repeat or scopes differ.

        """
        identities = {source.input_id for source in self.inputs}
        if len(identities) != len(self.inputs):
            message = "raw input identities must be unique in a batch"
            raise ValueError(message)
        if any(source.scope != self.context.scope for source in self.inputs):
            message = "raw inputs must have the processing scope"
            raise ValueError(message)
        for source in self.inputs:
            self.content_snapshot.resolve(source.content)
        return self


class CanonicalTransformRequest(TransformRequest[CanonicalFact]):
    """Supply core and extension candidates with their prior fact boundary."""

    prior_state: CoreStateSnapshot

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        """Validate canonical identities before a worker receives the batch.

        Returns:
            The checked request.

        Raises:
            ValueError: If identities repeat or inputs escape the request scope.

        """
        identities = {fact.event_id for fact in self.inputs}
        if len(identities) != len(self.inputs):
            message = "canonical input identities must be unique in a batch"
            raise ValueError(message)
        if any(fact.scope != self.context.scope for fact in self.inputs):
            message = "canonical inputs must have the processing scope"
            raise ValueError(message)
        foreign_prior = any(
            stored.fact.scope != self.context.scope
            for stored in self.prior_state.facts
        )
        if foreign_prior:
            message = "prior canonical facts must have the processing scope"
            raise ValueError(message)
        return self
