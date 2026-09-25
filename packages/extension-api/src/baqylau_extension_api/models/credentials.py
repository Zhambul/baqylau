# Copyright (c) 2026 Zhambyl Yermagambet
"""Ask the host for one declared secret without placing it in settings or snapshots."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, WireModel

MAX_SECRET_LENGTH = 65_536


class SecretRequest(WireModel):
    """Name one secret reference that the package manifest declares."""

    name: Identifier


class SecretAvailable(WireModel):
    """Return the stored value of one secret reference to the live worker lane."""

    status: Literal["available"] = "available"
    name: Identifier
    secret: Annotated[str, Field(min_length=1, max_length=MAX_SECRET_LENGTH, repr=False)]


class SecretMissing(WireModel):
    """Report that the user has not stored a value for the reference."""

    status: Literal["missing"] = "missing"
    name: Identifier


type SecretResult = Annotated[SecretAvailable | SecretMissing, Field(discriminator="status")]
