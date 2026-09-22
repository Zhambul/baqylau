# Copyright (c) 2026 Zhambyl Yermagambet
"""Declare schema-based settings without storing user choices or secrets."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.manifest.data import ScopeKinds
from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.documents import EncodedDocument


class SecretSetting(WireModel):
    """Name a credential reference, never a default secret value."""

    name: Identifier
    required: bool = False


class SettingsDefinition(WireModel):
    """Use the default document's schema for settings validation and migration."""

    defaults: EncodedDocument
    scopes: ScopeKinds
    secret_references: Annotated[tuple[SecretSetting, ...], Field(max_length=100)] = ()
