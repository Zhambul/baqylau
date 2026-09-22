# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate worker load requests before importing extension feature code."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.manifest.data import CapabilityName
from baqylau_extension_api.manifest.package import MAX_CAPABILITIES, ExtensionManifest
from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.documents import SchemaDefinition
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.lifecycle import ExtensionInfo


class WorkerLoadRequest(WireModel):
    """Supply installed metadata and schemas to the isolated worker."""

    manifest: ExtensionManifest
    environment: ExtensionEnvironment
    peer_schemas: Annotated[tuple[SchemaDefinition, ...], Field(max_length=1000)] = ()


class WorkerReady(WireModel):
    """Report the verified package and actual supported capabilities."""

    extension_info: ExtensionInfo
    capabilities: Annotated[tuple[CapabilityName, ...], Field(max_length=MAX_CAPABILITIES)]
