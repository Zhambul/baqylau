# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish secret reference states and write-only secret values."""

from typing import Annotated

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.credentials import MAX_SECRET_LENGTH
from pydantic import Field


class SecretStatusResponse(WireModel):
    """Tell whether one declared reference has a stored value; never the value."""

    name: Identifier
    required: bool
    configured: bool


class ExtensionSecretsResponse(WireModel):
    """Return every declared reference state and the current write policy."""

    secrets: tuple[SecretStatusResponse, ...]
    read_only: bool


class SecretWriteRequest(WireModel):
    """Carry one new secret value; no response contains it."""

    secret: Annotated[str, Field(min_length=1, max_length=MAX_SECRET_LENGTH, repr=False)]
