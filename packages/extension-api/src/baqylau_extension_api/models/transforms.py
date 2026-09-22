# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe ordered transform operations without mutable event objects."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from baqylau_extension_api.models.base import Identifier, NonemptyText, OpaqueId, WireModel
from baqylau_extension_api.models.canonical import CanonicalFact
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.transform_requests import (
    CanonicalTransformRequest as CanonicalTransformRequest,
    RawTransformRequest as RawTransformRequest,
    TransformRequest as TransformRequest,
)

MAX_TRANSFORM_OUTPUTS = 1000


class Keep(WireModel):
    """Retain a named input at its original position."""

    kind: Literal["keep"] = "keep"
    input_id: OpaqueId


class Drop(WireModel):
    """Suppress a named input with a recorded reason."""

    kind: Literal["drop"] = "drop"
    input_id: OpaqueId
    reason: NonemptyText


class Replace[Document](WireModel):
    """Replace input data while preserving its identity and scope."""

    kind: Literal["replace"] = "replace"
    input_id: OpaqueId
    document: Document


class Insert[Document](WireModel):
    """Add one stable output relative to a named input."""

    kind: Literal["insert"] = "insert"
    input_id: OpaqueId
    output_key: Identifier
    position: Literal["before", "after"]
    document: Document


type TransformOperation[Document] = Annotated[
    Keep | Drop | Replace[Document] | Insert[Document], Field(discriminator="kind"),
]
type TransformOperations[Document] = tuple[TransformOperation[Document], ...]


class TransformResult[Document](WireModel):
    """Return one extension's operations for atomic validation."""

    operations: Annotated[
        TransformOperations[Document],
        Field(max_length=MAX_TRANSFORM_OUTPUTS),
    ] = ()
    diagnostics: Annotated[tuple[Diagnostic, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def validate_operations(self) -> Self:
        """Require one base decision and unique additions for each input.

        Returns:
            The checked operation batch.

        Raises:
            ValueError: If decisions conflict or insertion keys repeat.

        """
        decisions = tuple(
            operation.input_id for operation in self.operations
            if not isinstance(operation, Insert)
        )
        additions = tuple(
            (operation.input_id, operation.output_key)
            for operation in self.operations if isinstance(operation, Insert)
        )
        if len(set(decisions)) != len(decisions):
            message = "only one keep, drop, or replace decision is allowed per input"
            raise ValueError(message)
        if len(set(additions)) != len(additions):
            message = "insertion output keys must be unique per input"
            raise ValueError(message)
        return self


CanonicalTransformResult = TransformResult[CanonicalFact]
