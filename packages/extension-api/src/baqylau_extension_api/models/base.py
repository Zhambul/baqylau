# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate immutable extension wire documents."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MAX_IDENTIFIER_LENGTH = 200
MAX_TEXT_LENGTH = 4096

Identifier = Annotated[
    str,
    Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
ExtensionId = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NonemptyText = Annotated[str, Field(min_length=1, max_length=MAX_TEXT_LENGTH)]
OpaqueId = NonemptyText
Revision = Annotated[int, Field(ge=0)]


class WireModel(BaseModel):
    """Reject unknown fields and invalid values at the wire boundary."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )
