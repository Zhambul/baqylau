# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish typed extension projection feed bodies."""

from typing import Literal

from baqylau_extension_api.core.base import CoreModel
from baqylau_extension_api.core.entry_base import CoreEntryBodyModel
from baqylau_extension_api.models.base import OpaqueId


class ExtensionSchemaRefBody(CoreModel):
    """Identify the feature-owned schema of one derived document."""

    owner: str
    name: str
    version: int
    digest: str


class ExtensionBody(CoreEntryBodyModel):
    """Record one feature-owned derived entry without its stored fact."""

    kind: Literal["extension"] = "extension"
    owner: str
    entry_type: str
    source_event_id: OpaqueId
    schema_ref: ExtensionSchemaRefBody
    document: str
