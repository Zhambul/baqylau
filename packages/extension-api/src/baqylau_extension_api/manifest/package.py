# Copyright (c) 2026 Zhambyl Yermagambet
"""Define the data-only extension.json document."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.data import CapabilityName
from baqylau_extension_api.manifest.metadata import BackendEntry, E2eCase, LoadOrder, PackageAsset, PackageDependency
from baqylau_extension_api.manifest.migrations import DeclaredMigrationPath
from baqylau_extension_api.manifest.settings import SettingsDefinition
from baqylau_extension_api.models.base import ExtensionId, NonemptyText, WireModel
from baqylau_extension_api.models.documents import SchemaDefinition
from baqylau_extension_api.versions import PackageVersion, VersionRange

MAX_CAPABILITIES = 12
MAX_ASSETS = 2000


class ExtensionManifest(WireModel):
    """Describe an external package; full registration also checks cross-references."""

    manifest_version: Literal[1] = 1
    extension_id: ExtensionId
    name: NonemptyText
    description: NonemptyText
    package_version: PackageVersion
    api_requires: VersionRange
    quality_policy: PackageVersion
    backend: BackendEntry | None = None
    capabilities: Annotated[tuple[CapabilityName, ...], Field(max_length=MAX_CAPABILITIES)] = ()
    schemas: Annotated[tuple[SchemaDefinition, ...], Field(max_length=1000)] = ()
    contributions: Contributions = Contributions()
    dependencies: Annotated[tuple[PackageDependency, ...], Field(max_length=100)] = ()
    load_order: LoadOrder = LoadOrder()
    settings: SettingsDefinition | None = None
    migration_paths: Annotated[tuple[DeclaredMigrationPath, ...], Field(max_length=1000)] = ()
    assets: Annotated[tuple[PackageAsset, ...], Field(max_length=MAX_ASSETS)] = ()
    e2e: Annotated[tuple[E2eCase, ...], Field(min_length=1, max_length=1000)]
