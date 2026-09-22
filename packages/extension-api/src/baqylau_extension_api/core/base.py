# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate public core data and support the host attribute mapper."""

from pydantic import ConfigDict

from baqylau_extension_api.models.base import WireModel


class CoreModel(WireModel):
    """Read declared attributes when mapping an existing private model."""

    model_config = ConfigDict(from_attributes=True)


class CorePayloadModel(CoreModel):
    """Require each core payload to declare its closed event type."""

    kind: str
