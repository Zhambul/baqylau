# Copyright (c) 2026 Zhambyl Yermagambet
"""Select the enabled packages that declare a projector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.contracts import projection
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.scope_relations import ScopedSettings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from extensions.registry_package import RegistryPackage


@dataclass(frozen=True)
class ProjectorPackage:
    """Keep one enabled package's projector and processing identity."""

    extension_id: str
    runtime_revision: str
    settings: ScopedSettings
    manifest: ExtensionManifest
    schemas: SchemaSet
    projector: projection.ExtensionProjector


def projector_packages(registry_packages: Sequence[RegistryPackage]) -> tuple[ProjectorPackage, ...]:
    """Select the enabled packages which declare a projector.

    Returns:
        The projector packages in their active order.

    """
    selected: list[ProjectorPackage] = []
    for package in registry_packages:
        plugin = package.plugin
        if plugin is None or plugin.capabilities.projector is None:
            continue
        selected.append(ProjectorPackage(
            extension_id=package.manifest.extension_id,
            runtime_revision="" if package.environment is None else package.environment.runtime_revision,
            settings=package.resolved_settings,
            manifest=package.manifest,
            schemas=SchemaSet(package.manifest.schemas),
            projector=plugin.capabilities.projector,
        ))
    return tuple(selected)
