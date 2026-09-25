# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the consumed peer services that a runtime change removes, for the consumers' removal notices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.lifecycle import RemovedService

if TYPE_CHECKING:
    from collections.abc import Iterable

    from baqylau_extension_api.manifest.package import ExtensionManifest

    from extensions.registry_contract import ExtensionRegistry


def retired_services(
    published: Iterable[ExtensionManifest], planned: Iterable[ExtensionManifest],
) -> tuple[RemovedService, ...]:
    """Name the services that the published runtime provides and the planned runtime does not.

    Returns:
        The retired services, in published order.

    """
    kept = tuple(service for manifest in planned for service in _provided(manifest))
    return tuple(service for manifest in published for service in _provided(manifest) if service not in kept)


def published_retirements(
    registry: ExtensionRegistry, planned: Iterable[ExtensionManifest],
) -> tuple[RemovedService, ...]:
    """Compare the services of the published runtime with the planned runtime.

    Returns:
        The services that the change removes.

    """
    with registry.read_snapshot() as read:
        packages = read.snapshot.packages
    published = (package.manifest for package in packages if package.entry.state == "enabled")
    return retired_services(published, planned)


def removed_for(manifest: ExtensionManifest, retired: tuple[RemovedService, ...]) -> tuple[RemovedService, ...]:
    """Select the retired services that one package consumes.

    Returns:
        The package's removal notice.

    """
    consumed = tuple(
        RemovedService(owner=requirement.owner, name=requirement.name)
        for requirement in manifest.contributions.consumes
    )
    return tuple(service for service in retired if service in consumed)


def _provided(manifest: ExtensionManifest) -> tuple[RemovedService, ...]:
    return tuple(
        RemovedService(owner=manifest.extension_id, name=service.name)
        for service in manifest.contributions.services
    )
