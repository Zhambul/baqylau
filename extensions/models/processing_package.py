# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep retained declarations and effective settings independent of worker objects."""

from dataclasses import dataclass

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.lifecycle_selection import RuntimePackageSelection


@dataclass(frozen=True)
class ProcessingPackage:
    """Use one selected package, its retained manifest, and checked schemas."""

    selection: RuntimePackageSelection
    manifest: ExtensionManifest
    schemas: SchemaSet
