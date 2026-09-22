# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep transform results and their applied, failed, or rejected outcomes."""

from typing import Annotated, Literal, Self

from baqylau_extension_api.models import base, documents, transforms
from pydantic import Field, model_validator

from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_bodies import BodyRef
from extensions.models.interpretation_storage_operations import StoredInsert, StoredOperation


class StoredTransformResult(base.WireModel):
    """Return one extension's operations with referenced documents."""

    operations: Annotated[
        tuple[StoredOperation, ...],
        Field(max_length=transforms.MAX_TRANSFORM_OUTPUTS),
    ] = ()
    diagnostics: Annotated[tuple[documents.Diagnostic, ...], Field(max_length=100)] = ()

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
            if not isinstance(operation, StoredInsert)
        )
        additions = tuple(
            (operation.input_id, operation.output_key)
            for operation in self.operations if isinstance(operation, StoredInsert)
        )
        if len(set(decisions)) != len(decisions):
            message = "only one keep, drop, or replace decision is allowed per input"
            raise ValueError(message)
        if len(set(additions)) != len(additions):
            message = "insertion output keys must be unique per input"
            raise ValueError(message)
        return self


class StoredRawTransformResult(StoredTransformResult):
    """Supply changed bytes through one referenced content snapshot."""

    content_snapshot: BodyRef


class StoredAppliedStep[Reply](base.WireModel):
    """Retain a checked reply that the host applied."""

    kind: Literal["applied"] = "applied"
    reply: Reply


class StoredFailedStep[Reply](base.WireModel):
    """Keep the input unchanged and retain a bounded failure."""

    kind: Literal["failed"] = "failed"
    diagnostic: documents.Diagnostic
    reply: Reply | None = None


type StoredStepOutcome[Reply] = Annotated[
    StoredAppliedStep[Reply] | StoredFailedStep[Reply] | steps.RejectedStep,
    Field(discriminator="kind"),
]


def _required_reply[Reply](reply: Reply | None) -> Reply:
    if reply is None:
        message = "an applied step requires its stored reply"
        raise ValueError(message)
    return reply
