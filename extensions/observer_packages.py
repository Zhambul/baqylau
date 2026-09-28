# Copyright (c) 2026 Zhambyl Yermagambet
"""Select the enabled observer packages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.contracts.observers import ExtensionObserver
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.scope_relations import ScopedSettings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from extensions.registry_package import RegistryPackage


class ObserverNotFoundError(LookupError):
    """Reject observer work for a package that has no active observer."""


@dataclass(frozen=True)
class ObserverPackage:
    """Keep one enabled package's observer and processing identity."""

    extension_id: str
    runtime_revision: str
    settings: ScopedSettings
    manifest: ExtensionManifest
    schemas: SchemaSet
    observer: ExtensionObserver


def observer_packages(registry_packages: Sequence[RegistryPackage]) -> tuple[ObserverPackage, ...]:
    """Select the enabled packages which declare an observer.

    Returns:
        The observer packages in their active order.

    """
    selected: list[ObserverPackage] = []
    for package in registry_packages:
        plugin = package.plugin
        if plugin is None or plugin.capabilities.observer is None:
            continue
        selected.append(ObserverPackage(
            extension_id=package.manifest.extension_id,
            runtime_revision="" if package.environment is None else package.environment.runtime_revision,
            settings=package.resolved_settings,
            manifest=package.manifest,
            schemas=SchemaSet(package.manifest.schemas),
            observer=plugin.capabilities.observer,
        ))
    return tuple(selected)


def observer_package(registry_packages: Sequence[RegistryPackage], extension_id: str) -> ObserverPackage:
    """Select the active observer of one owner.

    Returns:
        The owner's observer package.

    Raises:
        ObserverNotFoundError: If the owner has no active observer.

    """
    for package in observer_packages(registry_packages):
        if package.extension_id == extension_id:
            return package
    message = "extension observer not found"
    raise ObserverNotFoundError(message)
